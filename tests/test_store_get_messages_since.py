"""get_messages_since 的截断方向契约。

修复前 SQL 是 `ORDER BY timestamp ASC LIMIT ?`，窗口内消息数超过 limit 时
丢掉的是**最新**的消息 —— 忙群 24h 有 1037 条时只取到最早 500 条，
等于摘要了当天前半段、把刚刚发生的讨论全部漏掉。
"""
import os
import sqlite3
import tempfile
import unittest

from src.db.schema import initialize_db
from src.db.store import MessageStore

CHAT = "11111@chatroom"
OTHER = "22222@chatroom"


class TestGetMessagesSince(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "messages.db")
        self.conn = initialize_db(self.db_path)
        self.store = MessageStore(self.conn)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _insert(self, chat_id, ts, msg_id=None, content="消息内容"):
        self.conn.execute(
            """INSERT INTO messages
               (message_id, chat_id, sender_id, sender_name, content, msg_type, timestamp)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (msg_id or f"m{chat_id.split('@')[0]}_{ts:08d}",
             chat_id, f"wxid_{ts}", "某人", content, ts),
        )
        self.conn.commit()

    def _seed(self, count, chat_id=CHAT, start_ts=1):
        for i in range(count):
            self._insert(chat_id, start_ts + i)

    def test_returns_newest_n_when_over_limit(self):
        """1037 条 / limit=500 → 必须是最新的 500 条（ts 538..1037）。"""
        self._seed(1037)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=500)
        self.assertEqual(len(rows), 500)
        self.assertEqual(rows[0]["timestamp"], 538)
        self.assertEqual(rows[-1]["timestamp"], 1037)

    def test_newest_message_is_never_dropped(self):
        """回归护栏：窗口内最新一条必须在结果里（这正是修复前的 bug）。"""
        self._seed(1037)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=500)
        self.assertIn(1037, [r["timestamp"] for r in rows])
        self.assertNotIn(1, [r["timestamp"] for r in rows])

    def test_result_is_ascending(self):
        """调用方依赖时间正序：scheduler 的 raw[-unread:] 取尾部、prompt 按时间排列。"""
        self._seed(1037)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=500)
        stamps = [r["timestamp"] for r in rows]
        self.assertEqual(stamps, sorted(stamps))

    def test_under_limit_returns_all(self):
        self._seed(100)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=500)
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0]["timestamp"], 1)
        self.assertEqual(rows[-1]["timestamp"], 100)

    def test_window_bounds_respected(self):
        self._seed(100)
        rows = self.store.get_messages_since(CHAT, since_ts=20, until_ts=30, limit=500)
        self.assertEqual([r["timestamp"] for r in rows], list(range(20, 31)))

    def test_limit_applies_after_window_filter(self):
        """先按窗口过滤再截断：窗口内 50 条、limit=10 → 最新的 10 条。"""
        self._seed(100)
        rows = self.store.get_messages_since(CHAT, since_ts=20, until_ts=69, limit=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["timestamp"], 60)
        self.assertEqual(rows[-1]["timestamp"], 69)

    def test_same_second_tie_break_is_deterministic(self):
        """同一 timestamp 多条时两次调用结果完全一致（message_id 作 tie-break）。"""
        for mid in ("aaa", "bbb", "ccc", "ddd"):
            self._insert(CHAT, 500, msg_id=mid)
        first = self.store.get_messages_since(CHAT, since_ts=0, until_ts=999, limit=2)
        second = self.store.get_messages_since(CHAT, since_ts=0, until_ts=999, limit=2)
        self.assertEqual([r["message_id"] for r in first],
                         [r["message_id"] for r in second])
        self.assertEqual([r["message_id"] for r in first], ["ccc", "ddd"])

    def test_other_chat_not_leaked(self):
        self._seed(50, chat_id=CHAT)
        self._seed(50, chat_id=OTHER)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=500)
        self.assertEqual(len(rows), 50)
        self.assertTrue(all(r["chat_id"] == CHAT for r in rows))

    def test_until_ts_defaults_to_now(self):
        import time
        now = int(time.time())
        self._insert(CHAT, now - 10)
        self._insert(CHAT, now + 3600)  # 未来消息不该被默认窗口捞进来
        rows = self.store.get_messages_since(CHAT, since_ts=now - 60)
        self.assertEqual(len(rows), 1)

    def test_returns_dict_rows_with_expected_keys(self):
        self._seed(3)
        rows = self.store.get_messages_since(CHAT, since_ts=0, until_ts=99999, limit=10)
        self.assertIsInstance(rows[0], dict)
        for key in ("message_id", "chat_id", "sender_id", "sender_name",
                    "content", "msg_type", "timestamp"):
            self.assertIn(key, rows[0])


if __name__ == "__main__":
    unittest.main()
