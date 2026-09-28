#!/usr/bin/env python3
"""One entry point for offline and authorized BOBO media research."""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import secrets
import sys
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from core import ALIASES, atomic_json, classify, count, dedupe, first, instant, now, read_json, today_window, with_baselines, write_tables
from provider import BudgetError, Client, Ledger, ROUTES, UnknownOutcome, collect_pages, find_items, params

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bobo-media-ops"
DEFAULT = {
    "timezone": "Asia/Shanghai", "platforms": ["douyin", "xiaohongshu"],
    "followers_max": 50000, "views_min": 100000, "interaction_min": 1000,
    "profile": "profile.json", "max_requests": 150, "max_posts": 100,
    "max_comment_pages": 100, "max_asr_duration": 0,
    "endpoint_prices_usd": {}, "max_cost_estimate": None,
}


def config_path(arg=None):
    return Path(arg or os.environ.get("BOBO_CONFIG", HOME / "config.json")).expanduser()


def config(arg=None):
    path = config_path(arg)
    return {**DEFAULT, **(read_json(path, {}) or {})}


def key(cfg, path=None):
    env = os.environ.get("TIKHUB_API_KEY", "").strip()
    if env:
        return env, "environment"
    file = Path(path or cfg.get("key_file") or (config_path().parent / "tikhub.key")).expanduser()
    if file.exists():
        return file.read_text(encoding="utf-8-sig").strip(), "file"
    if sys.platform == "darwin":
        import subprocess
        result = subprocess.run(["security", "find-generic-password", "-s", "tikhub-api", "-a", "tikhub", "-w"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip(), "keychain"
    return "", "missing"


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def setup(args):
    path = config_path(args.config)
    cfg = config(args.config)
    if args.timezone:
        from zoneinfo import ZoneInfo
        ZoneInfo(args.timezone)
        cfg["timezone"] = args.timezone
    if args.platforms:
        cfg["platforms"] = args.platforms.split(",")
    if args.key_file:
        cfg["key_file"] = str(Path(args.key_file).expanduser().resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    atomic_json(path, cfg)
    path.chmod(0o600)
    profile = path.parent / cfg["profile"]
    if not profile.exists():
        atomic_json(profile, read_json(ROOT / "templates" / "profile.example.json"))
        profile.chmod(0o600)
    if args.save_key:
        secret = getpass.getpass("TikHub Key: ").strip()
        if not secret:
            raise ValueError("Key 不能为空")
        dest = Path(cfg.get("key_file") or path.parent / "tikhub.key")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as f:
            f.write(secret + "\n")
        dest.chmod(0o600)
    print_json({"config": str(path), "profile": str(profile), "key_present": bool(key(cfg, args.key_file)[0]), "online_verified": False})


def doctor(args):
    cfg = config(args.config)
    secret, source = key(cfg, args.key_file)
    status = {"python": sys.version.split()[0], "config": config_path(args.config).exists(), "timezone": cfg["timezone"], "tikhub_key_present": bool(secret), "key_source": source, "online_verified": False, "workbuddy_verified": False}
    try:
        import openpyxl
        status["xlsx"] = True
    except ImportError:
        status["xlsx"] = False
    if args.online:
        import urllib.request
        try:
            with urllib.request.urlopen("https://mcp.tikhub.io/health", timeout=10) as response:
                status["mcp_health"] = response.status
        except Exception as exc:
            status["mcp_health"] = type(exc).__name__
    print_json(status)


def capabilities(args):
    result = {platform: {action: {"route": path, "state": "文档核对；真实服务未验证"} for action, (_, path) in actions.items()} for platform, actions in ROUTES.items()}
    result["xiaohongshu"]["hot"] = {"state": "缺失，不称官方热榜"}
    result["wechat_channels"] = {"state": "外部下载器依赖未配置；未验证"}
    if args.online:
        secret, _ = key(config(args.config), args.key_file)
        if not secret:
            raise ValueError("需要 TikHub Key 才能动态查询 MCP 工具")
        sys.path.insert(0, str(ROOT / "vendor" / "konglong"))
        import tikhub_mcp
        old = os.environ.get("TIKHUB_API_KEY")
        os.environ["TIKHUB_API_KEY"] = secret
        try:
            for platform in ("douyin", "xiaohongshu"):
                tools = tikhub_mcp.list_tools(platform)
                result[platform]["mcp_tools"] = [{"name": x.get("name"), "description": x.get("description", "")[:120]} for x in tools]
        finally:
            if old is None:
                os.environ.pop("TIKHUB_API_KEY", None)
            else:
                os.environ["TIKHUB_API_KEY"] = old
    print_json(result)


def make_plan(args):
    cfg = config(args.config)
    pages = args.pages if args.pages is not None else (5 if args.task == "account" else 2)
    include_comments = args.comments if args.comments is not None else args.task == "account"
    if pages < 1 or args.media_limit < 0 or any(x is not None and x < 0 for x in (args.max_requests, args.max_posts, args.max_comment_pages, args.followers_max, args.views_min)):
        raise ValueError("页数须至少为 1，其余限额不可为负")
    platforms = args.platforms.split(",") if args.platforms else cfg["platforms"]
    if any(x not in ROUTES for x in platforms):
        raise ValueError("首版自动采集仅支持 douyin,xiaohongshu；其他平台可导入现有数据")
    if args.task == "search" and not args.query:
        raise ValueError("search 需要 --query")
    if args.task in ("account", "video") and not args.url:
        raise ValueError("account/video 需要 --url")
    start, end = today_window(cfg["timezone"])
    limits = {"max_requests": min(args.max_requests or cfg["max_requests"], cfg["max_requests"]),
              "max_posts": min(args.max_posts or cfg["max_posts"], cfg["max_posts"]),
              "max_comment_pages": min(args.max_comment_pages if args.max_comment_pages is not None else cfg["max_comment_pages"], cfg["max_comment_pages"]),
              "max_asr_duration": cfg["max_asr_duration"], "max_cost_estimate": cfg["max_cost_estimate"]}
    request_plan = []
    if args.task == "search":
        for platform in platforms:
            request_plan.append({"platform": platform, "action": "search", "requests": pages})
            if platform == "douyin" and args.hot:
                request_plan.append({"platform": platform, "action": "hot", "requests": 1})
    else:
        platform = platform_for(args.url)
        request_plan.append({"platform": platform, "action": args.task, "requests": pages if args.task == "account" else 1})
        if args.task == "account" and is_video_link(platform, args.url):
            request_plan.append({"platform": platform, "action": "author_from_video_detail", "requests": 1})
    if include_comments:
        request_plan.append({"platform": "selected_posts", "action": "comments", "requests": limits["max_comment_pages"]})
    if args.media_limit:
        request_plan.append({"platform": "selected_posts", "action": "media_detail", "requests": args.media_limit, "note": "ASR 另计，时长待媒体核实"})
    prices = cfg.get("endpoint_prices_usd", {})
    known_cost = 0.0
    unpriced = []
    for part in request_plan:
        action = "detail" if part["action"] in ("media_detail", "author_from_video_detail") else part["action"]
        endpoint = ROUTES.get(part["platform"], {}).get(action, (None, None))[1]
        if endpoint is None or endpoint not in prices:
            unpriced.append(f"{part['platform']}:{part['action']}")
        else:
            known_cost += float(prices[endpoint]) * part["requests"]
    plan = {"id": secrets.token_hex(6), "created_at": now(), "task": args.task, "query": args.query, "url": args.url,
            "platforms": platforms, "timezone": cfg["timezone"], "start_utc": start if args.task == "search" else None,
            "end_utc": end if args.task == "search" else None, "pages_per_source": pages,
            "include_hot": bool(args.hot), "comments": include_comments, "limits": limits,
            "thresholds": {"followers_max": args.followers_max if args.followers_max is not None else cfg["followers_max"], "views_min": args.views_min if args.views_min is not None else cfg["views_min"], "interaction_min": cfg["interaction_min"]},
            "prices": prices, "request_plan": request_plan, "estimated_usd_for_priced_actions": round(known_cost, 6),
            "unpriced_actions": unpriced, "media_limit": args.media_limit,
            "route": "REST", "status": "planned", "dry_run": True,
            "warning": "端点价格未知时，只能按授权请求数限制，无法保证美元金额；仅配置 Key 不授权扣费。"}
    path = Path(args.out or f"plan-{plan['id']}.json")
    atomic_json(path, plan)
    print_json({"plan": str(path.resolve()), **plan})


def platform_for(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return "douyin"
    if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com") or host in ("xhslink.com", "xhslink.cn"):
        return "xiaohongshu"
    raise ValueError("仅可自动处理抖音和小红书链接")


def account_subject(platform, url):
    if platform == "xiaohongshu":
        return url
    parsed = urllib.parse.urlsplit(url)
    value = urllib.parse.parse_qs(parsed.query).get("sec_user_id", [None])[0]
    match = re.search(r"/user/([\w.-]+)", parsed.path)
    if not value and match:
        value = match.group(1)
    if not value:
        raise ValueError("抖音账号链接缺少 sec_user_id；可提供主页完整分享链接")
    return value


def is_video_link(platform, url):
    parsed = urllib.parse.urlsplit(url)
    path, host = parsed.path, parsed.hostname or ""
    return (platform == "douyin" and ("/video/" in path or host == "v.douyin.com")) or (platform == "xiaohongshu" and ("/explore/" in path or "/discovery/item/" in path or host in ("xhslink.com", "xhslink.cn")))


def author_from_detail(platform, payload):
    if platform == "douyin":
        return first(payload, "data.aweme_detail.author.sec_uid", "data.data.aweme_detail.author.sec_uid", "data.aweme_info.author.sec_uid")
    return first(payload, "data.data.note.user.user_id", "data.data.user.user_id", "data.note.user.user_id", "data.user.user_id")


def run(args):
    plan_path = Path(args.plan).resolve()
    plan = read_json(plan_path)
    if not plan or plan.get("status") != "planned":
        raise ValueError("需要有效 plan 文件")
    if not args.authorize:
        raise ValueError("真实采集需要 --authorize，且须已获得用户费用授权")
    if any(platform not in ROUTES for platform in plan["platforms"]):
        raise ValueError("存在未支持的平台")
    cfg = config(args.config)
    secret, _ = key(cfg, args.key_file)
    if not secret:
        raise ValueError("TikHub Key 未配置")
    if plan["limits"]["max_cost_estimate"] is None and not args.authorize_unknown_prices:
        raise ValueError("价格未知；请先配置端点价格/金额上限，或明确授权请求数模式")
    job = Path(args.out or plan_path.parent / f"research-{plan['id']}").resolve()
    job.mkdir(parents=True, exist_ok=True)
    manifest_path = job / "manifest.json"
    manifest = read_json(manifest_path, {"plan": plan, "status": "collecting", "sources": {}, "errors": []})
    if manifest["plan"]["id"] != plan["id"]:
        raise ValueError("输出目录属于另一任务")
    if any("网络状态不确定" in error for error in manifest.get("errors", [])):
        raise ValueError("上次请求结果不明，先核对服务端与账单后再续跑")
    ledger = Ledger(job / "ledger.json", plan["limits"])
    client = Client(secret, ledger, plan["prices"])
    posts, comments, hot = [], [], []
    try:
        if plan["task"] == "search":
            for platform in plan["platforms"]:
                remaining = plan["limits"]["max_posts"] - len(posts)
                if remaining <= 0:
                    break
                posts += collect_pages(client, job, platform, "search", plan["query"], plan["pages_per_source"], remaining, manifest)
                if platform == "douyin" and plan["include_hot"]:
                    hot += collect_pages(client, job, platform, "hot", plan["query"], 1, plan["limits"]["max_posts"], manifest)
        else:
            platform = platform_for(plan["url"])
            if platform not in plan["platforms"]:
                raise ValueError("链接平台不在计划内")
            if plan["task"] == "account":
                if is_video_link(platform, plan["url"]):
                    author_path = job / "raw" / "seed-video-detail.json"
                    if author_path.exists():
                        payload = read_json(author_path)
                    else:
                        payload = client.request(platform, "detail", params(platform, "detail", plan["url"], 1))
                        atomic_json(author_path, payload)
                    subject = author_from_detail(platform, payload)
                    if not subject:
                        raise ValueError("视频详情未返回可核实的作者 ID，已停止账号扩展")
                else:
                    subject = account_subject(platform, plan["url"])
                posts = collect_pages(client, job, platform, "account", subject, plan["pages_per_source"], plan["limits"]["max_posts"], manifest)
            else:
                posts = collect_pages(client, job, platform, "detail", plan["url"], 1, 1, manifest)
        if plan["comments"]:
            for post in dedupe(posts)[:plan["limits"]["max_comment_pages"]]:
                if post.get("post_id"):
                    comments += collect_pages(client, job, post["platform"], "comments", post["post_id"], 1, 100, manifest)
    except (BudgetError, RuntimeError, ValueError) as exc:
        manifest["errors"].append(str(exc))
        manifest["status"] = "partial"
    start = instant(plan.get("start_utc"))
    end = instant(plan.get("end_utc"))
    fresh, historical_hot, undated = [], [], []
    for post in dedupe(posts + hot):
        published = instant(post.get("published_at"))
        if start and end:
            if published and start <= published <= end:
                fresh.append(post)
            elif not published:
                undated.append(post)
        else:
            fresh.append(post)
    for post in dedupe(hot):
        published = instant(post.get("published_at"))
        if not (published and start and end and start <= published <= end):
            historical_hot.append(post)
    enriched = with_baselines([classify(p, **plan["thresholds"]) for p in fresh])
    counts = write_tables(job / "normalized", {"posts": enriched, "comments": comments, "hot_reference": historical_hot, "undated": undated})
    atomic_json(job / "analysis_input.json", {"task": plan["task"], "scope": {k: plan[k] for k in ("platforms", "timezone", "start_utc", "end_utc", "query", "url")},
        "coverage": {"sources": {k: {"pages": v["page"]-1, "raw_count": len(v["rows"]), "stop": v["stop"]} for k, v in manifest["sources"].items()}, "counts": counts},
        "posts": enriched, "comments": comments, "historical_hot": historical_hot, "undated": undated, "limitations": manifest["errors"]})
    manifest["status"] = "awaiting_analysis" if not manifest["errors"] else "partial_awaiting_analysis"
    atomic_json(manifest_path, manifest)
    print_json({"job": str(job), "status": manifest["status"], "counts": counts, "errors": manifest["errors"]})


def resume(args):
    manifest = read_json(Path(args.job) / "manifest.json")
    if not manifest:
        raise ValueError("manifest 不存在")
    plan_path = Path(args.job) / "plan.json"
    atomic_json(plan_path, manifest["plan"])
    args.plan, args.out = str(plan_path), args.job
    return run(args)


def import_data(args):
    source = Path(args.input)
    if source.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif source.suffix.lower() == ".json":
        data = read_json(source)
        rows = data if isinstance(data, list) else data.get("posts", [])
    elif source.suffix.lower() == ".csv":
        with source.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    elif source.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(source, read_only=True, data_only=True)
        values = wb.active.values
        headers = next(values)
        rows = [dict(zip(headers, values)) for values in values]
    else:
        raise ValueError("支持 CSV/JSON/JSONL/XLSX")
    headers = set().union(*(row.keys() for row in rows)) if rows else set()
    mapping = {field: [alias for alias in aliases if alias in headers] for field, aliases in ALIASES.items()}
    ambiguous = {field: found for field, found in mapping.items() if len(found) > 1}
    if ambiguous:
        print_json({"status": "mapping_ambiguous", "candidates": ambiguous})
        return
    posts = []
    for row in rows:
        mapped = {field: row.get(matches[0]) if matches else None for field, matches in mapping.items()}
        for field in ("followers", "views", "likes", "comments", "saves", "shares"):
            mapped[field] = count(mapped[field])
        mapped["platform"] = mapped["platform"] or args.platform
        mapped["observed_at"] = now()
        mapped["source_file"] = str(source.resolve())
        mapped["metric_source"] = "user_import"
        mapped["source_reliability"] = "verified_import" if args.verified_import else "unverified_import"
        posts.append(mapped)
    cfg = config(args.config)
    posts = with_baselines([classify(row, cfg["followers_max"], cfg["views_min"], cfg["interaction_min"]) for row in dedupe(posts)])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / "analysis_input.json", {"task": "import", "scope": {"source": str(source.resolve())}, "coverage": {"counts": {"posts": len(posts)}, "mapping": mapping}, "posts": posts, "comments": [], "limitations": []})
    counts = write_tables(out / "normalized", {"posts": posts})
    atomic_json(out / "manifest.json", {"status": "awaiting_analysis", "task": "import", "source": str(source.resolve()), "mapping": mapping})
    print_json({"status": "awaiting_analysis", "mapping": mapping, "counts": counts, "job": str(out.resolve())})


def media(args):
    job = Path(args.job).resolve()
    data = read_json(job / "analysis_input.json")
    manifest = read_json(job / "manifest.json")
    if not data or not manifest:
        raise ValueError("任务目录缺少采集状态")
    if not args.authorize:
        raise ValueError("媒体详情可能收费，需要 --authorize 与有效预算授权")
    cfg = config(args.config)
    secret, _ = key(cfg, args.key_file)
    if not secret:
        raise ValueError("TikHub Key 未配置")
    plan = manifest.get("plan", {})
    limits = plan.get("limits", cfg)
    if limits.get("max_cost_estimate") is None and not args.authorize_unknown_prices:
        raise ValueError("未知单价须明确允许按请求数控制")
    from media import process
    ledger = Ledger(job / "ledger.json", limits)
    client = Client(secret, ledger, plan.get("prices", cfg.get("endpoint_prices_usd", {})))
    result = process(job, data.get("posts", []), client, ledger, limit=min(args.limit, limits["max_posts"]), asr=args.asr,
                     asr_authorized=args.authorize_asr, max_duration=limits.get("max_asr_duration", 0),
                     asr_price_per_minute=cfg.get("asr_price_per_minute_usd"))
    print_json({"media_items": len(result["items"]), "status": {k: x["status"] for k, x in result["items"].items()}})


def validate(args):
    job = Path(args.job)
    data = read_json(job / "analysis_input.json")
    if not data:
        raise ValueError("analysis_input.json 不存在")
    required = {"search": ["topics.md", "topics.json"], "account": ["account-report.md"], "video": ["video-report.md"], "import": ["account-report.md"]}.get(data["task"], [])
    if args.scene == "script":
        required = ["patterns.json", "script.txt", "script-check.md"]
    missing = [name for name in required if not (job / "analysis" / name).exists()]
    known = {p.get("url") for p in data.get("posts", [])} | {p.get("url") for p in data.get("historical_hot", [])}
    known_ids = {str(p.get("post_id")) for p in data.get("posts", [])}
    issues = []
    for name in required:
        file = job / "analysis" / name
        if file.exists():
            text = file.read_text(encoding="utf-8")
            if not text.strip():
                issues.append(f"{name} 为空")
            if name == "script.txt" and "\n" in text.strip():
                issues.append("script.txt 不是一整段")
            if name.endswith("report.md") or name == "topics.md":
                if data.get("posts") and not any(url and url in text for url in known):
                    issues.append(f"{name} 缺少可解析的作品链接")
    topics_path = job / "analysis" / "topics.json"
    if topics_path.exists():
        topics = read_json(topics_path)
        if not isinstance(topics, list):
            issues.append("topics.json 必须是数组")
        else:
            for index, topic in enumerate(topics, 1):
                if not topic.get("angle") or not topic.get("audience_problem"):
                    issues.append(f"选题 {index} 缺少原创角度或受众问题")
                if not topic.get("evidence_urls") or any(url not in known for url in topic["evidence_urls"]):
                    issues.append(f"选题 {index} 证据链接无法回指作品")
    patterns_path = job / "analysis" / "patterns.json"
    if args.scene == "script" and patterns_path.exists():
        patterns = read_json(patterns_path)
        if not isinstance(patterns, list):
            issues.append("patterns.json 必须是数组")
        else:
            for index, pattern in enumerate(patterns, 1):
                support = set(map(str, pattern.get("support_post_ids", [])))
                if not support.issubset(known_ids):
                    issues.append(f"模式 {index} 包含未知作品")
                if pattern.get("repeated") and len(support) < 3:
                    issues.append(f"模式 {index} 不足三条独立作品，不可称重复")
    status = "complete" if not missing and not issues else "awaiting_analysis"
    manifest = read_json(job / "manifest.json", {})
    manifest["status"] = status
    atomic_json(job / "manifest.json", manifest)
    print_json({"status": status, "missing": missing, "issues": issues})


def export(args):
    job = Path(args.job)
    data = read_json(job / "analysis_input.json")
    if not data:
        raise ValueError("analysis_input.json 不存在")
    tables = {"posts": data.get("posts", []), "comments": data.get("comments", [])}
    for filename, sheet in (("topics.json", "topics"), ("competitors.json", "competitors")):
        path = job / "analysis" / filename
        if path.exists():
            rows = read_json(path)
            if not isinstance(rows, list):
                raise ValueError(f"{filename} 必须是数组")
            tables[sheet] = rows
    counts = write_tables(job / "normalized", tables)
    print_json({"job": str(job.resolve()), "counts": counts})


def package(args):
    target = Path(args.out)
    allowed = [
        "SKILL.md", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VERIFICATION.md", "config.example.json", "requirements.txt",
        "scripts/bobo_media.py", "scripts/core.py", "scripts/provider.py", "scripts/media.py",
        "references/workflow.md", "references/topic.md", "references/account.md", "references/script.md", "references/routes.md",
        "templates/profile.example.json", "templates/topics.example.json", "templates/report.md", "templates/script-check.md",
        "examples/prompts.md", "tests/test_core.py",
        "vendor/shengjiang/LICENSE", "vendor/shengjiang/tikhub_request.py", "vendor/shengjiang/video_download_transcribe.py", "vendor/shengjiang/volc_auc.py",
        "vendor/konglong/LICENSE", "vendor/konglong/tikhub_mcp.py",
    ]
    files = [ROOT / name for name in allowed]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for file in files:
            z.write(file, "bobo-media-ops/" + str(file.relative_to(ROOT)))
    print_json({"zip": str(target.resolve()), "files": len(files)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--key-file")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("setup"); s.add_argument("--timezone"); s.add_argument("--platforms"); s.add_argument("--save-key", action="store_true")
    s = sub.add_parser("doctor"); s.add_argument("--online", action="store_true")
    s = sub.add_parser("capabilities"); s.add_argument("--online", action="store_true")
    s = sub.add_parser("plan"); s.add_argument("task", choices=("search", "account", "video")); s.add_argument("--query"); s.add_argument("--url"); s.add_argument("--platforms"); s.add_argument("--pages", type=int); s.add_argument("--max-requests", type=int); s.add_argument("--max-posts", type=int); s.add_argument("--max-comment-pages", type=int); s.add_argument("--followers-max", type=int); s.add_argument("--views-min", type=int); s.add_argument("--hot", action="store_true"); s.add_argument("--comments", action=argparse.BooleanOptionalAction, default=None); s.add_argument("--media-limit", type=int, default=0); s.add_argument("--out")
    for name in ("run", "resume"):
        s = sub.add_parser(name); s.add_argument("--plan" if name == "run" else "--job", required=True); s.add_argument("--out"); s.add_argument("--authorize", action="store_true"); s.add_argument("--authorize-unknown-prices", action="store_true")
    s = sub.add_parser("import"); s.add_argument("--input", required=True); s.add_argument("--platform", default="unknown"); s.add_argument("--verified-import", action="store_true"); s.add_argument("--out", required=True)
    s = sub.add_parser("media"); s.add_argument("--job", required=True); s.add_argument("--limit", type=int, default=3); s.add_argument("--authorize", action="store_true"); s.add_argument("--authorize-unknown-prices", action="store_true"); s.add_argument("--asr", action="store_true"); s.add_argument("--authorize-asr", action="store_true")
    s = sub.add_parser("validate"); s.add_argument("--job", required=True); s.add_argument("--scene", choices=("research", "script"), default="research")
    s = sub.add_parser("export"); s.add_argument("--job", required=True)
    s = sub.add_parser("package"); s.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        {"setup": setup, "doctor": doctor, "capabilities": capabilities, "plan": make_plan, "run": run, "resume": resume, "import": import_data, "media": media, "validate": validate, "export": export, "package": package}[args.command](args)
    except (ValueError, BudgetError, OSError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
