import html
import json
import math
from pathlib import Path
from threading import Lock
from urllib.parse import quote

import gradio as gr

from ai import AIClient
from config import GRADIO_AUTH_PASSWORD, GRADIO_AUTH_USER, PROMPTS,SERVER,SERVER_PORT
from downloader import DownloadManager, import_video, parse_targets
from storage import Store
from subtitles import Transcriber

store = Store()
downloads = DownloadManager(store)
transcriber = Transcriber()
ai = AIClient()
subtitle_lock = Lock()
GRADIO_6 = int(gr.__version__.split(".")[0]) >= 6



def message(error: Exception) -> str:
    return f"操作失败：{error}"


def configured_auth() -> tuple[str, str] | None:
    username = GRADIO_AUTH_USER.strip() if isinstance(GRADIO_AUTH_USER, str) else ""
    password = GRADIO_AUTH_PASSWORD
    if not password:
        return None
    if not username or not isinstance(password, str):
        raise ValueError("GRADIO_AUTH_USER 和 GRADIO_AUTH_PASSWORD 必须是非空字符串")
    return username, password


PAGE_SIZE = 10
MAX_PAGE_SIZE = 80
PAGE_SIZES = {10, 20, 40, 80}
LIBRARY_OUTPUT_COUNT = 2 + MAX_PAGE_SIZE * 4


def library(page: int = 1, page_size: int = PAGE_SIZE):
    try:
        page_size = int(page_size)
        page = int(page)
    except (TypeError, ValueError):
        page_size, page = PAGE_SIZE, 1
    if page_size not in PAGE_SIZES:
        page_size = PAGE_SIZE
    rows, total = store.list_videos(page, page_size)
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    if not rows and total:
        rows, _ = store.list_videos(page, page_size)
    updates = [page, f"{page}/{pages} 页 · 共{total}条 · 每页"]
    for index in range(MAX_PAGE_SIZE):
        if index < len(rows):
            row = rows[index]
            duration = f"{row['duration']/60:.1f} 分钟" if row["duration"] >= 60 else f"{row['duration']:.1f} 秒"
            info = f"{duration} · {row['size_bytes']/1048576:.1f} MB · 字幕 {row['subtitle_state']}"
            updates.extend((gr.update(visible=True), gr.update(value=row["title"]), gr.update(value=info), row["id"]))
        else:
            updates.extend((gr.update(visible=False), gr.update(value=""), gr.update(value=""), None))
    return tuple(updates)


def initial_library(page: int, page_size: int):
    result = library(page, page_size)
    return gr.update(value=page_size if page_size in PAGE_SIZES else PAGE_SIZE), *result


def change_page_size(page_size: int):
    return page_size, *library(1, page_size)


def download(url, manifest, allow_partial, browser, quality):
    try:
        store.set_preferred_browser(browser)
        targets = parse_targets(url or "", manifest, allow_partial, include_restricted=browser != "none")
        job_id = downloads.start(targets, browser, quality)
        return f"任务 #{job_id} 已加入队列，共 {len(targets)} 条。", jobs()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return message(error), jobs()


def upload_video(file, page_size=PAGE_SIZE):
    if not file:
        return "请选择视频文件", *library(1, page_size)
    try:
        video_id = import_video(store, file)
        return f"已导入视频 #{video_id}", *library(1, page_size)
    except (OSError, ValueError) as error:
        return message(error), *library(1, page_size)


def jobs():
    rows = store.list_downloads()
    if not rows:
        return '<div class="jobs-empty">暂无下载任务</div>'
    labels = {"queued": "排队中", "running": "下载中", "done": "已完成", "partial": "部分完成",
              "failed": "失败", "auth_required": "需要登录", "interrupted": "已中断"}
    items = []
    for row in rows:
        name = row["display_name"] or store.video_title_for_source(row["source"]) or row["source"]
        progress = min(100, round(100 * row["done_count"] / max(1, row["total_count"])))
        state = row["state"] if row["state"] in labels else "failed"
        detail = row["detail"] if state not in {"done", "queued"} else ""
        items.append(f'<article class="job-row"><div class="job-main"><strong>{html.escape(name)}</strong>'
                     f'<span class="job-badge state-{state}">{labels[state]}</span></div>'
                     f'<div class="job-bottom"><span>#{row["id"]} · {row["done_count"]}/{row["total_count"]} 条</span>'
                     f'<div class="job-progress"><span style="width:{progress}%"></span></div></div>'
                     f'{f"<p class=\"job-detail\">{html.escape(detail)}</p>" if detail else ""}</article>')
    return '<div class="job-list">' + ''.join(items) + '</div>'


def monitor(page, known_done, prompted_auth, page_size=PAGE_SIZE):
    active = store.list_downloads()
    table = jobs()
    done_signature = json.dumps([(row["id"], row["done_count"]) for row in active if row["done_count"]], separators=(",", ":"))
    library_result = library(page, page_size) if done_signature != known_done else (gr.update(),) * LIBRARY_OUTPUT_COUNT
    new_auth = next((row for row in active if row["state"] == "auth_required" and row["id"] > prompted_auth), None)
    if new_auth:
        auth_update = gr.update(visible=True)
        auth_message = f"任务 #{new_auth['id']} 未能通过浏览器登录状态下载。请确认浏览器已登录且账号有观看权限，然后重新读取并重试。{new_auth['detail']}"
        prompted_auth = new_auth["id"]
        browser_update = gr.update(value=store.preferred_browser())
    else:
        auth_update = auth_message = browser_update = gr.update()
    return table, *library_result, done_signature, prompted_auth, auth_update, auth_message, browser_update


def retry_download(job_id, url, manifest, allow_partial, browser, quality):
    failed = next((row for row in store.list_downloads() if row["id"] == job_id), None)
    if not failed:
        return "原任务已不可用，请重新提交下载", jobs(), gr.update(), gr.update(value=browser)
    if failed["source"].startswith("https://www.bilibili.com/"):
        url, manifest = failed["source"], None
    elif not manifest:
        return "请重新上传原合集清单后重试", jobs(), gr.update(), gr.update(value=browser)
    status, table = download(url, manifest, allow_partial, browser, quality)
    return status, table, gr.update(visible=not status.startswith("任务 #")), gr.update(value=browser)


def choose_video(video_id):
    return (video_id if store.get_video(video_id) else None), gr.update(selected="detail")


def request_delete(video_id):
    video = store.get_video(video_id) if video_id else None
    if not video:
        return gr.update(visible=False), None, "视频已不存在，请刷新列表"
    return gr.update(visible=True), video_id, f"确定删除《{video['title']}》及其字幕、笔记和对话？此操作不可撤销。"


def player(video_id: int | None) -> str:
    video = store.get_video(video_id) if video_id else None
    if not video:
        return '<div class="empty-detail">从视频库选择一条视频</div>'
    segments = store.get_segments(video_id)
    src = "/gradio_api/file=" + quote(video["file_path"], safe="/")
    data = html.escape(json.dumps(segments, ensure_ascii=False), quote=True)
    buttons = "".join(f'<button type="button" data-seek="{i}" title="跳转到本句"><span>{i+1:02d}</span> {html.escape(s["text"])}</button>' for i, s in enumerate(segments))
    return f'''<section id="study-player" data-video-id="{video_id}" data-segments="{data}">
      <div class="video-frame"><video controls playsinline preload="metadata" src="{src}"></video></div>
      <div class="study-controls">
        <label><input type="checkbox" class="repeat-mode"> 逐句</label>
        <label><input class="repeat-count" type="number" min="1" max="20" value="3"> 遍</label>
        <label>遍停 <input class="pause-factor" type="number" min="0" max="10" step="0.5" value="0"> 倍句长</label>
        <label>循停 <input class="end-pause-factor" type="number" min="0" max="10" step="0.5" value="0"> 倍句长</label>
        <label>速度 <select class="play-speed">{''.join(f'<option value="{s}" {"selected" if s == 1 else ""}>{s}×</option>' for s in (0.5, 0.75, 1, 1.25, 1.5, 2))}</select></label>
        <button type="button" class="next-sentence" title="跳转到下一句" {'disabled' if not segments else ''}>下一句</button>
      </div><div class="play-status" aria-live="polite"></div>
      <div class="sentence-toolbar"><span>字幕</span><button type="button" class="sentence-expand" aria-expanded="false">展开全部</button></div>
      <div class="sentence-list">{buttons or '<p>暂无字幕，仍可正常播放。字幕生成完成后将自动载入逐句学习。</p>'}</div>
    </section>'''


def detail(video_id):
    video = store.get_video(video_id) if video_id else None
    if not video:
        return "未选择视频", player(None), [], gr.update(choices=[], value=None), gr.update(visible=False), ""
    segments = store.get_segments(video_id)
    sentence_choices = [(f"{i + 1}. {s['text'][:75]}", i) for i, s in enumerate(segments)]
    state = video["subtitle_state"]
    status = ("字幕正在生成，完成后自动载入…" if state == "running" else
              f"字幕生成失败：{video['subtitle_error']}" if state == "failed" else
              "")
    return (f"### {video['title']}", player(video_id),
            store.list_chat(video_id), gr.update(choices=sentence_choices, value=0 if sentence_choices else None),
            gr.update(visible=not segments and state != "running"), status)


def save_note(video_id, note_id, content, at_seconds, revision):
    try:
        if not store.get_video(video_id):
            raise ValueError("请先选择视频")
        store.add_note(video_id, content, float(at_seconds), int(note_id) if note_id else None)
        return f"笔记已保存 · {float(at_seconds):.1f}s", revision + 1, None, ""
    except (TypeError, ValueError) as error:
        return message(error), revision, note_id, content


def choose_note(video_id, note_id):
    if not video_id or not note_id:
        return None, "", "请先选择笔记"
    row = next((item for item in store.list_notes(video_id) if item["id"] == note_id), None)
    if not row:
        return None, "", "笔记已不存在"
    return row["id"], row["content"], "正在编辑笔记"


def request_note_delete(video_id, note_id):
    row = next((item for item in store.list_notes(video_id) if item["id"] == note_id), None) if video_id else None
    if not row:
        return gr.update(visible=False), None, "笔记已不存在"
    preview = html.escape(row["content"].replace("\n", " ")[:100])
    return gr.update(visible=True), note_id, f"确定删除这条笔记？\n\n{preview}"


def remove_note(video_id, note_id, revision, editing_id):
    if not video_id or not note_id:
        return "笔记已不存在", revision, gr.update(visible=False), None, gr.update(), gr.update()
    try:
        if not store.delete_note(video_id, int(note_id)):
            raise ValueError("笔记已不存在")
        clear_edit = editing_id == note_id
        return "笔记已删除", revision + 1, gr.update(visible=False), None, None if clear_edit else gr.update(), "" if clear_edit else gr.update()
    except ValueError:
        return "笔记已不存在", revision, gr.update(visible=False), None, gr.update(), gr.update()


def remove_video(video_id, page, selected_id, page_size=PAGE_SIZE):
    if not video_id:
        return "请先选择视频", *library(page, page_size), gr.update(visible=False), None, selected_id
    try:
        store.delete_video(video_id)
        return "视频和关联记录已删除", *library(page, page_size), gr.update(visible=False), None, None if selected_id == video_id else selected_id
    except (OSError, ValueError) as error:
        return message(error), *library(page, page_size), gr.update(visible=False), None, selected_id


def submit_subtitle(video_id):
    with subtitle_lock:
        video = store.get_video(video_id) if video_id else None
        if not video:
            return "请先选择视频", gr.update(visible=False)
        if store.get_segments(video_id):
            return "字幕已就绪", gr.update(visible=False)
        if video["subtitle_state"] == "running" and video["subtitle_job_id"]:
            return "字幕正在生成，完成后自动载入…", gr.update(visible=False)
        try:
            job_id = transcriber.submit(Path(video["file_path"]))
            store.set_subtitle_state(video_id, "running", job_id)
            return f"字幕任务 {job_id} 已提交，正在自动等待结果…", gr.update(visible=False)
        except RuntimeError as error:
            store.set_subtitle_state(video_id, "failed", error=str(error))
            return message(error), gr.update(visible=True)


def poll_subtitle(video_id):
    unchanged = (gr.update(),) * 6
    video = store.get_video(video_id) if video_id else None
    if not video or video["subtitle_state"] != "running":
        return unchanged
    if not subtitle_lock.acquire(blocking=False):
        return unchanged
    try:
        if not video["subtitle_job_id"]:
            store.set_subtitle_state(video_id, "failed", error="字幕任务缺少 ID")
            return detail(video_id)
        try:
            state, info = transcriber.status(video["subtitle_job_id"])
            if state == "ready":
                store.replace_segments(video_id, transcriber.segments(video["subtitle_job_id"]))
                return detail(video_id)
            if state == "failed":
                store.set_subtitle_state(video_id, "failed", error=info)
                return detail(video_id)
            return (*unchanged[:-1], info or "字幕正在生成，完成后自动载入…")
        except RuntimeError as error:
            return (*unchanged[:-1], f"字幕服务暂时不可用，自动重试中：{error}")
    finally:
        subtitle_lock.release()


def ask(video_id, sentence_index, template, question):
    video = store.get_video(video_id) if video_id else None
    if not video:
        return [], "请先选择视频", question
    segments = store.get_segments(video_id)
    sentence = segments[int(sentence_index)]["text"] if sentence_index is not None and 0 <= int(sentence_index) < len(segments) else ""
    history = store.list_chat(video_id)
    try:
        answer = ai.ask(video["title"], sentence, question or "", template or "", history)
        prompt = f"[{template}] {sentence}\n{question or ''}".strip()
        store.add_chat(video_id, "user", prompt)
        store.add_chat(video_id, "assistant", answer)
        return store.list_chat(video_id), "已回复", ""
    except (RuntimeError, ValueError) as error:
        return history, message(error), question


CSS = """
.gradio-container {width:100% !important; max-width:1280px !important; margin:auto; padding:0 28px 48px !important; font-family:system-ui,-apple-system,sans-serif; --body-background-fill:#0e1517; --background-fill-primary:#111b1e; --background-fill-secondary:#172428; --block-background-fill:#172428; --input-background-fill:#172428; --body-text-color:#e8f2ef; --body-text-color-subdued:#a3b6b5; --border-color-primary:#304348; --button-secondary-background-fill:#203136; --button-secondary-text-color:#e8f2ef; --button-primary-background-fill:#128d82; --button-primary-text-color:#f5fffc;}
body,gradio-app,.gradio-container,.gradio-container .main {background:#0e1517 !important; color:#e8f2ef !important;}
.gradio-container [role=tablist] {border-bottom:1px solid #304348; gap:20px; background:transparent !important;}
.gradio-container [role=tablist] > [role=tab] {padding:10px 2px; font-size:14px; font-weight:600; color:#a3b6b5 !important; background:transparent !important;}
.gradio-container [role=tablist] > [role=tab].selected {color:#54d9c4 !important; border-bottom:2px solid #54d9c4;}
.gradio-container .tabitem {padding-top:24px;}
.gradio-container .html-container.padding {padding:0 !important;}
.section-head {margin-bottom:14px;}
.section-head h3 {font-size:18px; color:#e8f2ef; margin:0 0 4px;}
.form-band {padding:22px 0 20px; border-top:1px solid #304348; border-bottom:1px solid #304348; gap:28px !important;}
.form-band .form-settings {max-width:285px;}
.form-band .primary {min-height:42px;}
.gradio-container textarea[rows="1"] {overflow-y:hidden !important; resize:none !important;}
.job-list {border-top:1px solid #304348;}
.job-row {padding:15px 2px 16px; border-bottom:1px solid #304348;}
.job-main,.job-bottom {display:flex; align-items:center; justify-content:space-between; gap:16px;}
.job-main strong {font-size:14px; color:#e8f2ef; overflow-wrap:anywhere; font-weight:650;}
.job-badge {font-size:12px; font-weight:650; white-space:nowrap; color:#54d9c4;}
.state-failed,.state-auth_required,.state-interrupted {color:#ff9a7c;}
.state-partial {color:#efbd75;}
.job-bottom {margin-top:9px; color:#a3b6b5; font-size:12px;}
.job-progress {height:5px; width:33%; min-width:72px; background:#2a3b3e; border-radius:3px; overflow:hidden;}
.job-progress span {height:100%; display:block; background:#54d9c4;}
.job-detail {margin:8px 0 0; font-size:12px; color:#ff9a7c; overflow-wrap:anywhere;}
.jobs-empty {padding:24px 0; color:#a3b6b5; font-size:14px;}
.video-list-row {align-items:center; border-bottom:1px solid #304348; gap:6px !important; padding:7px 0; min-height:58px;}
.video-list-row .video-open,.video-list-row .video-meta {text-align:left; justify-content:flex-start; background:transparent !important; border:0 !important; box-shadow:none !important;}
.video-list-row .video-open {font-weight:600; color:#e8f2ef; white-space:normal; overflow-wrap:anywhere;}
.video-list-row .video-open:hover {color:#54d9c4;}
.video-list-row .video-meta {color:#a3b6b5; font-size:12px;}
.video-list-row .video-delete {max-width:48px; min-width:48px !important; background:transparent !important; border:0 !important; box-shadow:none !important; color:#ff9a7c !important; font-size:12px;}
.note-list-row {align-items:center; border-bottom:1px solid #304348; gap:6px !important; padding:5px 0;}
.note-list-row .note-time {flex:0 0 55px !important; width:55px !important; min-width:55px !important; color:#a3b6b5; font-size:12px; font-variant-numeric:tabular-nums;}
.note-list-row .note-time p {margin:0; white-space:nowrap;}
.note-list-row .note-open {min-width:0 !important; text-align:left; justify-content:flex-start; white-space:normal; overflow-wrap:anywhere; background:transparent !important; border:0 !important; box-shadow:none !important; color:#e8f2ef !important;}
.note-list-row .note-delete {flex:0 0 48px !important; width:48px !important; min-width:48px !important; max-width:48px; white-space:nowrap; background:transparent !important; border:0 !important; box-shadow:none !important; color:#ff9a7c !important; font-size:12px;}
.page-nav .form {width:86px !important; min-width:86px !important; flex:0 0 86px !important; overflow:visible !important; background:transparent !important; border:0 !important;}
.library-size {width:82px !important; max-width:82px; min-width:82px !important; flex:0 0 82px !important; margin:0; padding:3px !important;}
.page-nav {justify-content:flex-end; align-items:center; flex-wrap:nowrap !important; gap:10px !important; margin-top:18px;}
.page-nav button {max-width:76px; min-width:70px !important; background:#203136 !important; color:#e8f2ef !important; border:1px solid #375057 !important;}
.page-nav .page-count {width:fit-content !important; max-width:max-content !important; flex:0 0 auto !important; min-width:0; margin-left:0 !important; padding:0 !important; border:0 !important; background:transparent !important;}
.page-nav p {text-align:right; margin:0; color:#a3b6b5; font-size:13px; white-space:nowrap;}
.detail-heading h3 {font-size:20px; margin:0 0 3px; color:#e8f2ef;}
.video-frame {background:#11191a; aspect-ratio:16/9; max-height:70vh; display:flex; justify-content:center;}
.video-frame video {width:100%; height:100%;}
.study-controls {display:flex; align-items:center; flex-wrap:wrap; gap:10px 22px; padding:13px 0; border-bottom:1px solid #304348;}
.study-controls label {display:flex; align-items:center; gap:7px; font-size:13px; white-space:nowrap; color:#c5d7d4;}
.study-controls input[type=number] {width:55px; border:1px solid #3b555a; padding:4px; border-radius:4px; color:#e8f2ef; background:#172428;}
.study-controls select {border:1px solid #3b555a; padding:4px; border-radius:4px; color:#e8f2ef; background:#172428;}
.study-controls .next-sentence {min-height:30px; padding:4px 12px; border:1px solid #3b7772; border-radius:4px; color:#89e5d6; background:#193634; font-size:13px; font-weight:600; white-space:nowrap; cursor:pointer;}
.study-controls .next-sentence:hover:not(:disabled) {background:#245049;}
.study-controls .next-sentence:disabled {opacity:.45; cursor:not-allowed;}
.play-status {padding:9px 0; color:#54d9c4; font-weight:600; font-size:13px;}
.play-status:empty {display:none;}
.sentence-list {max-height:180px; overflow:auto; border-top:1px solid #304348; margin-bottom:22px;}
.sentence-toolbar {display:flex; justify-content:space-between; align-items:center; font-size:13px; font-weight:650; color:#c5d7d4; margin:9px 0;}
.sentence-toolbar button {border:0; background:transparent; color:#54d9c4; font-size:12px; font-weight:650; cursor:pointer;}
.sentence-list button {display:block; text-align:left; width:100%; padding:8px 4px; border-bottom:1px solid #273c40; color:#cbdad8; background:transparent; font-size:13px;}
.sentence-list button[hidden] {display:none;}
.sentence-list button.active {background:#163a39; color:#baf8e9; border-left:3px solid #54d9c4; padding-left:8px; font-weight:600;}
.sentence-list button:hover {background:#20373a;}
.sentence-list span {color:#efbd75; padding-right:9px; font-variant-numeric:tabular-nums;}
.empty-detail {padding:60px 12px; color:#a3b6b5; text-align:center; border-top:1px solid #304348;}
.study-secondary {border-top:1px solid #304348; padding-top:18px;}
#auth-dialog,#delete-dialog,#note-delete-dialog {position:fixed !important; top:50%; left:50%; transform:translate(-50%,-50%); width:min(440px,calc(100vw - 28px)); max-height:80vh; overflow:auto; z-index:10000; padding:20px; border:1px solid #3b555a; border-radius:6px; background:#172428; box-shadow:0 0 0 100vmax rgba(0,0,0,.68),0 16px 48px rgba(0,0,0,.35);}
@media(max-width:700px) {.gradio-container {padding:0 14px 30px !important;} .gradio-container main {padding:12px 14px !important;} .workspace-header {padding-top:18px;} .form-band {gap:12px !important;} .form-band .form-settings {max-width:none;} .video-frame {max-height:none;} .study-controls {gap:8px 14px;} .sentence-list {max-height:150px;} .video-list-row {flex-wrap:nowrap !important;} .video-list-row .video-meta {display:none;} .video-list-row .video-open {min-width:0 !important;} .page-nav {justify-content:center; gap:5px !important;} .page-nav button {min-width:48px !important; max-width:48px;} .page-nav p {font-size:12px;} .job-progress {width:27%;}}
"""


GLOBAL_HEAD = ('<style>footer[aria-label="Gradio footer navigation"],#sentence-repeat-control{display:none!important}'
               '@media(max-width:700px){body .gradio-container{padding:0 14px 30px!important}'
               '.gradio-container>.main{padding:0!important}'
               '.gradio-container main{padding:12px 0!important}}</style>')


def build_app():
    assets = dict(css=CSS, head=GLOBAL_HEAD,
                  js=Path(__file__).with_name("player.js").read_text(encoding="utf-8"))
    with gr.Blocks(title="视频学习工作台", **({} if GRADIO_6 else assets)) as app:
        selected = gr.State(None)
        note_revision = gr.State(0)
        page = gr.BrowserState(1, storage_key="video-study-page-v1", secret="video-study-local-preferences-v1")
        page_size_state = gr.BrowserState(PAGE_SIZE, storage_key="video-study-page-size-v1", secret="video-study-local-preferences-v1")
        known_done = gr.State("")
        prompted_auth = gr.State(0)
        with gr.Column(visible=False, elem_id="note-delete-dialog") as note_delete_dialog:
            gr.Markdown("### 删除笔记")
            note_delete_text = gr.Markdown()
            pending_note_delete = gr.State(None)
            with gr.Row():
                note_delete_confirm = gr.Button("确认删除", variant="stop")
                note_delete_cancel = gr.Button("取消")
        with gr.Tabs() as tabs:
            with gr.Tab("下载与导入"):
                with gr.Row():
                    with gr.Column(scale=5):
                        url = gr.Textbox(label="B 站单集或视频链接", placeholder="https://www.bilibili.com/video/BV...")
                        manifest = gr.File(label="合集 JSON 清单", file_types=[".json"], type="filepath", height=112)
                        allow_partial = gr.Checkbox(label="允许非全量清单（请先核对）")
                    with gr.Column(scale=2, elem_classes="form-settings"):
                        browser = gr.Dropdown([("不使用", "none"), ("Chrome", "chrome"), ("Edge", "edge")], value=store.preferred_browser(), label="浏览器登录状态")
                        quality = gr.Dropdown([("480p", "480"), ("720p", "720"), ("最高", "best")], value="720", label="清晰度")
                        start = gr.Button("开始下载", variant="primary")
                download_status = gr.Markdown()
                gr.HTML('<div class="section-head"><h3>下载任务</h3></div>')
                job_table = gr.HTML(value=jobs)
                with gr.Accordion("导入本机视频", open=False):
                    upload = gr.File(label="视频文件", file_types=[".mp4", ".mkv", ".webm"], type="filepath", height=112)
                    upload_button = gr.Button("导入视频")
                    upload_status = gr.Markdown()
            with gr.Tab("视频库"):
                video_rows = []
                for _ in range(MAX_PAGE_SIZE):
                    with gr.Row(visible=False, elem_classes="video-list-row") as row_container:
                        row_open = gr.Button("", elem_classes="video-open", size="sm", scale=5)
                        row_meta = gr.Button("", elem_classes="video-meta", size="sm", scale=2)
                        row_delete = gr.Button("删除", elem_classes="video-delete", size="sm", scale=0, min_width=48)
                    row_id = gr.State(None)
                    video_rows.append((row_container, row_open, row_meta, row_id, row_delete))
                with gr.Row(elem_classes="page-nav"):
                    previous = gr.Button("上一页", size="sm", scale=0, min_width=72)
                    page_text = gr.Markdown(elem_classes="page-count")
                    page_size = gr.Dropdown([10, 20, 40, 80], value=PAGE_SIZE, label="每页条数", show_label=False, elem_classes="library-size", scale=0, min_width=80)
                    following = gr.Button("下一页", size="sm", scale=0, min_width=72)
                library_status = gr.Markdown()

            with gr.Tab("学习详情", id="detail"):
                heading = gr.Markdown("未选择视频", elem_classes="detail-heading")
                player_html = gr.HTML(player(None))
                subtitle_button = gr.Button("生成字幕", visible=False)
                subtitle_status = gr.Markdown()
                with gr.Tabs(elem_classes="study-secondary"):
                    with gr.Tab("AI 对话"):
                        sentence = gr.Dropdown(show_label=False, choices=[])
                        template = gr.Dropdown(["自定义", *PROMPTS], show_label=False, label="提问模板")
                        question = gr.Textbox(label="补充", lines=2)
                        ask_button = gr.Button("提问", variant="primary")
                        chat_status = gr.Markdown("AI 未配置")
                        chat_kwargs = {} if GRADIO_6 else {"type": "messages"}
                        chat = gr.Chatbot(label="对话记录", height=320, **chat_kwargs)
                    with gr.Tab("笔记"):
                        note_id = gr.State(None)
                        note_time = gr.Number(value=0, visible=False)
                        note_content = gr.Textbox(show_label=False, lines=3)
                        with gr.Row():
                            save_button = gr.Button("保存", variant="primary")
                            cancel_edit = gr.Button("取消")
                        note_status = gr.Markdown()

                        @gr.render(inputs=[selected, note_revision], queue=False)
                        def render_notes(video_id, _revision):
                            rows = store.list_notes(video_id) if video_id else []
                            if not rows:
                                gr.Markdown("暂无笔记")
                            for row in rows:
                                with gr.Row(elem_classes="note-list-row"):
                                    gr.Markdown(f"{row['at_seconds']:.1f}s", elem_classes="note-time")
                                    edit_note = gr.Button(row["content"].replace("\n", " ")[:180], elem_classes="note-open", scale=1, min_width=0)
                                    delete_note = gr.Button("删除", elem_classes="note-delete", scale=0, min_width=48)
                                    row_id = gr.State(row["id"])
                                edit_note.click(choose_note, [selected, row_id], [note_id, note_content, note_status])
                                delete_note.click(request_note_delete, [selected, row_id],
                                                  [note_delete_dialog, pending_note_delete, note_delete_text])

        with gr.Column(visible=False, elem_id="auth-dialog") as auth_dialog:
            gr.Markdown("### 重新读取浏览器登录状态")
            auth_text = gr.Markdown()
            retry_browser = gr.Dropdown([("Chrome", "chrome"), ("Edge", "edge")], value=store.preferred_browser() if store.preferred_browser() != "none" else "edge", label="浏览器")
            with gr.Row():
                auth_retry = gr.Button("重新读取并重试", variant="primary")
                auth_cancel = gr.Button("稍后处理")
        with gr.Column(visible=False, elem_id="delete-dialog") as delete_dialog:
            gr.Markdown("### 删除视频")
            delete_text = gr.Markdown()
            pending_delete = gr.State(None)
            with gr.Row():
                delete_confirm = gr.Button("确认删除", variant="stop")
                delete_cancel = gr.Button("取消")
        timer = gr.Timer(3)
        subtitle_timer = gr.Timer(3)
        sentence_timer = gr.Timer(1)

        library_outputs = [page, page_text]
        for row_container, row_open, row_meta, row_id, _ in video_rows:
            library_outputs.extend((row_container, row_open, row_meta, row_id))
        detail_outputs = [heading, player_html, chat, sentence, subtitle_button, subtitle_status]

        app.load(initial_library, [page, page_size_state], [page_size, *library_outputs])
        start.click(download, [url, manifest, allow_partial, browser, quality], [download_status, job_table])
        browser.change(store.set_preferred_browser, browser, outputs=None)
        upload_button.click(upload_video, [upload, page_size], [upload_status, *library_outputs])
        page_size.input(change_page_size, page_size, [page_size_state, *library_outputs])
        previous.click(lambda p, size: library(max(1, p - 1), size), [page, page_size], library_outputs)
        following.click(lambda p, size: library(p + 1, size), [page, page_size], library_outputs)
        for _, row_open, row_meta, row_id, row_delete in video_rows:
            for opener in (row_open, row_meta):
                opener.click(choose_video, row_id, [selected, tabs]).then(detail, selected, detail_outputs).then(
                    lambda: (None, "", ""), outputs=[note_id, note_content, note_status])
            row_delete.click(request_delete, row_id, [delete_dialog, pending_delete, delete_text])
        delete_confirm.click(remove_video, [pending_delete, page, selected, page_size], [library_status, *library_outputs, delete_dialog, pending_delete, selected]).then(detail, selected, detail_outputs)
        delete_cancel.click(lambda: (gr.update(visible=False), None), outputs=[delete_dialog, pending_delete])
        auth_retry.click(retry_download, [prompted_auth, url, manifest, allow_partial, retry_browser, quality], [download_status, job_table, auth_dialog, browser])
        auth_cancel.click(lambda: gr.update(visible=False), outputs=auth_dialog)
        timer.tick(monitor, [page, known_done, prompted_auth, page_size], [job_table, *library_outputs, known_done, prompted_auth, auth_dialog, auth_text, retry_browser], queue=False)
        subtitle_button.click(submit_subtitle, selected, [subtitle_status, subtitle_button])
        subtitle_timer.tick(poll_subtitle, selected, detail_outputs, queue=False)
        sentence_timer.tick(fn=lambda current: current, inputs=sentence, outputs=sentence, queue=False, show_progress="hidden",
                            js="(current) => { const root = document.querySelector('#study-player'); if (!root) return current; const video = root.querySelector('video'); const segments = JSON.parse(root.dataset.segments || '[]'); if (!video || !segments.length) return current; const index = segments.findIndex(s => video.currentTime < s.end - 0.04); return index < 0 ? segments.length - 1 : index; }")
        ask_button.click(ask, [selected, sentence, template, question], [chat, chat_status, question])
        save_button.click(save_note, [selected, note_id, note_content, note_time, note_revision], [note_status, note_revision, note_id, note_content],
                          js="(videoId,noteId,content,time,revision) => [videoId,noteId,content,document.querySelector('#study-player video')?.currentTime ?? 0,revision]")
        note_delete_confirm.click(remove_note, [selected, pending_note_delete, note_revision, note_id],
                                  [note_status, note_revision, note_delete_dialog, pending_note_delete, note_id, note_content])
        note_delete_cancel.click(lambda: (gr.update(visible=False), None), outputs=[note_delete_dialog, pending_note_delete])
        cancel_edit.click(lambda: (None, "", ""), outputs=[note_id, note_content, note_status])
    return app


def main():
    launch_kwargs = dict(server_name=SERVER, server_port=SERVER_PORT,
                         allowed_paths=[str(store.media_dir)], auth=configured_auth())
    if GRADIO_6:
        player_script = Path(__file__).with_name("player.js").read_text(encoding="utf-8")
        launch_kwargs.update(css=CSS, head=GLOBAL_HEAD,
                             js=f"if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ({player_script}), {{once:true}}); else ({player_script})();")
    else:
        launch_kwargs["show_api"] = False
    build_app().queue().launch(**launch_kwargs)


if __name__ == "__main__":
    main()
