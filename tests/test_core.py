import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from core import classify, count, dedupe, normalize, public_url, safe_cell, today_window, with_baselines, write_tables
from provider import BudgetError, Ledger, collect_pages, pagination


class CoreTests(unittest.TestCase):
    def test_today_in_shanghai_is_not_utc_midnight(self):
        start, end = today_window("Asia/Shanghai", datetime(2026, 9, 27, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(start, "2026-09-26T16:00:00+00:00")
        self.assertEqual(end, "2026-09-27T04:00:00+00:00")

    def test_counts_and_missing_semantics(self):
        self.assertEqual(count("1.2万"), 12000)
        self.assertEqual(count("2亿"), 200000000)
        self.assertEqual(count("0"), 0)
        self.assertIsNone(count("-"))
        row = classify({"followers": 9000, "views": None, "likes": 2000, "comments": None}, 10000, 200000)
        self.assertEqual(row["classification"], "低粉高互动候选")
        self.assertIsNone(row["engagement_rate"])
        self.assertEqual(classify({"followers": None, "views": 300000}, 10000, 200000)["classification"], "数据不足")
        self.assertEqual(classify({"followers": 9000, "views": 300000, "source_reliability": "verified_import"}, 10000, 200000)["classification"], "低粉高播放已验证样本")
        self.assertIsNone(classify({"followers": 0, "views": 100000}, 10000, 200000)["view_follower_ratio"])

    def test_baseline_excludes_target_and_future_and_marks_small_sample(self):
        rows = [dict(platform="douyin", author_id="a", post_id=str(n), published_at=f"2026-09-{n:02d}T00:00:00+00:00", views=100) for n in range(1, 12)]
        rows.append(dict(platform="xiaohongshu", author_id="a", post_id="x", published_at="2026-09-01T00:00:00+00:00", views=999999))
        result = with_baselines(rows)
        self.assertEqual(result[10]["baseline_sample_size"], 10)
        self.assertEqual(result[10]["relative_performance"], 1)
        self.assertEqual(result[10]["baseline_confidence"], "directional")
        self.assertIsNone(result[0]["relative_performance"])
        rows[10]["views"] = 400
        rows[10]["classification"] = "其他样本"
        self.assertEqual(with_baselines(rows)[10]["classification"], "相对账号基线异常候选")

    def test_zero_follower_is_not_lost_in_normalization(self):
        row = normalize({"aweme_id": "1", "create_time": 1780000000, "author": {"follower_count": 0}, "statistics": {"play_count": 100}}, "douyin")
        self.assertEqual(row["followers"], 0)
        self.assertEqual(row["views"], 100)

    def test_duplicate_work_with_urls_and_missing_ids(self):
        rows = [{"platform": "douyin", "post_id": "123", "url": "https://v.douyin.com/short"}, {"platform": "douyin", "post_id": "123", "url": "https://www.douyin.com/video/123"}]
        self.assertEqual(len(dedupe(rows)), 1)
        self.assertEqual(len(dedupe([{"platform": "unknown"}, {"platform": "unknown"}])), 2)

    def test_pagination_repeats_stop_without_extra_request(self):
        class Fake:
            def __init__(self): self.calls = 0
            def request(self, *_):
                self.calls += 1
                return {"data": {"data": {"business_data": [{"aweme_info": {"aweme_id": str(self.calls), "create_time": 1780000000}}], "cursor": 20, "search_id": "x"}}}
        with tempfile.TemporaryDirectory() as d:
            state = {"sources": {}}
            rows = collect_pages(Fake(), Path(d), "douyin", "search", "WorkBuddy", 10, 100, state)
            self.assertEqual(len(rows), 2)
            self.assertEqual(next(iter(state["sources"].values()))["stop"], "repeated_cursor")

    def test_ledger_enforces_request_and_unknown_price(self):
        with tempfile.TemporaryDirectory() as d:
            limits = {"max_requests": 1, "max_cost_estimate": None}
            ledger = Ledger(Path(d) / "ledger.json", limits)
            ledger.charge("/a", None)
            with self.assertRaises(BudgetError): ledger.charge("/a", None)
            limits["max_requests"] = 2
            limits["max_cost_estimate"] = 1
            with self.assertRaises(BudgetError): ledger.charge("/b", None)

    def test_private_media_address_and_formula_input(self):
        with self.assertRaises(ValueError): public_url("https://127.0.0.1/file.mp4")
        self.assertEqual(safe_cell("=HYPERLINK(\"x\")")[0], "'")

    def test_csv_jsonl_xlsx_counts_and_chinese(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            counts = write_tables(out, {"posts": [{"title": "中文标题", "views": 0}, {"title": "=1+1", "views": None}]})
            self.assertEqual(counts["posts"], 2)
            self.assertEqual(len((out / "posts.jsonl").read_text().splitlines()), 2)
            with (out / "posts.csv").open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["title"], "中文标题")
            self.assertEqual(rows[1]["title"], "'=1+1")
            try:
                from openpyxl import load_workbook
                self.assertEqual(load_workbook(out / "research.xlsx", read_only=True).active.max_row, 3)
            except ImportError:
                self.assertIn("xlsx", counts)

    def test_cli_plan_offline_and_import(self):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "XDG_CONFIG_HOME": d}
            plan = Path(d) / "plan.json"
            command = [sys.executable, str(ROOT / "scripts" / "bobo_media.py"), "plan", "search", "--query", "WorkBuddy", "--out", str(plan)]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(plan.exists())
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "bobo_media.py"), "run", "--plan", str(plan)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            source = Path(d) / "source.csv"
            source.write_text("平台,作品id,标题,粉丝数,播放量\n抖音,a,示例,9,300000\n", encoding="utf-8")
            job = Path(d) / "job"
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "bobo_media.py"), "import", "--input", str(source), "--out", str(job)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((job / "analysis_input.json").read_text())["posts"][0]["classification"], "低粉高播放待核源候选")

    def test_validate_rejects_untraceable_topics_and_single_post_pattern(self):
        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            (job / "analysis").mkdir()
            (job / "analysis_input.json").write_text(json.dumps({"task": "search", "posts": [{"url": "https://example.com/p1", "post_id": "p1"}], "historical_hot": []}))
            (job / "analysis" / "topics.md").write_text("证据 https://example.com/p1")
            (job / "analysis" / "topics.json").write_text(json.dumps([{"angle": "原创角度", "audience_problem": "问题", "evidence_urls": ["https://example.com/fake"]}]))
            command = [sys.executable, str(ROOT / "scripts" / "bobo_media.py"), "validate", "--job", str(job)]
            output = subprocess.check_output(command, text=True)
            self.assertIn("证据链接无法回指", output)
            self.assertEqual(json.loads((job / "manifest.json").read_text())["status"], "awaiting_analysis")
            (job / "analysis" / "patterns.json").write_text(json.dumps([{"support_post_ids": ["p1"], "repeated": True}]))
            (job / "analysis" / "script.txt").write_text("完整口播一段。")
            (job / "analysis" / "script-check.md").write_text("核查事实")
            output = subprocess.check_output(command + ["--scene", "script"], text=True)
            self.assertIn("不足三条", output)

    def test_package_allowlist_excludes_private_data(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "share.zip"
            subprocess.check_call([sys.executable, str(ROOT / "scripts" / "bobo_media.py"), "package", "--out", str(target)], stdout=subprocess.DEVNULL)
            import zipfile
            with zipfile.ZipFile(target) as z:
                names = z.namelist()
            self.assertIn("bobo-media-ops/SKILL.md", names)
            self.assertFalse(any(".git/" in x or "config.json" in x and "example" not in x or "__pycache__" in x or ".key" in x for x in names))

    def test_media_resume_queries_existing_asr_task_without_submit(self):
        import media
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            job = Path(d)
            folder = job / "media" / "douyin-p1"
            folder.mkdir(parents=True)
            (folder / "detail.raw.json").write_text("{}")
            (folder / "video.mp4").write_bytes(b"0000ftyp" + b"0" * 20)
            (job / "media-manifest.json").write_text(json.dumps({"items": {"douyin:p1": {"status": "asr_pending", "url": "https://www.douyin.com/video/p1", "platform": "douyin", "asr_task_id": "existing-id", "asr_cluster": "volc_auc_meeting"}}, "asr_seconds_reserved": 60}))
            record = SimpleNamespace(title="样例", content_id="p1", video_url="https://cdn.example.com/video.mp4", official_transcript="", asr_url="https://cdn.example.com/audio.mp4", asr_format="mp4")
            ledger = Ledger(job / "ledger.json", {"max_requests": 2, "max_cost_estimate": None})
            fake_client = SimpleNamespace(key="test", request=lambda *_: self.fail("detail should be cached"))
            with patch.object(media.upstream_video, "extract_record", return_value=record), patch.object(media.volc_auc, "resolve_credentials", return_value=({"appid": "x", "token": "y"}, {})), patch.object(media.volc_auc, "_submit", side_effect=AssertionError("resubmitted")), patch.object(media.volc_auc, "_post_json", return_value={"resp": {"code": 1000, "text": "转写结果"}}), patch.object(media, "public_url", side_effect=lambda x: x), patch.object(media, "duration_seconds", return_value=60):
                result = media.process(job, [{"platform": "douyin", "post_id": "p1", "url": "https://www.douyin.com/video/p1"}], fake_client, ledger, limit=1, asr=True, asr_authorized=True, max_duration=120, asr_price_per_minute=0.1)
            self.assertEqual(result["items"]["douyin:p1"]["status"], "done")
            self.assertEqual((folder / "transcript.txt").read_text().strip(), "转写结果")


if __name__ == "__main__":
    unittest.main()
