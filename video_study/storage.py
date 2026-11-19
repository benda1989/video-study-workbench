import sqlite3
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from config import DATA_PATH

def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self):
        data_dir = Path(DATA_PATH).expanduser()
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parent / data_dir
        self.data_dir = data_dir.resolve()
        self.media_dir = self.data_dir / "videos"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "library.sqlite3"
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    duration REAL NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    subtitle_job_id TEXT,
                    subtitle_state TEXT NOT NULL DEFAULT 'none',
                    subtitle_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    done_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    text TEXT NOT NULL,
                    speaker TEXT NOT NULL DEFAULT '',
                    UNIQUE(video_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    at_seconds REAL NOT NULL DEFAULT 0,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            if "display_name" not in {row[1] for row in db.execute("PRAGMA table_info(downloads)")}:
                db.execute("ALTER TABLE downloads ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE downloads SET state = 'interrupted', detail = '应用重启，任务已中断', updated_at = ? WHERE state IN ('queued', 'running')", (now(),))
            columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
            for name, declaration in {
                "series_name": "TEXT NOT NULL DEFAULT ''", "season_number": "INTEGER NOT NULL DEFAULT 1",
                "episode_number": "INTEGER NOT NULL DEFAULT 0", "position": "REAL NOT NULL DEFAULT 0",
                "watched_at": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE videos ADD COLUMN {name} {declaration}")
            db.execute("""CREATE TABLE IF NOT EXISTS vocabulary (
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                target TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                items TEXT NOT NULL DEFAULT '[]', detail TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(video_id, target))""")
            db.execute("UPDATE vocabulary SET state='failed',detail='应用重启，生成已中断，请重试' WHERE state='running'")
            self.migrate_media_paths(db)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate_media_paths(self, db: sqlite3.Connection) -> None:
        rows = db.execute("SELECT id,file_path,size_bytes FROM videos").fetchall()
        occupied = {row["file_path"] for row in rows}
        for row in rows:
            old_path = Path(row["file_path"])
            if not old_path.is_absolute():
                continue
            candidate = self.media_dir / old_path.name
            if not candidate.resolve().is_relative_to(self.media_dir) or not candidate.is_file() or candidate.stat().st_size != row["size_bytes"]:
                continue
            relative_path = candidate.relative_to(self.data_dir).as_posix()
            if relative_path in occupied:
                continue
            db.execute("UPDATE videos SET file_path=? WHERE id=?", (relative_path, row["id"]))
            occupied.add(relative_path)

    def video_row(self, row: sqlite3.Row) -> dict:
        video = dict(row)
        path = Path(video["file_path"])
        if not path.is_absolute():
            video["file_path"] = str(self.managed_path(path))
        return video

    def managed_path(self, path: Path) -> Path:
        resolved = (path if path.is_absolute() else self.data_dir / path).resolve()
        if not resolved.is_relative_to(self.media_dir):
            raise ValueError("文件不在受管视频目录中")
        return resolved

    def add_video(self, path: Path, title: str, source_url: str, duration: float, metadata: dict | None = None) -> int:
        path = self.managed_path(path)
        if not path.is_file() or duration <= 0:
            raise ValueError("视频文件无效")
        relative_path = path.relative_to(self.data_dir).as_posix()
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO videos(source_url,title,file_path,duration,size_bytes,added_at) VALUES(?,?,?,?,?,?)",
                (source_url, title[:300], relative_path, duration, path.stat().st_size, now()),
            )
            video_id = int(db.execute("SELECT id FROM videos WHERE file_path = ?", (relative_path,)).fetchone()[0])
            if metadata and metadata.get("series") and str(metadata.get("episode_number", "")).isdigit():
                # Metadata is authoritative; strip a matching season suffix from series.
                from library import episode_info
                inferred = episode_info({"title": title})
                name = inferred[0] if inferred else str(metadata["series"])
                season = metadata.get("season_number")
                season = int(season) if str(season).isdigit() else inferred[1] if inferred else 1
                db.execute("UPDATE videos SET series_name=?,season_number=?,episode_number=? WHERE id=?",
                           (name, season, int(metadata["episode_number"]), video_id))
            return video_id

    def list_videos(self, page: int = 1, page_size: int = 10) -> tuple[list[dict], int]:
        page = max(1, page)
        page_size = min(80, max(1, page_size))
        with self.connect() as db:
            total = int(db.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
            rows = db.execute("SELECT * FROM videos ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
            return [self.video_row(row) for row in rows], total

    def get_video(self, video_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return self.video_row(row) if row else None

    def video_for_source(self, source_url: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM videos WHERE source_url = ? ORDER BY id DESC LIMIT 1", (source_url,)).fetchone()
            return self.video_row(row) if row else None

    def update_video_title(self, video_id: int, old_title: str, new_title: str) -> bool:
        title = new_title.strip()[:300]
        if not title:
            raise ValueError("视频名称不能为空")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE videos SET title = ? WHERE id = ? AND title = ?",
                (title, video_id, old_title),
            ).rowcount
        return bool(changed)

    def delete_video(self, video_id: int) -> bool:
        video = self.get_video(video_id)
        if not video:
            return False
        path = self.managed_path(Path(video["file_path"]))
        path.unlink(missing_ok=True)
        with self.connect() as db:
            db.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        return True

    def create_download(self, source: str, total: int, display_name: str = "") -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO downloads(source,display_name,state,total_count,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (source[:500], display_name.strip()[:300], "queued", total, now(), now()),
            )
            return int(cursor.lastrowid)

    def set_download_name(self, job_id: int, display_name: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE downloads SET display_name=? WHERE id=?", (display_name.strip()[:300], job_id))

    def video_title_for_source(self, source: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT title FROM videos WHERE source_url=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        return row["title"] if row else None

    def update_download(self, job_id: int, state: str, done: int, detail: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE downloads SET state=?,done_count=?,detail=?,updated_at=? WHERE id=?", (state, done, detail[:1000], now(), job_id))

    def list_downloads(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,))]

    def preferred_browser(self) -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'preferred_browser'").fetchone()
        return row["value"] if row and row["value"] in {"none", "chrome", "edge"} else "none"

    def set_preferred_browser(self, browser: str) -> None:
        if browser not in {"none", "chrome", "edge"}:
            raise ValueError("不支持的浏览器凭证来源")
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key,value) VALUES('preferred_browser',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (browser,),
            )

    def set_subtitle_state(self, video_id: int, state: str, job_id: str | None = None, error: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE videos SET subtitle_state=?,subtitle_job_id=COALESCE(?,subtitle_job_id),subtitle_error=? WHERE id=?", (state, job_id, error[:500], video_id))

    def replace_segments(self, video_id: int, segments: list[dict]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM segments WHERE video_id=?", (video_id,))
            db.executemany(
                "INSERT INTO segments(video_id,ordinal,start_time,end_time,text,speaker) VALUES(?,?,?,?,?,?)",
                [(video_id, index, item["start"], item["end"], item["text"], item.get("speaker", "")) for index, item in enumerate(segments)],
            )
            db.execute("UPDATE videos SET subtitle_state='ready',subtitle_error='' WHERE id=?", (video_id,))

    def get_segments(self, video_id: int) -> list[dict]:
        with self.connect() as db:
            return [{"start": row["start_time"], "end": row["end_time"], "text": row["text"], "speaker": row["speaker"]}
                    for row in db.execute("SELECT start_time,end_time,text,speaker FROM segments WHERE video_id=? ORDER BY ordinal", (video_id,))]

    def add_note(self, video_id: int, content: str, at_seconds: float = 0, note_id: int | None = None) -> int:
        content = content.strip()
        if not content or len(content) > 5000 or at_seconds < 0:
            raise ValueError("笔记内容或时间无效")
        with self.connect() as db:
            if note_id is not None:
                cursor = db.execute("UPDATE notes SET content=?,at_seconds=?,updated_at=? WHERE id=? AND video_id=?", (content, at_seconds, now(), note_id, video_id))
                if not cursor.rowcount:
                    raise ValueError("笔记不存在")
                return note_id
            cursor = db.execute("INSERT INTO notes(video_id,content,at_seconds,created_at,updated_at) VALUES(?,?,?,?,?)", (video_id, content, at_seconds, now(), now()))
            return int(cursor.lastrowid)

    def list_notes(self, video_id: int) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM notes WHERE video_id=? ORDER BY id DESC", (video_id,))]

    def delete_note(self, video_id: int, note_id: int) -> bool:
        with self.connect() as db:
            return bool(db.execute("DELETE FROM notes WHERE id=? AND video_id=?", (note_id, video_id)).rowcount)

    def add_chat(self, video_id: int, role: str, content: str) -> None:
        if role not in {"user", "assistant"} or not content.strip():
            raise ValueError("对话内容无效")
        with self.connect() as db:
            db.execute("INSERT INTO chat_messages(video_id,role,content,created_at) VALUES(?,?,?,?)", (video_id, role, content[:20000], now()))

    def list_chat(self, video_id: int) -> list[dict]:
        with self.connect() as db:
            return [{"role": row["role"], "content": row["content"]}
                    for row in db.execute("SELECT role,content FROM chat_messages WHERE video_id=? ORDER BY id", (video_id,))]


    def all_videos(self) -> list[dict]:
        with self.connect() as db:
            return [self.video_row(row) for row in db.execute("SELECT * FROM videos ORDER BY id DESC")]

    def setting(self, key: str, default: str = "") -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def save_position(self, video_id: int | None, position: float) -> None:
        if not video_id or not isinstance(position, (int, float)) or not math.isfinite(position) or position < 0:
            return
        with self.connect() as db:
            db.execute("UPDATE videos SET position=MIN(?,duration),watched_at=? WHERE id=?", (position, now(), video_id))

    def vocabulary(self, video_id: int, target: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM vocabulary WHERE video_id=? AND target=?", (video_id, target)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['items'] = json.loads(result['items'])
        return result

    def save_vocabulary(self, video_id: int, target: str, fingerprint: str, state: str,
                        items: list[dict], detail: str = "") -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO vocabulary(video_id,target,fingerprint,state,items,detail) VALUES(?,?,?,?,?,?)
                ON CONFLICT(video_id,target) DO UPDATE SET fingerprint=excluded.fingerprint,
                state=excluded.state,items=excluded.items,detail=excluded.detail""",
                       (video_id, target, fingerprint, state, json.dumps(items, ensure_ascii=False), detail[:500]))
