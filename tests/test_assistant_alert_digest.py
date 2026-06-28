"""Tests for Assistant alert engine and digest filtering."""

import unittest

from src.assistant.alert import AlertEngine
from src.assistant.digest import filter_messages, build_digest_prompt, generate_memory_update_prompt
from src.assistant.config import AssistantConfig, AlertGroup, DigestGroup, GroupProfile
from src.assistant.outbox import Outbox


class TestAlertEngine(unittest.TestCase):

    def test_keyword_match(self):
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [
            AlertGroup(group_name="抢单群A", keywords=["派单", "急单"], enabled=True),
        ]
        outbox = Outbox()
        engine = AlertEngine(cfg, outbox)

        msg = {
            "group_name": "抢单群A",
            "sender_name": "张三",
            "content": "急单！谁接 报价500 明天就要",
            "timestamp": 1700000000,
        }
        nid = engine.check(msg)
        self.assertIsNotNone(nid)

    def test_keyword_case_insensitive(self):
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [
            AlertGroup(group_name="测试群", keywords=["需求", "报价"], enabled=True),
        ]
        outbox = Outbox()
        engine = AlertEngine(cfg, outbox)

        msg = {
            "group_name": "测试群",
            "sender_name": "李四",
            "content": "这个项目的报价是5000",
            "timestamp": 1700000000,
        }
        nid = engine.check(msg)
        self.assertIsNotNone(nid)

    def test_no_match(self):
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [
            AlertGroup(group_name="抢单群A", keywords=["派单"], enabled=True),
        ]
        outbox = Outbox()
        engine = AlertEngine(cfg, outbox)

        msg = {
            "group_name": "抢单群A",
            "sender_name": "张三",
            "content": "哈哈 今天天气真好",
            "timestamp": 1700000000,
        }
        nid = engine.check(msg)
        self.assertIsNone(nid)

    def test_disabled_group(self):
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [
            AlertGroup(group_name="抢单群A", keywords=["派单"], enabled=False),
        ]
        outbox = Outbox()
        engine = AlertEngine(cfg, outbox)

        msg = {
            "group_name": "抢单群A",
            "content": "急单派单！",
        }
        nid = engine.check(msg)
        self.assertIsNone(nid)

    def test_assistant_disabled(self):
        cfg = AssistantConfig(assistant_enabled=False)
        outbox = Outbox()
        engine = AlertEngine(cfg, outbox)

        msg = {"group_name": "抢单群A", "content": "派单！"}
        nid = engine.check(msg)
        self.assertIsNone(nid)


class TestDigestFiltering(unittest.TestCase):

    def test_filter_noise_replies(self):
        msgs = [
            {"content": "收到", "sender_name": "A"},
            {"content": "好的", "sender_name": "B"},
            {"content": "哈哈", "sender_name": "C"},
            {"content": "这是一个有意义的讨论", "sender_name": "D"},
        ]
        result = filter_messages(msgs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "这是一个有意义的讨论")

    def test_filter_system_messages(self):
        msgs = [
            {"content": "张三加入了群聊"},
            {"content": "李四退出了群聊"},
            {"content": "有人修改群名为'新群名'"},
            {"content": "正常消息"},
        ]
        result = filter_messages(msgs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "正常消息")

    def test_filter_too_short(self):
        msgs = [
            {"content": "a"},
            {"content": "这是一条足够长的正常消息"},
        ]
        result = filter_messages(msgs)
        self.assertEqual(len(result), 1)

    def test_filter_ignore_keywords(self):
        msgs = [
            {"content": "这是广告推广信息"},
            {"content": "正常讨论"},
        ]
        result = filter_messages(msgs, ignore_keywords=["广告"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "正常讨论")

    def test_filter_placeholder(self):
        msgs = [
            {"content": "[图片]"},
            {"content": "[语音]"},
            {"content": "正常消息"},
        ]
        result = filter_messages(msgs)
        self.assertEqual(len(result), 1)

    def test_build_digest_prompt(self):
        dg = DigestGroup(
            group_name="测试群",
            schedule=["12:00"],
            profile=GroupProfile(
                purpose="测试",
                description="这是测试群",
                focus=["重点"],
                ignore=["闲聊"],
                style="偏极简",
            ),
        )
        msgs = [
            {"sender_name": "A", "content": "好消息", "timestamp": 1700000000},
            {"sender_name": "B", "content": "什么消息", "timestamp": 1700000100},
        ]
        prompt = build_digest_prompt(dg, msgs)
        self.assertIn("测试群", prompt)
        self.assertIn("好消息", prompt)
        self.assertIn("什么消息", prompt)
        self.assertIn("偏极简", prompt)
        self.assertIn("重点", prompt)

    def test_memory_update_prompt(self):
        prompt = generate_memory_update_prompt("旧记忆", "新摘要内容")
        self.assertIn("旧记忆", prompt)
        self.assertIn("新摘要内容", prompt)
        self.assertIn("500", prompt)


if __name__ == "__main__":
    unittest.main()
