import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


digest = load("digest", ROOT / "scripts" / "send_daily_digest.py")
fallback = load("fallback", ROOT / "scripts" / "check_delivery.py")
site = load("site", ROOT / "scripts" / "digest_site.py")


class DeliveryTests(unittest.TestCase):
    def test_marker_wins(self):
        target = datetime(2026, 7, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("sent", fallback.decide("sent", [], target))

    def test_sending_marker_waits(self):
        target = datetime(2026, 7, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("running", fallback.decide("sending", [], target))

    def test_active_run_prevents_duplicate(self):
        target = datetime(2026, 7, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
        runs = [{"createdAt": "2026-07-30T00:05:00Z", "status": "in_progress"}]
        self.assertEqual("running", fallback.decide("", runs, target))

    def test_missing_run_dispatches(self):
        target = datetime(2026, 7, 30, 18, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual("dispatch", fallback.decide("", [], target))

    def test_wechat_articles_do_not_collapse(self):
        a = digest.canonical_url("https://mp.weixin.qq.com/s?__biz=A&mid=1")
        b = digest.canonical_url("https://mp.weixin.qq.com/s?__biz=A&mid=2")
        self.assertNotEqual(a, b)

    def test_local_success_marker_blocks_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            marker.write_text('{"status":"sent"}', encoding="utf-8")
            self.assertTrue(digest.marker_is_sent(marker, "unused"))

    def test_pages_build_has_filters_archive_and_links(self):
        item = {"source": "机器之心", "source_class": "专业媒体", "title": "测试文章",
                "url": "https://example.com/a", "summary": "这是中文摘要。", "score": 90,
                "created_at": "2026-07-30T00:00:00Z", "daily_scope": True}
        funcs = {"editorial_fields": digest.editorial_fields, "editorial_type": digest.editorial_type,
                 "item_tags": digest.item_tags, "display_time": digest.display_time}
        with tempfile.TemporaryDirectory() as tmp:
            url, anchors = site.render_site([item], [], datetime(2026, 7, 30, 8, tzinfo=ZoneInfo("Asia/Shanghai")),
                                            "morning", "上午篇", tmp, "https://example.test/daily", funcs)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertIn("历史日报归档", page)
            self.assertIn("全部来源", page)
            self.assertIn("阅读原文", page)
            self.assertIn("测试文章", page)
            self.assertIn("下午篇尚未生成", page)
            self.assertTrue(url.endswith("2026-07-30-morning.html"))
            self.assertIn(item["url"], anchors)
            self.assertEqual(1, len(json.loads((Path(tmp) / "archive.json").read_text(encoding="utf-8"))))


if __name__ == "__main__":
    unittest.main()
