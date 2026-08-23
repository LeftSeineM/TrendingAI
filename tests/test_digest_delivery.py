import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "editorial_issue.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


editorial = load("editorial", ROOT / "scripts" / "publish_editorial_issue.py")
fallback = load("fallback", ROOT / "scripts" / "check_delivery.py")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.issue = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_marker_wins(self):
        target = datetime(2026, 8, 24, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("sent", fallback.decide("sent", [], target))

    def test_sending_marker_without_active_run_can_recover(self):
        target = datetime(2026, 8, 24, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("dispatch", fallback.decide("sending", [], target))

    def test_active_run_prevents_duplicate(self):
        target = datetime(2026, 8, 24, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        runs = [{"createdAt": "2026-08-24T00:05:00Z", "status": "in_progress"}]
        self.assertEqual("running", fallback.decide("", runs, target))

    def test_missing_run_would_dispatch_only_after_other_guards(self):
        target = datetime(2026, 8, 24, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("dispatch", fallback.decide("", [], target))

    def test_valid_editorial_issue_passes_strict_gate(self):
        self.assertEqual([], editorial.validate_issue(self.issue, "2026-08-24", "morning"))

    def test_generic_copy_is_rejected(self):
        issue = copy.deepcopy(self.issue)
        issue["stories"][0]["body"][0] += "这件事值得持续关注。"
        errors = editorial.validate_issue(issue, "2026-08-24", "morning")
        self.assertTrue(any("空泛表达" in error for error in errors))

    def test_weak_source_mix_is_rejected(self):
        issue = copy.deepcopy(self.issue)
        for index, story in enumerate(issue["stories"]):
            story["source"]["type"] = "media"
            story["source"]["url"] = f"https://example.com/{index}"
        errors = editorial.validate_issue(issue, "2026-08-24", "morning")
        self.assertIn("至少需要 2 条官方、第一手或研究原文，当前 0", errors)
        self.assertIn("至少需要 4 个不同来源域名，当前 1", errors)

    def test_local_success_marker_blocks_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            marker.write_text('{"status":"sent"}', encoding="utf-8")
            self.assertTrue(editorial.marker_is_sent(marker, "unused"))

    def test_recent_sending_lock_blocks_duplicate_but_stale_lock_recovers(self):
        now = datetime(2026, 8, 24, 1, 0, tzinfo=ZoneInfo("UTC"))
        recent = {"status": "sending", "started_at": "2026-08-24T00:40:00+00:00"}
        stale = {"status": "sending", "started_at": "2026-08-23T23:00:00+00:00"}
        self.assertTrue(editorial.sending_marker_is_fresh(recent, now))
        self.assertFalse(editorial.sending_marker_is_fresh(stale, now))

    def test_page_and_email_preserve_edited_prose_and_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = editorial.render_page(self.issue, "上午篇", tmp, "https://example.test/daily")
            subject, email = editorial.render_email(self.issue, url)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertEqual(self.issue["subject"], subject)
            self.assertIn("今天真正重要的三件事", email)
            self.assertIn("主编手记", page)
            self.assertIn(self.issue["stories"][0]["body"][0], page)
            self.assertIn(self.issue["stories"][0]["source"]["url"], page)
            self.assertNotIn("跑分", self.issue["standfirst"])
            self.assertTrue(url.endswith("2026-08-24-morning.html"))
            self.assertEqual(1, len(json.loads((Path(tmp) / "archive.json").read_text(encoding="utf-8"))))

    def test_workflow_uses_editorial_issue_and_has_no_ai_fallback(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-email.yml").read_text(encoding="utf-8")
        self.assertIn("publish_editorial_issue.py", workflow)
        self.assertNotIn("send_daily_digest.py", workflow)
        self.assertNotIn("AI_API_KEY", workflow)
        self.assertNotIn("falls back", workflow)

    def test_pages_redeploy_after_digest_workflow(self):
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_run:", workflow)
        self.assertIn('workflows: ["TrendingAI Daily Email"]', workflow)
        self.assertIn("workflow_run.conclusion == 'success'", workflow)


if __name__ == "__main__":
    unittest.main()
