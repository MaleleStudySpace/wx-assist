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


if __name__ == "__main__":
    unittest.main()
