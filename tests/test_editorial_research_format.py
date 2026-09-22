"""Synthetic rendering tests; these fixtures are not publication-ready news."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publisher", ROOT / "scripts/publish_editorial_issue.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def example_issue():
    issue = json.loads((ROOT / "tests/fixtures/editorial_issue.json").read_text(encoding="utf-8"))
    while len(issue["stories"]) < 8:
        extra = copy.deepcopy(next(s for s in issue["stories"] if s["section"] == "think"))
        extra["source"]["url"] += f"/fixture-{len(issue['stories'])}"
        issue["stories"].append(extra)
    issue["format_version"] = 2
    issue["codex_quota_watch"] = {
        "confidence": "未核实", "summary": "测试示例，不代表真实额度消息；以账户用量页为准。",
        "checked_at": "2026-08-24T12:00:00+08:00",
        "sources": [{"name": "官方用量说明", "url": "https://learn.chatgpt.com/docs/pricing"}],
    }
    issue["research_title"] = "结构化输出仍需验证判断是否正确"
    issue["research_sources"] = [{"name": "测试研究原文", "url": "https://example.org/research"}]
    for index, story in enumerate(issue["stories"]):
        story["track"] = "application" if index < 5 else "capability"
        if index >= 6:
            story["paper_topic"] = "ai" if index == 6 else "embodied"
            story["source"]["type"] = "research"
    return issue


class ResearchFormatTests(unittest.TestCase):
    def test_v2_valid(self):
        self.assertEqual([], publisher.validate_issue(example_issue()))

    def test_missing_watch_and_bad_time_rejected(self):
        issue = example_issue()
        issue.pop("codex_quota_watch")
        self.assertIn("codex_quota_watch 必须是对象", publisher.validate_issue(issue))
        issue = example_issue()
        issue["codex_quota_watch"]["checked_at"] = "2026-08-23T12:00:00+08:00"
        self.assertTrue(any("核验时间" in e for e in publisher.validate_issue(issue)))

    def test_confidence_and_sources_rejected(self):
        issue = example_issue()
        issue["codex_quota_watch"]["confidence"] = "95%"
        issue["research_sources"][0]["url"] = "javascript:alert(1)"
        errors = publisher.validate_issue(issue)
        self.assertTrue(any("置信度" in e for e in errors))
        self.assertTrue(any("https" in e for e in errors))

    def test_ratio_and_paper_classification(self):
        issue = example_issue()
        issue["stories"][0]["track"] = "capability"
        self.assertTrue(any("55%" in e for e in publisher.validate_issue(issue)))
        issue = example_issue()
        issue["stories"][-1]["source"]["type"] = "media"
        self.assertTrue(any("research 原文" in e for e in publisher.validate_issue(issue)))

    def test_layout_all_subtitles_and_anchors(self):
        issue = example_issue()
        page_url = "https://example.com/daily/2026-08-24-morning.html"
        _, email = publisher.render_email(issue, page_url)
        with tempfile.TemporaryDirectory() as tmp:
            publisher.render_page(issue, "每日版", tmp)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
        for output in (email, page):
            positions = [output.index(v) for v in (issue["daily_quote"]["text"], "Codex 额度刷新消息置信度", "研究前言｜", issue["standfirst"], "本期目录")]
            self.assertEqual(sorted(positions), positions)
        for story in issue["stories"]:
            anchor = publisher.story_anchor(story)
            self.assertIn(f'href="{page_url}#{anchor}"', email)
            self.assertIn(f'href="#{anchor}"', page)
            self.assertIn(f'id="{anchor}"', page)
            self.assertIn(story["teaser"], email)
            self.assertIn(story["teaser"], page)

    def test_dynamic_header_escapes_html(self):
        issue = example_issue()
        issue["codex_quota_watch"]["summary"] = "<script>alert(1)</script>"
        issue["research_title"] = "<img src=x onerror=alert(1)>"
        quota, heading, _ = publisher.research_header(issue)
        self.assertNotIn("<script>", quota)
        self.assertNotIn("<img", heading)

    def test_legacy_issue_stays_compatible(self):
        issue = example_issue()
        issue.pop("format_version")
        issue.pop("codex_quota_watch")
        self.assertEqual([], publisher.validate_issue(issue))
        _, output = publisher.render_email(issue, "https://example.com")
        self.assertNotIn("Codex 额度刷新消息置信度", output)

    def test_cli_validate_only_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issue = root / "fixture.json"
            issue.write_text(json.dumps(example_issue(), ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/publish_editorial_issue.py"),
                       "--issue", str(issue), "--edition", "morning", "--now", "2026-08-24T12:00:00+08:00"]
            checked = subprocess.run(command + ["--validate-only"], check=True, capture_output=True, text=True)
            self.assertIn("EDITORIAL_VALID", checked.stdout)
            rendered = subprocess.run(command + ["--dry-run", "--output", str(root / "email.html"),
                                      "--docs-output", str(root / "docs")], check=True, capture_output=True, text=True)
            self.assertIn("DRY_RUN_OK", rendered.stdout)
            self.assertIn("本期目录", (root / "email.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
