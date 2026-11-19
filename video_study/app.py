import html
import json
from pathlib import Path
from threading import Lock
from urllib.parse import quote

import gradio as gr

from ai import AIClient
from library import group_videos
from vocabulary import TARGETS, VocabularyService, fingerprint
from config import GRADIO_AUTH_PASSWORD, GRADIO_AUTH_USER, PROMPTS,SERVER,SERVER_PORT
from downloader import DownloadManager, import_video, parse_targets
from storage import Store
from subtitles import Transcriber

store = Store()
downloads = DownloadManager(store)
transcriber = Transcriber()
ai = AIClient()
vocabulary_service = VocabularyService(store, ai)
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


LIBRARY_OUTPUT_COUNT = 2


def library():
    groups = group_videos(store.all_videos())
    total = sum(len(group['videos']) for group in groups)
    return f"共{len(groups)}项 · {total}个视频", groups


def refresh_library(existing):
    count, groups = library()
    # Playback progress does not change the hierarchy. Avoid tearing down live
    # dynamic controls merely because a video has been watched since last visit.
    def structure(rows):
        return [(group['key'], [{k: v for k, v in video.items() if k not in {'position', 'watched_at'}}
                                for video in group['videos']]) for group in rows]
    return count, gr.update() if structure(existing or []) == structure(groups) else groups


def latest_series_video(group_key):
    group = next((g for g in group_videos(store.all_videos()) if g['key'] == group_key), None)
    return next((v for v in group['videos'] if v['id'] == group['resume']), None) if group else None


def continue_label(title, video):
    return f"{title} · 继续播放 第{video['season']}季 第{video['episode']}集 · {video.get('position', 0)/60:.1f}分"


def continue_series(group_key):
    video = latest_series_video(group_key)
    return choose_video(video['id'] if video else None)


def refresh_continue(group_key, title):
    video = latest_series_video(group_key)
    return gr.update(value=continue_label(title, video)) if video else gr.update()


def download(url, manifest, allow_partial, browser, quality):
    try:
        store.set_preferred_browser(browser)
        targets = parse_targets(url or "", manifest, allow_partial, include_restricted=browser != "none")
        job_id = downloads.start(targets, browser, quality)
        return f"任务 #{job_id} 已加入队列，共 {len(targets)} 条。", jobs()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return message(error), jobs()


def upload_video(file):
    if not file:
        return "请选择视频文件", *library()
    try:
        video_id = import_video(store, file)
        return f"已导入视频 #{video_id}", *library()
    except (OSError, ValueError) as error:
        return message(error), *library()


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


def monitor(known_done, prompted_auth):
    active = store.list_downloads()
    table = jobs()
    done_signature = json.dumps([(row["id"], row["done_count"]) for row in active if row["done_count"]], separators=(",", ":"))
    library_result = library() if done_signature != known_done else (gr.update(),) * LIBRARY_OUTPUT_COUNT
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
    target = video_id if store.get_video(video_id) else None
    return target, gr.update(selected="detail"), *detail(target)


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
    return f'''<section id="study-player" data-video-id="{video_id}" data-segments="{data}" data-resume="{video.get('position', 0)}">
      <div class="video-frame"><video controls playsinline preload="metadata" src="{src}"></video>
        <div class="vocab-current" aria-live="polite" hidden></div>
      </div>
      <details class="player-settings" open>
        <summary>播放设置<span class="settings-toggle-hint" aria-hidden="true"></span></summary>
      <div class="study-controls">
        <label><input type="checkbox" class="repeat-mode"> 逐句</label>
        <label>循环<input class="repeat-count" type="number" min="1" max="20" value="3"></label>
        <label>遍停 <input class="pause-factor" type="number" min="0" max="10" step="0.5" value="0">X句</label>
        <label>循停 <input class="end-pause-factor" type="number" min="0" max="10" step="0.5" value="0">X句</label>
        <label title="独立说话人句子按词数跳过；无说话人时先跳词再并句；0为不跳过"><input type="checkbox" class="skip-mode" checked> 跳词 <input class="skip-words" type="number" min="0" step="1" value="0" aria-label="跳词数量"></label>
        <label title="连续同说话人走并句；无说话人时先跳词再并句；保留1.5秒间隔与词数限制"><input type="checkbox" class="merge-mode"> 并句</label>
        <label title="少于此词数才向后并句，累计达到后停止；不保证每组都能达到"><input class="merge-min-words" type="number" min="1" max="100" step="1" value="8" aria-label="并句最低单词数">-<input class="merge-max-words" type="number" min="1" max="200" step="1" value="20" aria-label="并句最多单词数"></label>
        <label>速度 <select class="play-speed">{''.join(f'<option value="{s}" {"selected" if s == 1 else ""}>{s}×</option>' for s in (0.5, 0.75, 1, 1.25, 1.5, 2))}</select></label>
      </div></details><div class="play-status" aria-live="polite"></div>
      <div class="sentence-toolbar"><span>字幕</span><div class="sentence-actions"><button type="button" class="playback-toggle" aria-label="启动" title="读取当前设置并启动播放">启动</button><button type="button" class="next-sentence" title="跳转到下一句" {'disabled' if not segments else ''}>下一句</button><button type="button" class="tornado-mode" aria-pressed="false" title="锁定当前句或组无限循环" {'disabled' if not segments else ''}>龙卷风</button><button type="button" class="sentence-expand" aria-expanded="false">展开全部</button></div></div>
      <div class="sentence-list">{buttons or '<p>暂无字幕，仍可正常播放。字幕生成完成后将自动载入逐句学习。</p>'}</div>
    </section>'''


def detail(video_id):
    video = store.get_video(video_id) if video_id else None
    if not video:
        return "未选择视频", player(None), [], gr.update(choices=[], value=None), gr.update(visible=False), ""
    segments = store.get_segments(video_id)
    sentence_choices = [(f"{i + 1}. {s['text'][:75]}", f"{video_id}:{i}") for i, s in enumerate(segments)]
    state = video["subtitle_state"]
    status = ("字幕正在生成，完成后自动载入…" if state == "running" else
              f"字幕生成失败：{video['subtitle_error']}" if state == "failed" else
              "")
    return (f"### {video['title']}", player(video_id),
            store.list_chat(video_id), gr.update(choices=sentence_choices, value=f"{video_id}:0" if sentence_choices else None),
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


def remove_video(video_id, selected_id):
    if not video_id:
        return "请先选择视频", *library(), gr.update(visible=False), None, selected_id
    try:
        store.delete_video(video_id)
        return "视频和关联记录已删除", *library(), gr.update(visible=False), None, None if selected_id == video_id else selected_id
    except (OSError, ValueError) as error:
        return message(error), *library(), gr.update(visible=False), None, selected_id


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
    history = store.list_chat(video_id)
    sentence = ""
    if sentence_index is not None and sentence_index != "":
        try:
            owner, raw_index = sentence_index.split(":", 1)
            index = int(raw_index)
            if owner != str(video_id) or not 0 <= index < len(segments):
                raise ValueError("stale sentence")
            sentence = segments[index]["text"]
        except (AttributeError, TypeError, ValueError):
            return history, "字幕选择已失效，请在当前视频重新选择后提问", question
    try:
        answer = ai.ask(video["title"], sentence, question or "", template or "", history)
        prompt = f"[{template}] {sentence}\n{question or ''}".strip()
        store.add_chat(video_id, "user", prompt)
        store.add_chat(video_id, "assistant", answer)
        return store.list_chat(video_id), "已回复", ""
    except (RuntimeError, ValueError) as error:
        return history, message(error), question


def remember_target(target):
    if target in TARGETS:
        store.set_setting('learning_target', target)


def start_vocabulary(video_id, target):
    try:
        remember_target(target)
        result = vocabulary_service.start(video_id, target)
        return result if not result.startswith("已开始") else "任务已提交，进度与结果见下方词汇区"
    except (RuntimeError, ValueError) as error:
        return message(error)


def vocabulary_view(video_id, target):
    segments = store.get_segments(video_id) if video_id else []
    digest = fingerprint(segments)
    row = store.vocabulary(video_id, target) if video_id else None
    items = []
    if not video_id:
        status = "请先选择视频"
    elif not segments:
        status = "请先生成字幕，再整理词汇"
    elif not row:
        status = "尚未整理此学习目标的词汇"
    elif row['fingerprint'] != digest:
        status = "字幕已更新，请重新整理词汇"
    else:
        status = row['detail']
        if row['state'] == 'ready':
            items = row['items']
            if not items:
                status = "本视频没有筛选出适合该目标的词汇，可更换学习目标"
    cards = ''.join(
        f'<button type="button" class="vocab-item" data-vocab-start="{item["start"]}" data-vocab-end="{item["end"]}" '
        f'data-cue="{item["cue"]}"><strong>{html.escape(item["term"])}</strong><span>{html.escape(item["meaning"])}</span>'
        f'<em>{html.escape(item["example"])}</em><span>{html.escape(item["translation"])}</span></button>' for item in items)
    data = html.escape(json.dumps(segments, ensure_ascii=False), quote=True)
    content = (f'<section id="vocabulary-panel" data-owner="{video_id}" data-source="{data}">'
               f'<div class="vocab-heading">{html.escape(target or "")} · 词汇学习</div><p>{html.escape(status)}</p>'
               f'<div class="vocab-list" aria-label="词汇列表">{cards}</div></section>')
    return content, fingerprint([{'content': content}])


def vocabulary_ui(video_id, target):
    content, signature = vocabulary_view(video_id, target)
    segments = store.get_segments(video_id) if video_id else []
    row = store.vocabulary(video_id, target) if video_id else None
    valid = bool(row and row['fingerprint'] == fingerprint(segments))
    running = bool(valid and row['state'] == 'running')
    ready = bool(valid and row['state'] == 'ready' and row['items'])
    button = gr.update(visible=not ready, interactive=bool(segments) and not running,
                       value="整理中…" if running else "整理词汇")
    feed = content.replace('id="vocabulary-panel"', 'id="vocabulary-feed"', 1)
    return content, signature, button, feed


def request_vocabulary(video_id, target):
    status = start_vocabulary(video_id, target)
    return *vocabulary_ui(video_id, target), status


def poll_vocabulary(video_id, target, signature):
    result = vocabulary_ui(video_id, target)
    return (gr.update(), gr.update(), result[2], gr.update()) if result[1] == signature else result


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
.current-video {border-left:3px solid #67dac4; padding-left:8px;}
#vocabulary-feed {display:none !important;}
#vocabulary-panel {border:1px solid #304348; border-radius:8px; padding:14px; margin-bottom:16px; background:#132124;}
#vocabulary-panel p {font-size:12px; color:#a3b6b5; margin:5px 0;}
.vocab-heading {font-size:13px; color:#67dac4; font-weight:650;}
.vocab-list {max-height:360px; overflow:auto;}
.vocab-current[hidden] {display:none;}
.vocab-current {position:absolute; z-index:2; top:12px; left:12px; right:12px; max-height:45%; overflow:auto; pointer-events:none; scrollbar-width:none;}
.vocab-item,.vocab-card {text-align:left; display:flex; flex-direction:column; gap:5px; padding:12px; margin:6px 0; width:100%; background:#1a3033; border:1px solid #304348; border-radius:6px; color:#d8e7e3; overflow-wrap:anywhere;}
.video-frame .vocab-card {box-sizing:border-box; margin:0 0 6px; padding:8px 12px; background:rgba(10,22,25,.82); border-color:rgba(139,231,210,.3); font-size:clamp(11px,1.5vw,14px); line-height:1.4;}
.video-frame .vocab-card strong {font-size:clamp(14px,1.8vw,18px);}
.vocab-item {cursor:pointer;}
.vocab-item.active {border-color:#67dac4;}
.vocab-item strong,.vocab-card strong {color:#8be7d2; font-size:18px;}
.vocab-item em,.vocab-card em {font-style:normal; color:#f1ce95;}
.vocab-item:focus-visible {outline:2px solid #67dac4;}
.video-list-row {align-items:center; border-bottom:1px solid #304348; gap:6px !important; padding:7px 0; min-height:58px;}
.video-list-row .video-open,.video-list-row .video-meta {text-align:left; justify-content:flex-start; background:transparent !important; border:0 !important; box-shadow:none !important;}
.video-list-row .video-open {font-weight:600; color:#e8f2ef; white-space:normal; overflow-wrap:anywhere;}
.video-list-row .video-open:hover {color:#54d9c4;}
.video-list-row .video-meta {color:#a3b6b5; font-size:12px;}
.video-list-row .video-delete {white-space:nowrap; padding:4px !important; max-width:48px; min-width:48px !important; background:transparent !important; border:0 !important; box-shadow:none !important; color:#ff9a7c !important; font-size:12px;}
.note-list-row {align-items:center; border-bottom:1px solid #304348; gap:6px !important; padding:5px 0;}
.note-list-row .note-time {flex:0 0 55px !important; width:55px !important; min-width:55px !important; color:#a3b6b5; font-size:12px; font-variant-numeric:tabular-nums;}
.note-list-row .note-time p {margin:0; white-space:nowrap;}
.note-list-row .note-open {min-width:0 !important; text-align:left; justify-content:flex-start; white-space:normal; overflow-wrap:anywhere; background:transparent !important; border:0 !important; box-shadow:none !important; color:#e8f2ef !important;}
.note-list-row .note-delete {flex:0 0 48px !important; width:48px !important; min-width:48px !important; max-width:48px; white-space:nowrap; background:transparent !important; border:0 !important; box-shadow:none !important; color:#ff9a7c !important; font-size:12px;}
.detail-heading h3 {font-size:20px; margin:0 0 3px; color:#e8f2ef;}
.video-frame {position:relative; background:#11191a; aspect-ratio:16/9; max-height:70vh; display:flex; justify-content:center;}
.video-frame video {width:100%; height:100%;}
.player-settings {border-bottom:1px solid #304348;}
.player-settings summary {display:flex; align-items:center; justify-content:space-between; padding:12px 0; cursor:pointer; color:#c5d7d4; font-size:13px; font-weight:650; list-style:none;}
.player-settings summary::-webkit-details-marker {display:none;}
.player-settings summary:focus-visible {outline:2px solid #54d9c4; outline-offset:3px; border-radius:4px;}
.settings-toggle-hint::after {content:'展开 ▾'; color:#54d9c4; font-size:12px;}
.player-settings[open] .settings-toggle-hint::after {content:'收起 ▴';}
.sentence-actions {display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:8px;}
.study-controls {display:flex; align-items:center; flex-wrap:wrap; gap:10px 22px; padding:0 0 13px;}
.study-controls label {display:flex; align-items:center; gap:7px; font-size:13px; white-space:nowrap; color:#c5d7d4;}
.study-controls input[type=number] {width:55px; border:1px solid #3b555a; padding:4px; border-radius:4px; color:#e8f2ef; background:#172428;}
.study-controls select {border:1px solid #3b555a; padding:4px; border-radius:4px; color:#e8f2ef; background:#172428;}
.sentence-actions .next-sentence, .sentence-actions .playback-toggle, .sentence-actions .tornado-mode {min-height:30px; padding:4px 12px; border:1px solid #3b7772; border-radius:4px; color:#89e5d6; background:#193634; font-size:13px; font-weight:600; white-space:nowrap; cursor:pointer;}
.sentence-actions .next-sentence:hover:not(:disabled), .sentence-actions .playback-toggle:hover {background:#245049;}
.sentence-actions .tornado-mode.is-active {background:#67dac4; color:#102925; border-color:#67dac4; box-shadow:0 0 0 2px #67dac433;}
.sentence-actions .tornado-mode:focus-visible {outline:2px solid #a2f4e2; outline-offset:2px;}
.sentence-actions .next-sentence:disabled, .sentence-actions .tornado-mode:disabled {opacity:.45; cursor:not-allowed;}
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
@media(max-width:700px) {.gradio-container {padding:0 14px 30px !important;} .gradio-container main {padding:12px 14px !important;} .workspace-header {padding-top:18px;} .form-band {gap:12px !important;} .form-band .form-settings {max-width:none;} .video-frame {max-height:none;} .study-controls {gap:8px 14px;} .sentence-list {max-height:150px;} .video-list-row {flex-wrap:nowrap !important;} .video-list-row .video-meta {display:none;} .video-list-row .video-open {min-width:0 !important;} .job-progress {width:27%;}}
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
            with gr.Tab("视频库") as library_tab:
                library_groups = gr.State([])

                @gr.render(inputs=library_groups, queue=True, concurrency_limit=1, concurrency_id="library-render")
                def render_library(groups):
                    current_video = None
                    def video_row(video):
                        with gr.Row(elem_classes=["video-list-row", "current-video" if video["id"] == current_video else ""], key=f"video-row:{video['id']}"):
                            opener = gr.Button(video['title'], elem_classes="video-open", scale=5, min_width=0, key=f"video-open:{video['id']}", preserved_by_key=[])
                            subtitle_label = {'ready': '已就绪', 'none': '未生成', 'running': '生成中', 'failed': '失败'}.get(video['subtitle_state'], video['subtitle_state'])
                            gr.Markdown(f"{video['duration']/60:.1f} 分钟 · 字幕{subtitle_label}", elem_classes="video-meta", key=f"video-meta:{video['id']}", preserved_by_key=[])
                            delete = gr.Button("删除", elem_classes="video-delete", scale=0, min_width=48, key=f"video-delete:{video['id']}")
                        opener.click(lambda vid=video['id']: choose_video(vid), outputs=[selected, tabs, *detail_outputs],
                                     concurrency_id="video-selection", key=f"open-event:{video['id']}").then(lambda: (None, "", ""), outputs=[note_id, note_content, note_status], key=f"open-clear:{video['id']}")
                        delete.click(lambda vid=video['id']: request_delete(vid), outputs=[delete_dialog, pending_delete, delete_text], key=f"delete-event:{video['id']}")
                    if not groups:
                        gr.Markdown("暂无视频，请先下载或导入")
                    for group in groups:
                        current_video = group['resume'] if any(v.get('watched_at') for v in group['videos']) else None
                        if not group['series']:
                            video_row(group['videos'][0])
                            continue
                        key = group['key']
                        seasons = sorted({v['season'] for v in group['videos']})
                        preferred = store.setting('season:' + key, str(seasons[0]))
                        season_value = int(preferred) if preferred.isdigit() and int(preferred) in seasons else seasons[0]
                        resume = next(v for v in group['videos'] if v['id'] == group['resume'])
                        continue_button = gr.Button(continue_label(group['title'], resume), variant="primary", key=key+":continue", preserved_by_key=[])
                        library_tab.select(lambda group_key=key, title=group['title']: refresh_continue(group_key, title), outputs=continue_button, key=key+":refresh-continue", show_progress="hidden")
                        continue_button.click(lambda group_key=key: continue_series(group_key), outputs=[selected, tabs, *detail_outputs],
                                              concurrency_id="video-selection", key=f"continue-event:{key}").then(lambda: (None, "", ""), outputs=[note_id, note_content, note_status], key=f"continue-clear:{key}")
                        with gr.Accordion(f"{group['title']} · {len(group['videos'])} 集", open=store.setting('open:' + key) == '1',
                                          key=key, preserved_by_key=['open']) as accordion:
                            season_picker = gr.Dropdown([(f"第{n}季", n) for n in seasons], value=season_value, interactive=True, label="选择季", key=key+':season')
                            season_columns = []
                            for season in seasons:
                                with gr.Column(visible=season == season_value, key=f"{key}:season-column:{season}") as column:
                                    for video in group['videos']:
                                        if video['season'] == season:
                                            video_row(video)
                                season_columns.append(column)
                            def change_season(value, group_key=key, values=tuple(seasons)):
                                store.set_setting('season:' + group_key, str(value))
                                return [gr.update(visible=n == value) for n in values]
                            season_picker.input(change_season, season_picker, season_columns, key=key+":season-event")
                        accordion.expand(lambda group_key=key: store.set_setting('open:' + group_key, '1'), key=key+':expand')
                        accordion.collapse(lambda group_key=key: store.set_setting('open:' + group_key, '0'), key=key+':collapse')
                library_count = gr.Markdown()
                library_status = gr.Markdown()

            with gr.Tab("学习详情", id="detail"):
                heading = gr.Markdown("未选择视频", elem_classes="detail-heading")
                vocabulary_feed = gr.HTML('<section id="vocabulary-feed"></section>')
                player_html = gr.HTML(player(None))
                subtitle_button = gr.Button("生成字幕", visible=False)
                subtitle_status = gr.Markdown()
                with gr.Tabs(elem_classes="study-secondary"):
                    with gr.Tab("词汇", render_children=True):
                        vocabulary_html = gr.HTML('<section id="vocabulary-panel">请选择有字幕的视频</section>')
                        vocabulary_signature = gr.State("")
                        learning_target = gr.Dropdown(TARGETS, value=lambda: store.setting('learning_target', '四级'), label="学习目标")
                        vocabulary_button = gr.Button("整理词汇", variant="primary", interactive=False)
                        vocabulary_status = gr.Markdown()
                    with gr.Tab("AI 对话"):
                        sentence = gr.Dropdown(show_label=False, choices=[], elem_id="ai-sentence")
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

        library_outputs = [library_count, library_groups]
        detail_outputs = [heading, player_html, chat, sentence, subtitle_button, subtitle_status]

        app.load(library, outputs=library_outputs)
        start.click(download, [url, manifest, allow_partial, browser, quality], [download_status, job_table])
        browser.change(store.set_preferred_browser, browser, outputs=None)
        upload_button.click(upload_video, upload, [upload_status, *library_outputs])
        library_tab.select(refresh_library, library_groups, library_outputs, show_progress="hidden")
        vocabulary_timer = gr.Timer(2)
        position_timer = gr.Timer(5)
        vocabulary_outputs = [vocabulary_html, vocabulary_signature, vocabulary_button, vocabulary_feed]
        vocabulary_button.click(request_vocabulary, [selected, learning_target], [*vocabulary_outputs, vocabulary_status])
        learning_target.change(remember_target, learning_target)
        for event in (selected.change, learning_target.change):
            event(lambda vid, goal: (*vocabulary_ui(vid, goal), ""), [selected, learning_target], [*vocabulary_outputs, vocabulary_status])
        vocabulary_timer.tick(poll_vocabulary, [selected, learning_target, vocabulary_signature],
                              vocabulary_outputs, queue=False, show_progress="hidden")
        position_timer.tick(store.save_position, [selected, gr.Number(value=0, visible=False)], outputs=None,
                            js="(id, time) => {const p=document.querySelector('#study-player'); const v=p?.querySelector('video'); return [p ? Number(p.dataset.videoId) : null,v?.currentTime ?? 0]}",
                            queue=False, show_progress="hidden")
        delete_confirm.click(remove_video, [pending_delete, selected], [library_status, *library_outputs, delete_dialog, pending_delete, selected]).then(detail, selected, detail_outputs)
        delete_cancel.click(lambda: (gr.update(visible=False), None), outputs=[delete_dialog, pending_delete])
        auth_retry.click(retry_download, [prompted_auth, url, manifest, allow_partial, retry_browser, quality], [download_status, job_table, auth_dialog, browser])
        auth_cancel.click(lambda: gr.update(visible=False), outputs=auth_dialog)
        timer.tick(monitor, [known_done, prompted_auth], [job_table, *library_outputs, known_done, prompted_auth, auth_dialog, auth_text, retry_browser], queue=False)
        subtitle_button.click(submit_subtitle, selected, [subtitle_status, subtitle_button])
        subtitle_timer.tick(poll_subtitle, selected, detail_outputs, queue=False)
        sentence_timer.tick(fn=None, inputs=sentence, outputs=sentence, queue=False, show_progress="hidden",
                            js=Path(__file__).with_name("sentence_sync.js").read_text(encoding="utf-8"))
        # ask() validates the video owner and range against current persisted subtitles.
        # Gradio's dynamic choices may lag behind the browser during a switch.
        ask_button.click(ask, [selected, sentence, template, question],
                         [chat, chat_status, question], preprocess=False)
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
