"""Guarded TikHub REST collection. Endpoint definitions come from current public docs."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core import atomic_json, first, normalize, now, read_json

BASE = "https://api.tikhub.io"
ROUTES = {
    "douyin": {
        "search": ("POST", "/api/v1/douyin/search/fetch_video_search_v2"),
        "hot": ("POST", "/api/v1/douyin/billboard/fetch_hot_total_video_list"),
        "account": ("GET", "/api/v1/douyin/app/v3/fetch_user_post_videos"),
        "comments": ("GET", "/api/v1/douyin/app/v3/fetch_video_comments"),
        "detail": ("GET", "/api/v1/douyin/web/fetch_one_video_by_share_url"),
    },
    "xiaohongshu": {
        "search": ("GET", "/api/v1/xiaohongshu/app_v2/search_notes"),
        "account": ("GET", "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"),
        "comments": ("GET", "/api/v1/xiaohongshu/app_v2/get_note_comments"),
        "detail": ("GET", "/api/v1/xiaohongshu/app_v2/get_video_note_detail"),
    },
}


def find_items(payload, kind="posts"):
    if isinstance(payload, dict):
        for key in (("business_data", "aweme_list", "notes", "items", "list") if kind == "posts" else ("comments", "comment_list", "items")):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for key in ("data", "result", "response"):
            if key in payload:
                result = find_items(payload[key], kind)
                if result is not None:
                    return result
    return None


def pagination(payload, platform, action):
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        return None
    if action == "search" and platform == "douyin":
        cursor = data.get("cursor")
        return {"cursor": cursor, "search_id": data.get("search_id", ""), "backtrace": data.get("backtrace", "")} if cursor not in (None, "") else None
    if platform == "xiaohongshu":
        cursor = data.get("cursor")
        if action == "search":
            return {"search_id": data.get("search_id", ""), "search_session_id": data.get("search_session_id", "")}
        return {"cursor": cursor} if cursor not in (None, "") else None
    cursor = data.get("max_cursor" if action == "account" else "cursor")
    return {"max_cursor" if action == "account" else "cursor": cursor} if cursor not in (None, "") else None


class BudgetError(RuntimeError):
    pass


class UnknownOutcome(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: Path, limits: dict):
        self.path = path
        self.limits = limits
        self.data = read_json(path, {"attempts": [], "estimated_usd": 0.0})

    def charge(self, endpoint: str, unit_price: float | None, kind="tikhub"):
        if unit_price is not None:
            unit_price = float(unit_price)
            if unit_price < 0:
                raise ValueError("单价不可为负")
        if len(self.data["attempts"]) >= self.limits["max_requests"]:
            raise BudgetError("max_requests reached")
        cap = self.limits.get("max_cost_estimate")
        if cap is not None and unit_price is None:
            raise BudgetError("该端点价格未知，不能保证美元上限；需明确请求数授权")
        estimate = self.data["estimated_usd"] + (unit_price or 0)
        if cap is not None and estimate > cap:
            raise BudgetError("max_cost_estimate reached")
        self.data["attempts"].append({"at": now(), "endpoint": endpoint, "kind": kind, "unit_price": unit_price, "status": "sent"})
        self.data["estimated_usd"] = estimate
        atomic_json(self.path, self.data)
        return len(self.data["attempts"]) - 1

    def status(self, index: int, status: str):
        self.data["attempts"][index]["status"] = status
        atomic_json(self.path, self.data)


class Client:
    def __init__(self, key: str, ledger: Ledger, prices: dict):
        self.key, self.ledger, self.prices = key, ledger, prices

    def request(self, platform: str, action: str, args: dict):
        method, endpoint = ROUTES[platform][action]
        price = self.prices.get(endpoint)
        query = urllib.parse.urlencode(args) if method == "GET" else ""
        url = BASE + endpoint + ("?" + query if query else "")
        body = json.dumps(args, ensure_ascii=False).encode() if method == "POST" else None
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, request, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        for attempt in range(3):
            slot = self.ledger.charge(endpoint, price)
            headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json", "User-Agent": "bobo-media-ops/0.1"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with opener.open(request, timeout=45) as response:
                    payload = json.load(response)
                self.ledger.status(slot, "http_200")
                if not isinstance(payload, dict) or payload.get("code") not in (None, 0, 200):
                    raise RuntimeError("TikHub 返回业务错误；原始响应需核查")
                return payload
            except urllib.error.HTTPError as exc:
                self.ledger.status(slot, f"http_{exc.code}")
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise RuntimeError(f"TikHub HTTP {exc.code}") from None
                delay = min(int(exc.headers.get("Retry-After", 2**attempt)), 30)
                time.sleep(delay)
            except urllib.error.URLError:
                self.ledger.status(slot, "network_unknown")
                raise UnknownOutcome("网络状态不确定；为避免重复计费，已暂停") from None


def params(platform: str, action: str, subject: str, page: int, cursor=None):
    if action == "search":
        if platform == "douyin":
            return {"keyword": subject, "cursor": (cursor or {}).get("cursor", 0), "sort_type": "2", "publish_time": "1", "filter_duration": "0", "content_type": "1", "search_id": (cursor or {}).get("search_id", ""), "backtrace": (cursor or {}).get("backtrace", "")}
        return {"keyword": subject, "page": page, "sort_type": "time_descending", "note_type": "视频笔记", "time_filter": "一天内", "search_id": (cursor or {}).get("search_id", ""), "search_session_id": (cursor or {}).get("search_session_id", "")}
    if action == "hot" and platform == "douyin":
        return {"page": page, "page_size": 10, "date_window": 24, "sub_type": 1001, "keyword": subject, "tags": []}
    if action == "account":
        if platform == "douyin":
            return {"sec_user_id": subject, "max_cursor": (cursor or {}).get("max_cursor", 0), "count": 20, "sort_type": 0}
        return {"share_text" if subject.startswith("http") else "user_id": subject, "cursor": (cursor or {}).get("cursor", "")}
    if action == "comments":
        if platform == "douyin":
            return {"aweme_id": subject, "cursor": (cursor or {}).get("cursor", 0)}
        return {"note_id": subject, "cursor": (cursor or {}).get("cursor", ""), "index": (cursor or {}).get("index", 0), "pageArea": "UNFOLDED", "sort_strategy": "latest_v2"}
    if action == "detail":
        return {"share_url": subject} if platform == "douyin" else {"share_text": subject}
    raise ValueError("Unsupported route")


def collect_pages(client: Client, job: Path, platform: str, action: str, subject: str, max_pages: int, max_posts: int, state: dict):
    key = f"{platform}:{action}:{subject}"
    progress = state.setdefault("sources", {}).setdefault(key, {"page": 1, "cursor": None, "seen_cursors": [], "raw": [], "rows": [], "stop": None})
    while progress["page"] <= max_pages and len(progress["rows"]) < max_posts:
        page = progress["page"]
        request_args = params(platform, action, subject, page, progress["cursor"])
        response = client.request(platform, action, request_args)
        raw_path = job / "raw" / f"{platform}-{action}-{len(progress['raw'])+1:04d}.json"
        atomic_json(raw_path, response)
        progress["raw"].append(str(raw_path.relative_to(job)))
        items = [response.get("data", response)] if action == "detail" else find_items(response, "comments" if action == "comments" else "posts")
        if items is None:
            progress["stop"] = "schema_unrecognized"
            atomic_json(job / "manifest.json", state)
            break
        for item in items:
            if isinstance(item, dict):
                if action == "comments":
                    progress["rows"].append({"platform": platform, "post_id": subject, "comment_id": str(first(item, "cid", "comment_id", "id") or ""), "text": str(first(item, "text", "content", "comment") or ""), "url": "", "observed_at": now(), "source_file": str(raw_path.relative_to(job))})
                else:
                    progress["rows"].append(normalize(item, platform, str(raw_path.relative_to(job))))
        if page == 1 and action not in ("comments", "hot") and progress["rows"]:
            sample = progress["rows"][:3]
            if not all(x.get("post_id") and x.get("published_at") for x in sample):
                progress["stop"] = "sample_field_failure"
        next_cursor = pagination(response, platform, action)
        marker = json.dumps(next_cursor, sort_keys=True)
        if platform == "xiaohongshu" and action == "search":
            marker = f"page:{page}"
        if not items:
            progress["stop"] = "empty_page"
        elif marker in progress["seen_cursors"]:
            progress["stop"] = "repeated_cursor"
        elif next_cursor is None and platform == "douyin" and action not in ("hot", "detail"):
            progress["stop"] = "no_next_cursor"
        progress["seen_cursors"].append(marker)
        progress["cursor"] = next_cursor
        progress["page"] += 1
        atomic_json(job / "manifest.json", state)
        if progress["stop"]:
            break
    if not progress["stop"]:
        progress["stop"] = "page_or_post_limit"
    atomic_json(job / "manifest.json", state)
    return progress["rows"][:max_posts]
