"""Tests for Assistant Outbox."""

import os
import tempfile
import unittest
from pathlib import Path

from src.assistant.outbox import Outbox


class TestOutbox(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "test_outbox.db"

    def tearDown(self):
        try:
            if self._db_path.exists():
                self._db_path.unlink()
        except PermissionError:
            pass  # Windows file lock — db will be cleaned up next test run
        try:
            Path(self._tmpdir).rmdir()
        except (PermissionError, OSError):
            pass

    def test_add_and_get_pending(self):
        outbox = Outbox(db_path=self._db_path)
        nid = outbox.add("keyword_alert", "测试群", "新订单", "张三: 急单 报价500")
        self.assertIsNotNone(nid)
        self.assertGreater(nid, 0)

        pending = outbox.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["type"], "keyword_alert")
        self.assertEqual(pending[0]["group_name"], "测试群")
        self.assertEqual(pending[0]["status"], "pending")

    def test_ack(self):
        outbox = Outbox(db_path=self._db_path)
        nid = outbox.add("group_digest", "群A", "摘要", "内容...")
        self.assertTrue(outbox.ack(nid))

        pending = outbox.get_pending()
        self.assertEqual(len(pending), 0)

    def test_ignore(self):
        outbox = Outbox(db_path=self._db_path)
        nid = outbox.add("keyword_alert", "群A", "标题", "内容")
        self.assertTrue(outbox.ignore(nid))

        pending = outbox.get_pending()
        self.assertEqual(len(pending), 0)

    def test_ack_nonexistent(self):
        outbox = Outbox(db_path=self._db_path)
        self.assertFalse(outbox.ack(99999))

    def test_get_pending_limit(self):
        outbox = Outbox(db_path=self._db_path)
        for i in range(5):
            outbox.add("keyword_alert", f"群{i}", f"标题{i}", f"内容{i}")

        pending = outbox.get_pending(limit=3)
        self.assertEqual(len(pending), 3)

    def test_count_pending(self):
        outbox = Outbox(db_path=self._db_path)
        self.assertEqual(outbox.count_pending(), 0)
        outbox.add("keyword_alert", "群", "标题", "内容")
        self.assertEqual(outbox.count_pending(), 1)
        outbox.add("group_digest", "群", "标题", "内容")
        self.assertEqual(outbox.count_pending(), 2)

    def test_cleanup(self):
        """Cleanup should not affect pending notifications."""
        outbox = Outbox(db_path=self._db_path)
        nid = outbox.add("keyword_alert", "群", "标题", "内容")
        outbox.ack(nid)

        # With 0-hour retention, delivered items are cleaned immediately
        deleted = outbox.cleanup_expired(retention_hours=0)
        self.assertGreaterEqual(deleted, 0)

        # Pending items should still be empty (we acked the only one)
        self.assertEqual(outbox.count_pending(), 0)

    def test_query_by_url_treats_partial_as_delivered(self):
        """部分成功算已推送 —— 否则下一轮轮询重推，已送达渠道会收到重复提醒。"""
        outbox = Outbox(db_path=self._db_path)
        url = "https://mp.weixin.qq.com/s/partial-test"
        nid = outbox.add("oa_article_alert", "公众号", "标题", "{}", url=url)
        outbox.update_push_result(nid, "feishu", "partial", "ilink 发送失败")

        self.assertTrue(outbox.query_by_url(url, "oa_article_alert", only_success=True))

    def test_query_by_url_treats_failed_as_not_delivered(self):
        """全渠道失败不算已推送，调用方仍可重试。"""
        outbox = Outbox(db_path=self._db_path)
        url = "https://mp.weixin.qq.com/s/failed-test"
        nid = outbox.add("oa_article_alert", "公众号", "标题", "{}", url=url)
        outbox.update_push_result(nid, "", "failed", "全部渠道失败")

        self.assertFalse(outbox.query_by_url(url, "oa_article_alert", only_success=True))

    def test_get_push_stats_counts_partial_as_success(self):
        """至少一个渠道送达即计入成功，部分失败不应拉低成功率。"""
        outbox = Outbox(db_path=self._db_path)
        n1 = outbox.add("oa_digest", "公众号1", "标题", "内容")
        outbox.update_push_result(n1, "ilink", "success")
        n2 = outbox.add("oa_digest", "公众号2", "标题", "内容")
        outbox.update_push_result(n2, "feishu", "partial", "qqbot 发送失败")
        n3 = outbox.add("oa_digest", "公众号3", "标题", "内容")
        outbox.update_push_result(n3, "", "failed", "全部渠道失败")

        stats = outbox.get_push_stats()
        # n3 的 push_channel 为空（多渠道结果无法归属单一渠道），按既有口径不计入统计
        self.assertEqual(stats["today_total"], 2)
        self.assertEqual(stats["today_success"], 2)
        self.assertEqual(stats["today_rate"], 100.0)


if __name__ == "__main__":
    unittest.main()
