"""关键词提醒的正则匹配（方案 A：/pattern/ 斜杠语法）。

设计要点（改动时必须守住）：
  1. 纯字面关键词的历史行为**逐字节不变**：大小写不敏感的子串包含；
  2. 正则**默认大小写敏感**，匹配的是**原始** content —— 若改成匹配
     content.lower()，`[A-Z]` / `\\b` 这类语义会永久失效；
  3. 非法正则被配置层拦住（不落盘）；手改配置塞进去的坏正则只跳过该条，
     不能让整组关键词失效、也不能每条消息刷异常；
  4. `keywords` 字段仍是纯字符串列表，下游推送 / 前端无需改动。
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.assistant.alert as alert_mod
import src.assistant.config as config_mod
from src.assistant.alert import AlertEngine
from src.assistant.config import (
    AssistantConfig,
    AlertGroup,
    REGEX_KEYWORD_MAX_LEN,
    is_regex_keyword,
    regex_keyword_pattern,
    validate_alert_keywords,
)


# ── 语法识别 ──────────────────────────────────────────────────────────

class TestRegexKeywordSyntax(unittest.TestCase):

    def test_recognises_slash_wrapped_entries(self):
        for kw in ("/a/", "/\\d+/", "/(?i)x/", "/a/b/", " /a/ "):
            with self.subTest(kw=kw):
                self.assertTrue(is_regex_keyword(kw))

    def test_leaves_literals_alone(self):
        for kw in ("", "/", "//", "/a", "a/", "abc", "/a/b", "派单", "/dev/null"):
            with self.subTest(kw=kw):
                self.assertFalse(is_regex_keyword(kw))

    def test_non_string_entries_are_not_regex(self):
        """手改配置塞进数字/对象时不能抛异常 —— 那会挂在 Bot 启动阶段。"""
        for kw in (123, None, ["/a/"], {"p": "x"}):
            with self.subTest(kw=kw):
                self.assertFalse(is_regex_keyword(kw))
                self.assertEqual(regex_keyword_pattern(kw), "")

    def test_pattern_extraction(self):
        self.assertEqual(regex_keyword_pattern("/abc/"), "abc")
        self.assertEqual(regex_keyword_pattern(" /\\d{2,}元/ "), "\\d{2,}元")
        self.assertEqual(regex_keyword_pattern("/a/b/"), "a/b")
        self.assertEqual(regex_keyword_pattern("abc"), "")


# ── 配置层校验（配置 API / Agent 工具共用） ────────────────────────────

class TestValidateAlertKeywords(unittest.TestCase):

    def test_literal_keywords_are_unrestricted(self):
        """字面关键词不做限制 —— 保持既有行为，不引入新的保存失败。"""
        self.assertEqual(validate_alert_keywords(["派单", "急单", "BUG", "//"]), "")
        self.assertEqual(validate_alert_keywords([]), "")
        self.assertEqual(validate_alert_keywords(None), "")

    def test_valid_regex_passes(self):
        self.assertEqual(validate_alert_keywords(["/\\d{2,}元/", "/(?i)urgent/"]), "")

    def test_invalid_regex_rejected(self):
        err = validate_alert_keywords(["/([a-z]+/"])
        self.assertIn("正则语法错误", err)
        self.assertIn("/([a-z]+/", err)

    def test_overlong_regex_rejected(self):
        err = validate_alert_keywords(["/" + "a" * (REGEX_KEYWORD_MAX_LEN + 1) + "/"])
        self.assertIn("正则过长", err)

    def test_boundary_length_passes(self):
        self.assertEqual(
            validate_alert_keywords(["/" + "a" * REGEX_KEYWORD_MAX_LEN + "/"]), "")

    def test_blank_and_non_string_rejected(self):
        self.assertIn("关键词不能为空", validate_alert_keywords(["   "]))
        self.assertIn("关键词不能为空", validate_alert_keywords([123]))


# ── 引擎：字面关键词回归 ──────────────────────────────────────────────

class _EngineCase(unittest.TestCase):
    """公共夹具：隔离磁盘状态，且不让命中真的往用户环境推送。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = patch.object(alert_mod, "_TRIGGERED_PATH",
                         Path(self._tmp.name) / "triggered.json")
        p.start()
        self.addCleanup(p.stop)
        # 空绑定 → 推送走 skipped 分支，测试不触碰真实渠道
        p2 = patch("src.im.targets.bound_push_targets", return_value=[])
        p2.start()
        self.addCleanup(p2.stop)

    def _engine(self, keywords, enabled=True):
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [AlertGroup(
            chat_id="g@chatroom", group_name="测试群",
            keywords=list(keywords), enabled=enabled,
        )]
        outbox = MagicMock()
        outbox.add.return_value = 1
        return AlertEngine(cfg, outbox), outbox

    @staticmethod
    def _msg(content, **kw):
        msg = {
            "chat_id": "g@chatroom", "group_name": "测试群",
            "sender_name": "张三", "content": content,
            "timestamp": int(time.time()),
        }
        msg.update(kw)
        return msg

    @staticmethod
    def _payload(outbox) -> dict:
        return json.loads(outbox.add.call_args.kwargs["content"])


class TestLiteralBehaviourUnchanged(_EngineCase):

    def test_substring_match_is_case_insensitive(self):
        engine, _ = self._engine(["BUG"])
        self.assertEqual(engine.check(self._msg("there is a bug here")), 1)

    def test_no_match_returns_none(self):
        engine, _ = self._engine(["派单"])
        self.assertIsNone(engine.check(self._msg("哈哈 今天天气真好")))

    def test_literal_only_config_compiles_nothing(self):
        engine, _ = self._engine(["派单", "//", "/dev/null"])
        self.assertEqual(engine._compiled, {})

    def test_unicode_slash_wrapped_literal_still_regex(self):
        """首尾斜杠必然按正则处理 —— 这是方案 A 的既定语法，需显式记录。"""
        engine, _ = self._engine(["/派单/"])
        self.assertIn("/派单/", engine._compiled)
        self.assertEqual(engine.check(self._msg("有个派单信息")), 1)


class TestRegexMatching(_EngineCase):

    def test_regex_matches(self):
        engine, _ = self._engine(["/\\d{2,}元/"])
        self.assertEqual(engine.check(self._msg("报价 500元")), 1)

    def test_regex_does_not_false_positive(self):
        engine, _ = self._engine(["/\\d{2,}元/"])
        self.assertIsNone(engine.check(self._msg("报价 5元")))

    def test_regex_is_case_sensitive_by_default(self):
        engine, _ = self._engine(["/BUG/"])
        self.assertIsNone(engine.check(self._msg("a bug here")))
        self.assertEqual(engine.check(self._msg("a BUG here")), 1)

    def test_inline_flag_enables_case_insensitive(self):
        engine, _ = self._engine(["/(?i)bug/"])
        self.assertEqual(engine.check(self._msg("a BUG here")), 1)

    def test_uppercase_char_class_not_broken_by_lowercasing(self):
        """回归护栏：正则匹配 content.lower() 会让 [A-Z] 永远匹配不上。"""
        engine, _ = self._engine(["/[A-Z]{3}\\d/"])
        self.assertEqual(engine.check(self._msg("订单 ABC1 已生成")), 1)

    def test_matching_uses_stripped_content(self):
        """匹配对象是 _strip_ids 之后的内容，与字面关键词一致。"""
        engine, _ = self._engine(["/wxid_[a-z0-9]+/"])
        self.assertIsNone(engine.check(self._msg("wxid_abc123 你好")))

    def test_literal_and_regex_coexist(self):
        engine, outbox = self._engine(["急单", "/\\d+元/"])
        self.assertEqual(engine.check(self._msg("急单 报价500元")), 1)
        self.assertEqual(self._payload(outbox)["keywords"], ["急单", "/\\d+元/"])

    def test_cooldown_applies_to_regex(self):
        engine, _ = self._engine(["/\\d+元/"])
        self.assertEqual(engine.check(self._msg("报价 500元")), 1)
        self.assertIsNone(engine.check(self._msg("报价 600元")))


class TestRegexDisplayAndPayload(_EngineCase):

    def test_display_shows_actual_hit_snippet(self):
        """只给 pattern 用户看不出为什么触发，必须带上实际命中片段。"""
        engine, outbox = self._engine(["/\\d+元/"])
        engine.check(self._msg("今天的报价是500元，速联系"))
        display = self._payload(outbox)["display"]
        self.assertIn("‹500元›", display)
        self.assertIn("`/\\d+元/`", display)

    def test_display_unchanged_for_literal(self):
        engine, outbox = self._engine(["急单"])
        engine.check(self._msg("急单来了"))
        display = self._payload(outbox)["display"]
        self.assertIn("`急单`", display)
        self.assertNotIn("‹", display)

    def test_keywords_payload_stays_string_list(self):
        engine, outbox = self._engine(["/\\d+元/"])
        engine.check(self._msg("报价500元"))
        payload = self._payload(outbox)
        self.assertEqual(payload["keywords"], ["/\\d+元/"])
        self.assertTrue(all(isinstance(k, str) for k in payload["keywords"]))

    def test_hit_snippet_is_truncated(self):
        engine, outbox = self._engine(["/\\d+/"])
        engine.check(self._msg("单号 " + "1" * 60 + " 完成"))
        display = self._payload(outbox)["display"]
        # 只看「匹配关键词」这一段：消息正文本身也含长串 1，不能混在一起断言
        snippet_part = display.split("匹配关键词")[1]
        self.assertIn("…", snippet_part)
        self.assertIn("1" * 30, snippet_part)
        self.assertNotIn("1" * 31, snippet_part)


class TestRegexRuntimeSafety(_EngineCase):

    def test_invalid_regex_in_config_is_skipped_not_fatal(self):
        """手改 assistant_config.json 塞坏正则：只跳过该条，其余照常命中。"""
        with self.assertLogs("src.assistant.alert", level="WARNING") as cm:
            engine, _ = self._engine(["/([a-z]+/", "急单"])
        self.assertIn("跳过不可用的正则关键词", "\n".join(cm.output))
        self.assertEqual(engine._compiled, {})
        self.assertEqual(engine.check(self._msg("急单来了")), 1)

    def test_non_string_keyword_does_not_break_engine(self):
        """坏条目只影响自己，不能让引擎构造或整组关键词失效。"""
        engine, _ = self._engine([123, "急单"])
        self.assertEqual(engine._compiled, {})
        self.assertEqual(engine.check(self._msg("急单来了")), 1)

    def test_check_does_not_compile_per_message(self):
        """check() 跑在每条消息的接收线程上，绝不能在那里 re.compile。"""
        engine, _ = self._engine(["/\\d+元/"])
        patched = engine._compiled["/\\d+元/"]
        with patch.object(alert_mod.re, "compile",
                          side_effect=AssertionError("消息路径不应编译正则")):
            engine.check(self._msg("报价500元"))
            engine.check(self._msg("报价600元"))
        self.assertIs(engine._compiled["/\\d+元/"], patched)

    def test_update_config_recompiles(self):
        engine, _ = self._engine(["派单"])
        self.assertEqual(engine._compiled, {})
        cfg = AssistantConfig(assistant_enabled=True)
        cfg.alert_groups = [AlertGroup(chat_id="g@chatroom", group_name="测试群",
                                       keywords=["/\\d+元/"])]
        engine.update_config(cfg)
        self.assertIn("/\\d+元/", engine._compiled)


# ── 配置落盘兼容 ──────────────────────────────────────────────────────

class TestRegexConfigRoundTrip(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        self.addCleanup(setattr, config_mod, "CONFIG_PATH", self._orig)
        import shutil
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def test_regex_keywords_survive_save_and_load(self):
        cfg = config_mod.load_assistant_config()
        cfg.alert_groups.append(config_mod.AlertGroup(
            group_name="测试群", keywords=["派单", "/\\d{2,}元/", "/(?i)urgent/"],
        ))
        config_mod.save_assistant_config(cfg)

        reloaded = config_mod.load_assistant_config()
        self.assertEqual(
            reloaded.alert_groups[0].keywords,
            ["派单", "/\\d{2,}元/", "/(?i)urgent/"],
        )

    def test_legacy_literal_config_unchanged(self):
        """老配置（纯字面）读出来与原样一致，无迁移副作用。"""
        legacy = {
            "assistant_enabled": True,
            "alert_groups": [{
                "group_name": "抢单群A", "keywords": ["派单", "急"],
                "enabled": True, "chat_id": "a@chatroom",
            }],
        }
        config_mod.CONFIG_PATH.write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        cfg = config_mod.load_assistant_config()
        self.assertEqual(cfg.alert_groups[0].keywords, ["派单", "急"])


# ── Agent 工具 add_alert ──────────────────────────────────────────────

class TestAddAlertToolRegex(unittest.TestCase):

    def setUp(self):
        from src.agent.tools import ToolExecutor
        self._tmp = tempfile.mkdtemp()
        self._orig = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = Path(self._tmp) / "assistant_config.json"
        self.addCleanup(setattr, config_mod, "CONFIG_PATH", self._orig)
        import shutil
        self.addCleanup(shutil.rmtree, self._tmp, True)
        # __init__ 会注册全部工具（依赖较多），这里只测 handler，直接造实例
        self.executor = ToolExecutor.__new__(ToolExecutor)
        self.executor._alert_engine = None

    def test_invalid_regex_rejected_with_guidance(self):
        result = self.executor._handle_add_alert("测试群", ["/([a-z]+/"])
        self.assertIn("关键词不合法", result)
        self.assertIn("正则语法错误", result)
        self.assertIn("斜杠包裹", result)       # 引导文案
        cfg = config_mod.load_assistant_config()
        self.assertEqual(cfg.alert_groups, [])  # 非法正则不落盘

    def test_valid_regex_saved_and_echoed(self):
        result = self.executor._handle_add_alert("测试群", ["派单", "/\\d+元/"])
        self.assertIn("已为「测试群」添加关键词预警", result)
        self.assertIn("1 个按正则匹配", result)
        cfg = config_mod.load_assistant_config()
        self.assertEqual(cfg.alert_groups[0].keywords, ["派单", "/\\d+元/"])

    def test_literal_only_has_no_regex_note(self):
        result = self.executor._handle_add_alert("测试群", ["派单"])
        self.assertNotIn("按正则匹配", result)


if __name__ == "__main__":
    unittest.main()
