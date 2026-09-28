"""Guarded media and transcript work using Shengjiang parsers and Volc AUC."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from core import atomic_json, now, public_url, read_json
from provider import Client, Ledger, UnknownOutcome

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "shengjiang"))
import video_download_transcribe as upstream_video
import volc_auc


def safe_download(url: str, output: Path):
    public_url(url)
    class CheckedRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            public_url(newurl)
            return super().redirect_request(request, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(CheckedRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    temp = output.with_suffix(".part")
    try:
        with opener.open(request, timeout=60) as response, temp.open("wb") as f:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise ValueError("媒体超过 512 MiB 安全上限")
                f.write(chunk)
        if not upstream_video.is_mp4(temp):
            raise ValueError("响应不是 MP4")
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)


def duration_seconds(video: Path) -> int | None:
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)], capture_output=True, text=True)
    try:
        return int(float(result.stdout.strip()) + 0.999) if result.returncode == 0 else None
    except ValueError:
        return None


def process(job: Path, rows: list[dict], client: Client, ledger: Ledger, *, limit: int, asr: bool, asr_authorized: bool, max_duration: int, asr_price_per_minute: float | None):
    state_path = job / "media-manifest.json"
    state = read_json(state_path, {"items": {}, "asr_seconds_reserved": 0})
    for row in rows[:limit]:
        platform, url = row.get("platform"), row.get("url")
        if platform not in ("douyin", "xiaohongshu") or not url:
            continue
        key = f"{platform}:{row.get('post_id') or url}"
        item = state["items"].setdefault(key, {"status": "pending", "url": url, "platform": platform})
        if item.get("status") == "done":
            continue
        if item.get("status") in ("asr_submission_uncertain", "request_uncertain"):
            continue
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(row.get("post_id") or "item"))[:60]
        folder = job / "media" / f"{platform}-{safe_id}"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            raw_path = folder / "detail.raw.json"
            if raw_path.exists():
                payload = read_json(raw_path)
            else:
                args = {"share_url": url} if platform == "douyin" else {"share_text": url}
                payload = client.request(platform, "detail", args)
                atomic_json(raw_path, payload)
            record = upstream_video.extract_record(platform, payload, url)
            item.update({"status": "detail", "title": record.title, "post_id": record.content_id})
            atomic_json(state_path, state)
            video = folder / "video.mp4"
            if not upstream_video.is_mp4(video):
                if not record.video_url:
                    raise ValueError("详情无可用视频地址")
                safe_download(record.video_url, video)
            item["video_file"] = str(video.relative_to(job))
            item["status"] = "downloaded"
            atomic_json(state_path, state)
            if record.official_transcript:
                transcript = folder / "transcript.txt"
                transcript.write_text(record.official_transcript.strip() + "\n", encoding="utf-8")
                item.update({"status": "done", "transcript_source": "platform_explicit_subtitle_or_author_text", "transcript_file": str(transcript.relative_to(job))})
                atomic_json(state_path, state)
                continue
            if not asr:
                item.update({"status": "pending_asr", "transcript_source": None})
                atomic_json(state_path, state)
                continue
            if not asr_authorized:
                raise ValueError("火山 ASR 需要单独费用授权")
            credentials, _ = volc_auc.resolve_credentials()
            if not credentials["appid"] or not credentials["token"]:
                item.update({"status": "pending_asr", "blocker": "Volc AUC 凭据缺失"})
                atomic_json(state_path, state)
                continue
            duration = duration_seconds(video)
            if duration is None or max_duration <= 0:
                item.update({"status": "pending_asr", "blocker": "需 ffprobe 与 ASR 时长预算"})
                atomic_json(state_path, state)
                continue
            if state["asr_seconds_reserved"] + duration > max_duration:
                item.update({"status": "pending_asr", "blocker": "max_asr_duration reached"})
                atomic_json(state_path, state)
                continue
            if not record.asr_url:
                item.update({"status": "pending_asr", "blocker": "无远程音频地址；未自动开放本地目录"})
                atomic_json(state_path, state)
                continue
            public_url(record.asr_url)
            cluster = item.get("asr_cluster") or os.environ.get("VOLC_ASR_CLUSTER", volc_auc.DEFAULT_CLUSTER)
            if not item.get("asr_task_id"):
                if item.get("status") == "asr_submission_uncertain":
                    continue
                estimate = duration / 60 * asr_price_per_minute if asr_price_per_minute is not None else None
                slot = ledger.charge("volc_auc/submit", estimate, "asr")
                state["asr_seconds_reserved"] += duration
                item["status"] = "asr_submission_uncertain"
                atomic_json(state_path, state)
                try:
                    response = volc_auc._submit(audio_url=record.asr_url, audio_format=record.asr_format, credentials=credentials, cluster=cluster, uid=f"bobo-{platform}-{safe_id}", timeout=60)
                except Exception:
                    ledger.status(slot, "unknown")
                    raise RuntimeError("ASR 提交状态不明；先核对服务端，不能自动重复提交") from None
                ledger.status(slot, "responded")
                task_id = str((response.get("resp") or {}).get("id") or "")
                if not task_id:
                    raise RuntimeError("ASR 未返回任务 ID")
                item.update({"asr_task_id": task_id, "asr_cluster": cluster, "status": "asr_submitted"})
                atomic_json(state_path, state)
            slot = ledger.charge("volc_auc/query", 0.0, "asr")
            query = volc_auc._post_json(f"{os.environ.get('VOLC_ASR_SERVICE_URL', volc_auc.DEFAULT_SERVICE_URL).rstrip('/')}/query", {"appid": credentials["appid"], "token": credentials["token"], "cluster": cluster, "id": item["asr_task_id"]}, credentials["token"], timeout=60)
            ledger.status(slot, "responded")
            response = query.get("resp") or {}
            if str(response.get("code")) == "1000" and response.get("text"):
                transcript = folder / "transcript.txt"
                transcript.write_text(str(response["text"]).strip() + "\n", encoding="utf-8")
                atomic_json(folder / "transcript.raw.json", query)
                item.update({"status": "done", "transcript_source": "volcengine_auc", "transcript_file": str(transcript.relative_to(job))})
            elif str(response.get("code")) not in ("1000", "2000", "2001"):
                item.update({"status": "failed_asr", "blocker": "ASR 服务端失败"})
            else:
                item["status"] = "asr_pending"
            atomic_json(state_path, state)
        except Exception as exc:
            if isinstance(exc, UnknownOutcome):
                item["status"] = "request_uncertain"
            item["error"] = str(exc) if isinstance(exc, ValueError) and str(exc) in ("详情无可用视频地址", "响应不是 MP4", "媒体超过 512 MiB 安全上限") else type(exc).__name__
            atomic_json(state_path, state)
    return state
