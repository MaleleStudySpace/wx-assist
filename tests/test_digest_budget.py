"""token 预算判定、裁最旧、打包 prompt 与拼接的纯函数测试。

估算口径复用 AbstractSummarizer._estimate_tokens（1.5 字符/token + 每消息 40
字符开销 + 500 固定），实测这批语料真实比例 0.42，即估算保守约 1.6 倍。
下面的期望值都按这个公式手算，改公式会立刻暴露。
"""
import os
import unittest
from unittest.mock import patch

from src.assistant.config import DigestGroup, GroupProfile
from src.assistant.digest import (
    BUDGET_CEIL,
    BUDGET_FLOOR,
    DEFAULT_DIGEST_TOKEN_BUDGET,
    MIN_UNIT_TOKENS,
    PACKED_TRIM_SLACK_RATIO,
    SECTION_OVERHEAD_TOKENS,
    SYSTEM_RESERVE_TOKENS,
    ChatUnit,
    concat_sections,
    build_packed_prompt,
    digest_token_budget,
    estimate_messages_tokens,
    plan_digest,
    trim_oldest,
)
from src.summarize.base import AbstractSummarizer


def msgs(n, content_len=10, sender="A", start_ts=1000):
    """n 条消息，每条 content 长度 content_len。

    est = int(n * (content_len + len(sender) + 40) / 1.5) + 500
    content_len=10, sender="A" → 每条 51 字符 → est = 34n + 500
    """
    return [{"sender_name": sender, "content": "x" * content_len,
             "timestamp": start_ts + i} for i in range(n)]


def unit(name, n, content_len=10, chat_id=None):
    m = msgs(n, content_len)
    return ChatUnit(chat_id=chat_id or f"{name}@chatroom", name=name,
                    messages=m, est_tokens=estimate_messages_tokens(m),
                    raw_count=n)


class TestEstimate(unittest.TestCase):

    def test_reuses_summarizer_formula(self):
        m = msgs(7, 23)
        self.assertEqual(estimate_messages_tokens(m),
                         AbstractSummarizer._estimate_tokens(m))

    def test_formula_is_conservative_vs_measured_ratio(self):
        """实测真实比例 0.42 token/字符，估算器给 0.667 —— 保守方向才对。"""
        m = msgs(100, 50)
        chars = sum(len(x["content"]) + len(x["sender_name"]) for x in m)
        self.assertGreater(estimate_messages_tokens(m) / chars, 0.42)


class TestBudgetEnv(unittest.TestCase):

    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIGEST_TOKEN_BUDGET", None)
            self.assertEqual(digest_token_budget(), DEFAULT_DIGEST_TOKEN_BUDGET)

    def test_env_override(self):
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "50000"}):
            self.assertEqual(digest_token_budget(), 50000)

    def test_invalid_value_falls_back_to_default(self):
        for bad in ("abc", "", "  ", "1.5", "12_000x"):
            with self.subTest(bad=bad):
                with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": bad}):
                    self.assertEqual(digest_token_budget(),
                                     DEFAULT_DIGEST_TOKEN_BUDGET)

    def test_out_of_range_is_clamped(self):
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "99999999"}):
            self.assertEqual(digest_token_budget(), BUDGET_CEIL)
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "-5"}):
            self.assertEqual(digest_token_budget(), BUDGET_FLOOR)

    def test_kill_switch_forces_degradation(self):
        """DIGEST_TOKEN_BUDGET=1 → 夹到下限 → 多会话必然降级。

        这是出问题时的运维开关：改 env 重启即可，不用回滚代码。
        """
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "1"}):
            self.assertEqual(digest_token_budget(), BUDGET_FLOOR)
            units = [unit("甲", 100, 50), unit("乙", 100, 50)]
            plan = plan_digest(units, digest_token_budget())
            self.assertEqual(plan.mode, "per_chat")

    def test_budget_is_reread_not_cached(self):
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "20000"}):
            self.assertEqual(digest_token_budget(), 20000)
        with patch.dict(os.environ, {"DIGEST_TOKEN_BUDGET": "30000"}):
            self.assertEqual(digest_token_budget(), 30000)


class TestTrimOldest(unittest.TestCase):

    def test_noop_when_under_budget(self):
        m = msgs(5)
        kept, dropped = trim_oldest(m, 99999)
        self.assertEqual(kept, m)
        self.assertEqual(dropped, 0)

    def test_drops_oldest_and_keeps_newest(self):
        m = msgs(10)                       # est = 34n + 500
        kept, dropped = trim_oldest(m, 763)  # 34*7+500=738 ok, 34*8+500=772 no
        self.assertEqual(len(kept), 7)
        self.assertEqual(dropped, 3)
        self.assertEqual([x["timestamp"] for x in kept], list(range(1003, 1010)))

    def test_preserves_ascending_order(self):
        kept, _ = trim_oldest(msgs(20), 900)
        stamps = [x["timestamp"] for x in kept]
        self.assertEqual(stamps, sorted(stamps))

    def test_keeps_at_least_one_when_single_message_exceeds_budget(self):
        kept, dropped = trim_oldest(msgs(3), 100)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["timestamp"], 1002, "留下的必须是最新那条")
        self.assertEqual(dropped, 2)

    def test_zero_or_negative_budget_returns_empty(self):
        self.assertEqual(trim_oldest(msgs(4), 0), ([], 4))
        self.assertEqual(trim_oldest(msgs(4), -10), ([], 4))

    def test_empty_messages(self):
        self.assertEqual(trim_oldest([], 100), ([], 0))
        self.assertEqual(trim_oldest([], 0), ([], 0))


class TestPlanDigest(unittest.TestCase):

    def test_single_chat_mode(self):
        """只有一个会话 → single，不打包。升级当天线上 3 个组都走这条。"""
        u = unit("甲", 10)
        plan = plan_digest([u], 100000)
        self.assertEqual(plan.mode, "single")
        self.assertEqual(plan.notes, [])
        self.assertEqual(u.dropped, 0)

    def test_single_mode_still_trims_oversized_chat(self):
        u = unit("甲", 100, 50)            # est = int(100*91/1.5)+500 = 6566
        plan = plan_digest([u], 3000)        # available = 2200
        self.assertEqual(plan.mode, "single")
        self.assertGreater(u.dropped, 0)
        self.assertLessEqual(u.est_tokens, plan.available)
        self.assertIn("甲", plan.notes[0])

    def test_all_units_empty_still_returns_single(self):
        plan = plan_digest([unit("甲", 0)], 100000)
        self.assertEqual(plan.mode, "single")
        self.assertEqual(plan.total_tokens, 0)

    def test_packed_when_under_budget(self):
        units = [unit("甲", 10), unit("乙", 10), unit("丙", 10)]
        plan = plan_digest(units, 100000)
        self.assertEqual(plan.mode, "packed")
        self.assertEqual(plan.notes, [])
        self.assertEqual([u.dropped for u in units], [0, 0, 0])
        self.assertEqual(plan.total_tokens,
                         3 * 840 + SECTION_OVERHEAD_TOKENS * 3)

    def test_memory_tokens_reduce_available(self):
        units = [unit("甲", 10), unit("乙", 10)]
        with_mem = plan_digest(units, 100000, memory_tokens=50000)
        without = plan_digest(units, 100000, memory_tokens=0)
        self.assertEqual(without.available - with_mem.available, 50000)

    def test_available_has_floor_when_memory_huge(self):
        plan = plan_digest([unit("甲", 10)], 3000, memory_tokens=999999)
        self.assertEqual(plan.available, 2000)

    def test_trims_proportionally_when_slightly_over(self):
        """超限 ≤20% → 按占比裁最旧，仍然打包。"""
        units = [unit("甲", 10), unit("乙", 10), unit("丙", 10)]
        # est 各 840 → total = 2520 + 120 = 2640；available = 3200-800 = 2400
        plan = plan_digest(units, 3200)
        self.assertLessEqual((2640 - 2400) / 2400, PACKED_TRIM_SLACK_RATIO)
        self.assertEqual(plan.mode, "packed")
        self.assertEqual([u.dropped for u in units], [3, 3, 3])
        self.assertLessEqual(plan.total_tokens, plan.available)
        self.assertIn("裁剪最旧消息", plan.notes[0])
        # 裁掉的必须是最旧的，留下最新的
        for u in units:
            self.assertEqual([m["timestamp"] for m in u.messages],
                             list(range(1003, 1010)))

    def test_degrades_when_far_over(self):
        units = [unit("甲", 100), unit("乙", 100), unit("丙", 100)]
        plan = plan_digest(units, 3200)      # available=2400, total≈11820
        self.assertEqual(plan.mode, "per_chat")
        self.assertIn("降级为逐会话摘要后拼接", plan.notes[0])

    def test_per_chat_trims_each_oversized_unit_to_full_available(self):
        units = [unit("甲", 100), unit("乙", 100)]
        plan = plan_digest(units, 3200)      # available = 2400
        self.assertEqual(plan.mode, "per_chat")
        for u in units:
            self.assertLessEqual(u.est_tokens, plan.available)
            self.assertEqual(len(u.messages), 55)   # 34*55+500=2370 ≤ 2400
            self.assertEqual(u.dropped, 45)

    def test_min_unit_floor_forces_degradation(self):
        """均摊配额低于单条消息体积时裁剪无效 → 必须落到 per_chat。

        20 个各只有 1 条消息的会话：每条 est=534，均摊配额算出来是 485，
        但 trim_oldest 的"至少保留最新 1 条"规则让它降不下去，
        总量仍然超预算，所以不能假装打包成功。
        """
        units = [unit(f"群{i}", 1) for i in range(20)]
        total = 20 * 534 + SECTION_OVERHEAD_TOKENS * 20
        budget = total + SYSTEM_RESERVE_TOKENS - int(total * 0.09)  # 超限约 9%
        plan = plan_digest(units, budget)
        self.assertEqual(plan.mode, "per_chat")
        for u in units:
            self.assertEqual(len(u.messages), 1, "不能因为裁剪而丢掉唯一的消息")

    def test_empty_units_are_not_counted_in_total(self):
        units = [unit("甲", 10), unit("空", 0), unit("乙", 10)]
        plan = plan_digest(units, 100000)
        self.assertEqual(plan.mode, "packed")
        self.assertEqual(plan.total_tokens,
                         2 * 840 + SECTION_OVERHEAD_TOKENS * 2)

    def test_available_accounts_for_system_reserve(self):
        plan = plan_digest([unit("甲", 10)], 50000)
        self.assertEqual(plan.available, 50000 - SYSTEM_RESERVE_TOKENS)


class TestBuildPackedPrompt(unittest.TestCase):

    def _dg(self, memory="组级共享记忆"):
        return DigestGroup(id="dg_001", name="测试组", memory=memory)

    def test_has_ordered_section_markers(self):
        units = [unit("甲", 3), unit("乙", 2), unit("丙", 1)]
        p = build_packed_prompt(self._dg(), units)
        self.assertIn("## 近期记忆（本分组共用）", p)
        self.assertIn("组级共享记忆", p)
        self.assertIn("## 待摘要的 3 个会话", p)
        self.assertLess(p.index("=== [1] 甲 (3 条) ==="), p.index("=== [2] 乙 (2 条) ==="))
        self.assertLess(p.index("=== [2] 乙 (2 条) ==="), p.index("=== [3] 丙 (1 条) ==="))

    def test_marks_trimmed_sections(self):
        u = unit("甲", 10)
        u.messages, u.dropped = trim_oldest(u.messages, 763)
        p = build_packed_prompt(self._dg(), [u, unit("乙", 3)])
        self.assertIn("=== [1] 甲 (7 条，已省略最早 3 条) ===", p)

    def test_skips_empty_units(self):
        p = build_packed_prompt(self._dg(), [unit("甲", 3), unit("空", 0)])
        self.assertIn("## 待摘要的 1 个会话", p)
        self.assertNotIn("空", p)

    def test_empty_memory_placeholder(self):
        p = build_packed_prompt(self._dg(memory=""), [unit("甲", 1)])
        self.assertIn("（暂无历史记忆）", p)

    def test_strips_wxid_from_message_lines(self):
        u = unit("甲", 1)
        u.messages[0]["content"] = "联系 wxid_abc123 报价"
        p = build_packed_prompt(self._dg(), [u])
        self.assertNotIn("wxid_abc123", p)
        self.assertIn("联系  报价".replace("  ", " "), p)

    def test_message_lines_carry_time_and_sender(self):
        u = unit("甲", 1)
        u.messages[0]["sender_name"] = "张三"
        p = build_packed_prompt(self._dg(), [u])
        self.assertIn("张三: ", p)
        self.assertRegex(p, r"\[\d{2}:\d{2}\] 张三: ")


class TestConcatSections(unittest.TestCase):
    """降级路径的汇总必须是**纯字符串拼接**，一次 LLM 都不调。"""

    def test_joins_in_order_with_separator(self):
        a, b = unit("甲", 1), unit("乙", 1)
        out = concat_sections([(a, "甲的摘要", ""), (b, "乙的摘要", "")])
        self.assertEqual(out, "## 甲\n甲的摘要\n\n---\n\n## 乙\n乙的摘要")

    def test_no_llm_parameter_in_signature(self):
        """结构护栏：签名里不能出现 summarizer / llm 之类参数。"""
        import inspect
        params = list(inspect.signature(concat_sections).parameters)
        self.assertEqual(params, ["sections"])

    def test_failed_chat_is_annotated(self):
        a, b = unit("甲", 1), unit("乙", 1)
        out = concat_sections([(a, "甲的摘要", ""),
                               (b, "", "上下文超限 prompt_tokens=299247")])
        self.assertIn("## 乙\n（本次摘要失败：上下文超限 prompt_tokens=299247）", out)
        self.assertIn("甲的摘要", out, "一个会话失败不能影响其他会话")

    def test_dropped_messages_are_disclosed(self):
        """必须显式告知省略，否则 LLM 会把"窗口内没提到"当成"没发生"。"""
        a = unit("甲", 10)
        a.dropped = 3
        out = concat_sections([(a, "摘要", "")])
        self.assertIn("> 已省略最早 3 条消息（超出 token 预算）", out)

    def test_empty_body_becomes_no_content(self):
        out = concat_sections([(unit("甲", 1), "   ", "")])
        self.assertIn("（无实质内容）", out)

    def test_strips_duplicate_heading_from_llm(self):
        for heading in ("## 甲", "# 甲", "##  甲 ", "## 群：甲", "## 会话：甲"):
            with self.subTest(heading=heading):
                out = concat_sections([(unit("甲", 1), heading + "\n正文内容", "")])
                self.assertEqual(out, "## 甲\n正文内容")

    def test_keeps_unrelated_heading(self):
        out = concat_sections([(unit("甲", 1), "## 别的标题\n正文", "")])
        self.assertIn("## 别的标题", out)

    def test_single_section_has_no_separator(self):
        out = concat_sections([(unit("甲", 1), "摘要", "")])
        self.assertNotIn("---", out)


if __name__ == "__main__":
    unittest.main()
