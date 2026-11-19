"""Conservative episode grouping, without network lookups."""
import re


def number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    if value == '两':
        return 2
    if '十' in value:
        left, right = value.split('十', 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(value, 0)


def episode_info(video: dict) -> tuple[str, int, int] | None:
    if video.get('series_name') and video.get('episode_number'):
        return video['series_name'], int(video.get('season_number') or 1), int(video['episode_number'])
    title = video['title'].strip()
    match = re.fullmatch(r'(.+?)\s*[·\-–]?\s*[Ss](\d+)[Ee](\d+)(?:\s.*)?', title)
    if match:
        return match[1].strip(' ·-'), int(match[2]), int(match[3])
    match = re.fullmatch(r'(.+?)\s*[·\-–]?\s*第([\d零一二三四五六七八九十两]+)集(?:\s.*)?', title)
    if not match:
        return None
    prefix, ep = match.groups()
    season = re.fullmatch(r'(.+?)\s*第([\d零一二三四五六七八九十两]+)季', prefix.strip(' ·-'))
    name = (season[1] if season else prefix).strip(' ·-')
    if not name or not number(ep):
        return None
    return name, number(season[2]) if season else 1, number(ep)


def group_videos(videos: list[dict]) -> list[dict]:
    groups = {}
    for video in videos:
        info = episode_info(video)
        key = 'series:' + info[0].casefold() if info else f"video:{video['id']}"
        if key not in groups:
            groups[key] = {'key': key, 'title': info[0] if info else video['title'], 'series': bool(info), 'videos': []}
        groups[key]['videos'].append({**video, 'season': info[1] if info else 1, 'episode': info[2] if info else 0})
    result = list(groups.values())
    for group in result:
        group['videos'].sort(key=lambda v: (v['season'], v['episode'], v['id']))
        group['resume'] = max(group['videos'], key=lambda v: v.get('watched_at') or '')['id']
    return sorted(result, key=lambda group: max(v['id'] for v in group['videos']), reverse=True)
