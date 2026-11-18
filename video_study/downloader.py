import hashlib
import json
import logging
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from storage import Store
from titles import format_video_title

VIDEO_PATH = re.compile(r"/(?:video/BV[0-9A-Za-z]{10}|bangumi/play/ep\d+)/?$")
KEY = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PREVIEW_WARNING = "Only preview format is available"
logger = logging.getLogger(__name__)


def is_auth_error(detail: str) -> bool:
    text = detail.lower()
    return any(marker in text for marker in (
        "cookie", "login", "log in", "sign in", "authentication", "unauthorized",
        "会员预览", "请先登录", "未登录", "账号无权限", "付费观看",
    ))


@dataclass(frozen=True)
class Target:
    url: str
    key: str
    title: str = ""


def validate_url(raw: str) -> str:
    url = urlsplit(raw.strip())
    if url.scheme != "https" or url.hostname != "www.bilibili.com" or url.username or url.password or not VIDEO_PATH.fullmatch(url.path):
        raise ValueError("只支持 B 站视频或番剧单集 HTTPS 链接")
    query = parse_qs(url.query, keep_blank_values=True)
    if query and (set(query) != {"p"} or len(query["p"]) != 1 or not query["p"][0].isdigit() or int(query["p"][0]) < 1):
        raise ValueError("链接只允许分 P 参数 p")
    return f"https://www.bilibili.com{url.path.rstrip('/')}" + (f"?p={query['p'][0]}" if query else "")


def parse_targets(url: str, manifest_path: str | None, allow_partial: bool = False, include_restricted: bool = False) -> list[Target]:
    if manifest_path:
        path = Path(manifest_path)
        if path.stat().st_size > 3 * 1024 * 1024:
            raise ValueError("清单超过 3 MB")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
            raise ValueError("清单缺少 episodes 数组")
        if data.get("complete") is False and not allow_partial:
            raise ValueError("清单标记为非全量，请核对后勾选允许非全量")
        targets: list[Target] = []
        seen: set[str] = set()
        for item in data["episodes"]:
            if not isinstance(item, dict):
                raise TypeError("清单条目格式不正确")
            if not include_restricted and str(item.get("label", "")) not in {"", "限免"}:
                continue
            normalized = validate_url(str(item.get("url", "")))
            key = str(item.get("key") or item.get("ep_id") or "")
            if not KEY.fullmatch(key):
                raise ValueError("清单包含无效文件标识")
            if key in seen:
                raise ValueError("清单包含重复文件标识")
            seen.add(key)
            title = str(item.get("title", "")).strip()
            if (data.get("collection_type") == "bangumi" or str(data.get("season_id", "")).isdigit()) and "/bangumi/play/ep" in normalized:
                season_name = str(data.get("title", "")).strip()
                number = item.get("number")
                if season_name and str(number).isdigit():
                    title = f"{season_name} · 第{int(number)}集"
            targets.append(Target(normalized, key, title))
        if not targets:
            raise ValueError("清单没有可下载的视频；受限内容需选择已登录的浏览器")
        return targets
    normalized = validate_url(url)
    path = urlsplit(normalized).path
    identifier = path.rsplit("/", 1)[-1]
    page = parse_qs(urlsplit(normalized).query).get("p", [None])[0]
    key = f"{identifier}-p{page}" if page else identifier
    return [Target(normalized, key)]


def probe_media(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if duration <= 0:
        raise ValueError("媒体时长无效")
    return duration


class DownloadManager:
    def __init__(self, store: Store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-download")

    def start(self, targets: list[Target], browser: str, quality: str) -> int:
        if browser not in {"none", "chrome", "edge"}:
            raise ValueError("不支持的浏览器凭证来源")
        if quality not in {"480", "720", "best"}:
            raise ValueError("不支持的清晰度")
        if not targets:
            raise ValueError("没有可下载的视频")
        if len(targets) == 1:
            display_name = targets[0].title or self.store.video_title_for_source(targets[0].url) or targets[0].key
        else:
            first = targets[0].title
            season = first.split(" · 第", 1)[0] if " · 第" in first else ""
            display_name = f"{season} · {len(targets)} 集" if season else f"{first or '视频合集'} 等 {len(targets)} 条"
        job_id = self.store.create_download(targets[0].url if len(targets) == 1 else f"清单：{len(targets)} 条", len(targets), display_name)
        self.pool.submit(self._run, job_id, targets, browser, quality)
        return job_id

    def _run(self, job_id: int, targets: list[Target], browser: str, quality: str) -> None:
        self.store.update_download(job_id, "running", 0)
        done = 0
        failures: list[str] = []
        auth_failed = False
        for target in targets:
            try:
                video_id = self._download_one(target, browser, quality)
                if len(targets) == 1:
                    video = self.store.get_video(video_id)
                    if video:
                        self.store.set_download_name(job_id, video["title"])
                done += 1
                self.store.update_download(job_id, "running", done, f"已完成 {target.key}")
            except Exception as error:
                logger.exception("下载失败：%s", target.key)
                auth_failed = auth_failed or (browser != "none" and is_auth_error(str(error)))
                failures.append(f"{target.key}: {str(error)[:180]}")
                self.store.update_download(job_id, "running", done, failures[-1])
        state = "done" if not failures else "auth_required" if auth_failed else "partial" if done else "failed"
        self.store.update_download(job_id, state, done, "; ".join(failures)[:1000] if failures else "下载完成")

    def _download_one(self, target: Target, browser: str, quality: str) -> int:
        recorded = self.store.video_for_source(target.url)
        if recorded and Path(recorded["file_path"]).is_file():
            if target.title and recorded["title"] in {target.key, target.url.rsplit("/", 1)[-1]}:
                self.store.update_video_title(recorded["id"], recorded["title"], target.title)
            return recorded["id"]
        existing = next((p for p in self.store.media_dir.glob(f"{target.key}.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm"}), None)
        if existing:
            return self.store.add_video(existing, target.title or target.key, target.url, probe_media(existing))
        auth = ["--cookies-from-browser", browser] if browser != "none" else []
        preflight = subprocess.run(
            ["yt-dlp", "--dump-single-json", "--no-playlist", *auth, target.url],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if preflight.returncode != 0:
            raise ValueError((preflight.stderr or "无法获取视频信息").strip()[-300:])
        if PREVIEW_WARNING in preflight.stderr:
            raise ValueError("当前凭证只能获取会员预览，未下载")
        metadata = json.loads(preflight.stdout)
        if quality == "best":
            format_selector = "bestvideo[vcodec^=avc1]+bestaudio/best"
        else:
            format_selector = f"bestvideo[height<={quality}][vcodec^=avc1]+bestaudio/best[height<={quality}]"
        command = [
            "yt-dlp", "--no-playlist", "--no-overwrites", "--print", "after_move:filepath",
            "--merge-output-format", "mp4", "-f", format_selector,
            "-o", str(self.store.media_dir / f"{target.key}.%(ext)s"), *auth, target.url,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=7200, check=False)
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "下载失败").strip()[-300:])
        candidates = [Path(line.strip()) for line in result.stdout.splitlines() if line.strip().startswith(str(self.store.media_dir))]
        path = next((p for p in reversed(candidates) if p.is_file()), None)
        if path is None:
            path = next((p for p in self.store.media_dir.glob(f"{target.key}.*") if p.suffix.lower() in {".mp4", ".mkv", ".webm"}), None)
        if path is None:
            raise ValueError("下载命令完成但未找到媒体文件")
        return self.store.add_video(path, format_video_title(metadata, target.url, target.title or target.key), target.url, probe_media(path))


def import_video(store: Store, uploaded_path: str) -> int:
    source = Path(uploaded_path)
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".mkv", ".webm"}:
        raise ValueError("请选择 MP4、MKV 或 WebM 文件")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    destination = store.media_dir / f"import-{digest.hexdigest()}{source.suffix.lower()}"
    if not destination.exists():
        shutil.copy2(source, destination)
    return store.add_video(destination, source.stem, "", probe_media(destination))
