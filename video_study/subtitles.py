import math
from pathlib import Path
from typing import Any

import httpx

from config import MOSS_URL


def parse_segments(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("segments", "data", "result"):
            if key in payload:
                return parse_segments(payload[key])
        raise ValueError("字幕响应缺少 segments")
    if not isinstance(payload, list):
        raise TypeError("字幕响应不是句段列表")
    segments = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        start = item.get("start", item.get("start_time"))
        end = item.get("end", item.get("end_time"))
        try:
            if start is None and "start_ms" in item:
                start = float(item["start_ms"]) / 1000
            if end is None and "end_ms" in item:
                end = float(item["end_ms"]) / 1000
            start, end = float(start), float(end)+500
            cut = min(1000,(end-start)/100)
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            continue
        segments.append({"start": start, "end": end, "text": text, "speaker": str(item.get("speaker") or "")})
    segments[-1]["end"]+=500
    segments.sort(key=lambda segment: (segment["start"], segment["end"]))
    return segments


class Transcriber:
    def submit(self, media_path: Path) -> str:
        try:
            with media_path.open("rb") as handle, httpx.Client(timeout=180) as client:
                response = client.post(f"{MOSS_URL}/api/jobs", files={"file": (media_path.name, handle, "application/octet-stream")})
                response.raise_for_status()
                data = response.json()
        except (OSError, httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"字幕提交失败：{error}") from error
        job_id = data.get("job_id") or data.get("id") or data.get("job", {}).get("id")
        if not job_id:
            raise RuntimeError("字幕服务未返回任务 ID")
        return str(job_id)

    def status(self, job_id: str) -> tuple[str, str]:
        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(f"{MOSS_URL}/api/jobs/{job_id}")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"字幕状态查询失败：{error}") from error
        raw = str(data.get("status") or data.get("state") or data.get("job", {}).get("status") or "").lower()
        detail = str(data.get("error") or data.get("message") or "")
        if raw in {"completed", "complete", "done", "succeeded", "success", "finished", "waiting_review"}:
            return "ready", detail
        if raw in {"failed", "error", "cancelled", "canceled"}:
            return "failed", detail or "转写任务失败"
        return "running", detail

    def segments(self, job_id: str) -> list[dict]:
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(f"{MOSS_URL}/api/jobs/{job_id}/segments")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError(f"读取字幕失败：{error}") from error
        try:
            segments = parse_segments(data)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"字幕响应格式不正确：{error}") from error
        if not segments:
            raise RuntimeError("字幕服务未返回有效句段")
        return segments
