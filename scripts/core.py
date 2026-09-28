"""Deterministic, offline parts of BOBO media research."""
from __future__ import annotations

import csv
import ipaddress
import json
import re
import socket
import statistics
import tempfile
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


FIELDS = ("platform", "post_id", "url", "author_id", "author_name", "title", "published_at", "observed_at", "followers", "views", "likes", "comments", "saves", "shares", "source_file")
ALIASES = {
    "platform": ("platform", "平台"), "post_id": ("post_id", "aweme_id", "note_id", "作品id", "笔记id"),
    "url": ("url", "link", "share_url", "作品链接", "视频链接", "笔记链接"),
    "author_id": ("author_id", "uid", "user_id", "作者id"), "author_name": ("author_name", "nickname", "作者", "博主"),
    "title": ("title", "desc", "note_title", "标题", "文案"), "published_at": ("published_at", "create_time", "发布时间"),
    "followers": ("followers", "follower_count", "粉丝数", "粉丝"), "views": ("views", "play_count", "播放量", "观看量"),
    "likes": ("likes", "digg_count", "点赞数", "点赞"), "comments": ("comments", "comment_count", "评论数", "评论"),
    "saves": ("saves", "collect_count", "收藏数", "收藏"), "shares": ("shares", "share_count", "分享数", "分享"),
}


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
        temp = Path(f.name)
    temp.replace(path)


def read_json(path: Path, fallback=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_window(zone: str, instant: datetime | None = None) -> tuple[str, str]:
    tz = ZoneInfo(zone)
    end = instant or datetime.now(timezone.utc)
    local = end.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def instant(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            n = float(value)
            return datetime.fromtimestamp(n / (1000 if n > 10**11 else 1), timezone.utc)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def count(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    match = re.fullmatch(r"\s*([\d,]+(?:\.\d+)?)\s*([万亿wWkKmM]?)\s*", str(value))
    if not match:
        return None
    return int(float(match[1].replace(",", "")) * {"": 1, "万": 10**4, "亿": 10**8, "w": 10**4, "k": 10**3, "m": 10**6}[match[2].lower()])


def dig(data, path: str):
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def first(data, *paths):
    for path in paths:
        value = dig(data, path)
        if value not in (None, ""):
            return value
    return None


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url or "")
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("aweme_id", "note_id", "item_id"):
        if key in query:
            return f"{parsed.netloc.lower()}:{key}:{query[key][0]}"
    m = re.search(r"/(?:video|explore|discovery/item)/([\w-]+)", parsed.path)
    return f"{parsed.netloc.lower()}:{m.group(1)}" if m else urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), "", ""))


def normalize(raw: dict, platform: str, source_file: str = "") -> dict:
    item = raw
    for _ in range(4):
        if any(k in item for k in ("aweme_id", "note_id", "aweme_info", "note_info", "statistics", "interact_info")):
            break
        nested = item.get("data")
        if not isinstance(nested, dict):
            break
        item = nested
    item = item.get("aweme_info") or item.get("note_info") or item.get("note") or item
    author = item.get("author") or item.get("user") or item.get("user_info") or {}
    stats = item.get("statistics") or item.get("stats") or item.get("interact_info") or {}
    def pick(*paths):
        return first(item, *paths)
    post_id = pick("aweme_id", "note_id", "id", "item_id")
    if not post_id:
        post_id = first(raw, "post_id", "note_id", "aweme_id")
    url = pick("share_url", "url", "link") or raw.get("url")
    if not url and post_id:
        url = (f"https://www.douyin.com/video/{post_id}" if platform == "douyin" else f"https://www.xiaohongshu.com/explore/{post_id}")
    def metric(*keys):
        return count(first(stats, *keys) if first(stats, *keys) is not None else first(item, *keys))
    published = pick("create_time", "publish_time", "published_at", "time")
    dt = instant(published)
    return {
        "platform": platform, "post_id": str(post_id or canonical_url(url or "")), "url": url or "",
        "author_id": str(first(author, "sec_uid", "uid", "user_id", "id") or pick("author_id") or ""),
        "author_name": str(first(author, "nickname", "name", "user_name") or pick("author_name") or ""),
        "title": str(pick("title", "desc", "description", "text") or ""),
        "published_at": dt.isoformat() if dt else None, "observed_at": now(),
        "followers": count(first(author, "follower_count", "fans", "fans_count") if first(author, "follower_count", "fans", "fans_count") is not None else pick("followers")),
        "views": metric("play_count", "view_count", "views", "video_play_count"),
        "likes": metric("digg_count", "liked_count", "like_count", "likes"),
        "comments": metric("comment_count", "comments"), "saves": metric("collect_count", "favorite_count", "saves"),
        "shares": metric("share_count", "shares"), "source_file": source_file,
        "metric_source": "tikhub_api", "source_reliability": "platform_response",
    }


def classify(row: dict, followers_max: int, views_min: int, interaction_min: int = 1000) -> dict:
    item = dict(row)
    followers, views = count(item.get("followers")), count(item.get("views"))
    item["followers"], item["views"] = followers, views
    item["missing"] = [key for key in ("followers", "views", "likes", "comments", "saves", "shares") if item.get(key) is None]
    if followers is None:
        label = "数据不足"
    elif followers <= followers_max and views is not None and views >= views_min:
        label = "低粉高播放已验证样本" if item.get("source_reliability") in ("platform_response", "verified_import") else "低粉高播放待核源候选"
    elif followers <= followers_max and views is None and sum(count(item.get(k)) or 0 for k in ("likes", "comments", "saves", "shares")) >= interaction_min:
        label = "低粉高互动候选"
    else:
        label = "其他样本" if views is not None else "数据不足"
    item["classification"] = label
    item["thresholds"] = {"followers_max": followers_max, "views_min": views_min, "interaction_min": interaction_min}
    item["view_follower_ratio"] = views / followers if views is not None and followers and followers > 0 else None
    required = [count(item.get(k)) for k in ("likes", "comments", "shares")]
    item["engagement_rate"] = sum(required) / views if views and views > 0 and all(x is not None for x in required) else None
    item["observed_interactions_lower_bound"] = sum(x for x in required if x is not None)
    return item


def with_baselines(rows: list[dict], min_samples: int = 10) -> list[dict]:
    output = []
    for row in rows:
        item = dict(row)
        target_time = instant(item.get("published_at"))
        key = (item.get("platform"), item.get("author_id") or item.get("author_name"))
        prior = [x for x in rows if x is not row and (x.get("platform"), x.get("author_id") or x.get("author_name")) == key
                 and x.get("post_id") != item.get("post_id") and target_time and instant(x.get("published_at"))
                 and instant(x["published_at"]) < target_time]
        prior.sort(key=lambda x: instant(x["published_at"]), reverse=True)
        values = [count(x.get("views")) for x in prior[:20]]
        values = [x for x in values if x is not None]
        base = statistics.median(values) if values else None
        item["baseline_views"] = base
        item["baseline_sample_size"] = len(values)
        item["baseline_confidence"] = "directional" if len(values) >= min_samples else "insufficient"
        views = count(item.get("views"))
        item["relative_performance"] = views / base if views is not None and base and base > 0 else None
        if item.get("classification") == "其他样本" and item["relative_performance"] is not None and item["relative_performance"] >= 3 and len(values) >= min_samples:
            item["classification"] = "相对账号基线异常候选"
        output.append(item)
    return output


def dedupe(rows: list[dict]) -> list[dict]:
    seen, output = set(), []
    for row in rows:
        identity = row.get("post_id") or (canonical_url(row.get("url", "")) if row.get("url") else None)
        key = (row.get("platform"), identity if identity else id(row))
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def safe_cell(value) -> str:
    text = "" if value is None else (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_tables(out: Path, tables: dict[str, list[dict]]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, rows in tables.items():
        fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
        with (out / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: safe_cell(v) for k, v in row.items()})
        (out / f"{name}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        counts[name] = len(rows)
    try:
        from openpyxl import Workbook
        wb = Workbook()
        wb.remove(wb.active)
        for name, rows in tables.items():
            ws = wb.create_sheet(name[:31])
            fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
            ws.append(fields)
            for row in rows:
                ws.append([safe_cell(row.get(key)) for key in fields])
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
        wb.save(out / "research.xlsx")
    except ImportError:
        counts["xlsx"] = "openpyxl 未安装；CSV/JSONL 已导出"
    return counts


def public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("媒体 URL 必须是 HTTPS")
    host = parsed.hostname
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("本机地址不可下载")
    for answer in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(answer[4][0]).is_global:
            raise ValueError("内网地址不可下载")
    return url
