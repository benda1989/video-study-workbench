from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from storage import Store

EPISODE_PATH = re.compile(r"/bangumi/play/ep(\d+)$")


def episode_id(source_url: str) -> int | None:
    match = EPISODE_PATH.fullmatch(urlsplit(source_url).path)
    return int(match.group(1)) if match else None


def format_video_title(metadata: dict, source_url: str, fallback: str) -> str:
    raw = str(metadata.get("title") or "").strip()
    if episode_id(source_url) is None:
        return raw or fallback
    series = str(metadata.get("series") or "").strip()
    season = str(metadata.get("season") or "").strip()
    name = series if season and season in series else " ".join(part for part in (series, season) if part)
    number = metadata.get("episode_number")
    if not str(number).isdigit():
        number = raw if raw.isdigit() else None
    if name and number is not None:
        return f"{name} · 第{int(number)}集"
    return fallback if raw.isdigit() and fallback and not fallback.isdigit() else raw or fallback


def fetch_season_titles(ep_id: int) -> dict[int, str]:
    with httpx.Client(timeout=15) as client:
        response = client.get(
            "https://api.bilibili.com/pgc/view/web/season",
            params={"ep_id": ep_id},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"},
        )
        response.raise_for_status()
        payload = response.json()
    if payload.get("code") != 0 or not isinstance(payload.get("result"), dict):
        raise ValueError(f"B 站未返回剧集信息：{payload.get('message', '未知错误')}")
    season = payload["result"]
    name = str(season.get("title") or "").strip()
    if not name:
        raise ValueError("B 站未返回番剧名称")
    titles: dict[int, str] = {}
    for item in season.get("episodes") or []:
        if not isinstance(item, dict) or not str(item.get("id", "")).isdigit():
            continue
        number = str(item.get("title") or item.get("show_title") or "").strip()
        if number.isdigit():
            titles[int(item["id"])] = f"{name} · 第{int(number)}集"
        elif number:
            titles[int(item["id"])] = f"{name} · {number}"
    return titles


def repair_generic_bangumi_titles(store: Store) -> tuple[int, list[str]]:
    candidates = []
    page = 1
    while True:
        rows, _ = store.list_videos(page, 50)
        candidates.extend(row for row in rows if row["title"].strip().isdigit() and episode_id(row["source_url"]) is not None)
        if len(rows) < 50:
            break
        page += 1
    updated = 0
    errors: list[str] = []
    while candidates:
        current = candidates[0]
        ep_id = episode_id(current["source_url"])
        try:
            titles = fetch_season_titles(ep_id)
        except (httpx.HTTPError, ValueError) as error:
            errors.append(f"ep{ep_id}: {error}")
            candidates.pop(0)
            continue
        remaining = []
        for video in candidates:
            title = titles.get(episode_id(video["source_url"]))
            if title:
                updated += store.update_video_title(video["id"], video["title"], title)
            else:
                remaining.append(video)
        if current in remaining:
            errors.append(f"ep{ep_id}: 剧集列表中没有此集")
            remaining.remove(current)
        candidates = remaining
    return updated, errors
