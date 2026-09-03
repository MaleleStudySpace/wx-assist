"""`_format_schedule` / `_cron_to_label` 的标签口径测试。

改造前 `_format_schedule` 对 cron 只做 `strip().split()`，两行 cron 得到 10 段、
`len(parts) == 5` 不成立就原样返回 —— 运行状态页上「金融杂项」因此显示成
`0 9 * * 1 0 9 * * 4`，换行还被 HTML 压成空格，连"这是两条规则"都看不出来。
dow 也只认字面量 `1-5`，不认 `1,4` 这类列表。

**期望值与前端 `ui/src/utils/cron.js` 的 `cronToLabel` 逐条机器比对过**：
31 个输入（含下面全部用例 + 各类兜底形态）分别喂给 Python 与 node 里的前端实现，
0 处分歧。同一个分组在公众号页和运行状态页必须是同一句话，改任何一边都要
重跑这个比对。
"""
import unittest

from src.web.api_handlers import _cron_to_label, _expand_dow, _format_schedule


class TestCronToLabel(unittest.TestCase):
    """单行与多行 cron → 中文标签。"""

    def test_daily_single_hour(self):
        self.assertEqual(_cron_to_label("0 19 * * *"), "每天 19:00")
        self.assertEqual(_cron_to_label("0 9 * * *"), "每天 09:00")

    def test_zero_pads_hour_and_minute(self):
        """群聊助手那边早就是「每天 10:01」，两边必须同一种写法。"""
        self.assertEqual(_cron_to_label("1 10 * * *"), "每天 10:01")
        self.assertEqual(_cron_to_label("5 8 * * *"), "每天 08:05")

    def test_comma_hour_list(self):
        """旧实现就支持逗号小时列表，收窄守卫时差点把它退化成裸 cron。"""
        self.assertEqual(_cron_to_label("0 9,18 * * *"), "每天 09:00 · 18:00")

    def test_comma_hours_cross_product_with_minutes(self):
        self.assertEqual(_cron_to_label("30 8,20 * * 1,4"), "周一 周四 08:30 · 20:30")

    def test_weekday_range(self):
        self.assertEqual(_cron_to_label("0 9 * * 1-5"), "工作日 09:00")

    def test_weekday_list_equivalent_to_range(self):
        self.assertEqual(_cron_to_label("0 9 * * 1,2,3,4,5"), "工作日 09:00")

    def test_partial_weekday_range(self):
        """前端旧实现把 `1-3` 解析成 NaN 丢掉、再兜底成周一到周五。"""
        self.assertEqual(_cron_to_label("0 9 * * 1-3"), "周一 周二 周三 09:00")

    def test_mixed_weekday_range_and_single(self):
        self.assertEqual(_cron_to_label("0 9 * * 2-4,6"), "周二 周三 周四 周六 09:00")

    def test_multi_line_merges_weekdays(self):
        """回归：生产上「金融杂项」就是这条，改造前显示两行裸 cron。

        星期必须**合并**，只留最后一行会显示成"周四 09:00"——
        静默丢掉周一，而且比裸 cron 更像是对的。
        """
        self.assertEqual(_cron_to_label("0 9 * * 1\n0 9 * * 4"), "周一 周四 09:00")

    def test_multi_line_merges_times(self):
        self.assertEqual(_cron_to_label("0 9 * * *\n0 20 * * *"),
                         "每天 09:00 · 20:00")

    def test_multi_line_dedupes_and_sorts_times(self):
        self.assertEqual(_cron_to_label("0 20 * * *\n0 9 * * *\n0 20 * * *"),
                         "每天 09:00 · 20:00")

    def test_three_lines_distinct_weekdays(self):
        self.assertEqual(_cron_to_label("0 8 * * 1\n30 12 * * 3\n0 18 * * 5"),
                         "周一 周三 周五 08:00 · 12:30 · 18:00")

    def test_mixed_daily_and_weekday_downgrades_to_custom(self):
        self.assertEqual(_cron_to_label("0 9 * * *\n0 9 * * 1-5"),
                         "周一 周二 周三 周四 周五 09:00")

    def test_sunday_and_saturday(self):
        self.assertEqual(_cron_to_label("0 9 * * 0"), "周日 09:00")
        self.assertEqual(_cron_to_label("0 9 * * 6"), "周六 09:00")


class TestCronToLabelFallsBackToRaw(unittest.TestCase):
    """表达不了的形态一律原样返回 —— 猜出来的标签比裸 cron 更危险。

    `_cron_to_label` 不能像"解析失败就套默认值"那样处理：`* * * * *` 若被
    当成 hours=[9]/mins=[] 再兜底成 09:00，就会把"每分钟"显示成"每天 09:00"。
    前端 cronToLabel 有同一道形态守卫，两边必须一致。
    """

    def test_minute_step(self):
        self.assertEqual(_cron_to_label("*/5 * * * *"), "*/5 * * * *")

    def test_every_minute(self):
        self.assertEqual(_cron_to_label("* * * * *"), "* * * * *")

    def test_hour_range(self):
        self.assertEqual(_cron_to_label("0 9-18 * * *"), "0 9-18 * * *")

    def test_specific_day_of_month(self):
        self.assertEqual(_cron_to_label("0 9 1 * *"), "0 9 1 * *")

    def test_specific_month(self):
        self.assertEqual(_cron_to_label("0 9 * 6 *"), "0 9 * 6 *")

    def test_out_of_range_hour(self):
        """不能把 `0 99 * * *` 标成"每天 99:00"。"""
        self.assertEqual(_cron_to_label("0 99 * * *"), "0 99 * * *")

    def test_out_of_range_minute(self):
        self.assertEqual(_cron_to_label("70 9 * * *"), "70 9 * * *")

    def test_named_weekday(self):
        self.assertEqual(_cron_to_label("0 9 * * MON"), "0 9 * * MON")

    def test_garbage(self):
        self.assertEqual(_cron_to_label("乱写的东西"), "乱写的东西")

    def test_wrong_field_count(self):
        self.assertEqual(_cron_to_label("0 9 * *"), "0 9 * *")

    def test_out_of_range_weekday(self):
        self.assertEqual(_cron_to_label("0 9 * * 9"), "0 9 * * 9")

    def test_reversed_weekday_range(self):
        self.assertEqual(_cron_to_label("0 9 * * 5-1"), "0 9 * * 5-1")

    def test_one_bad_line_poisons_the_whole_label(self):
        """半中半英的串（"每天 09:00、*/5 * * * *"）比全裸更难读。"""
        self.assertEqual(_cron_to_label("0 9 * * *\n*/5 * * * *"),
                         "0 9 * * *\n*/5 * * * *")


class TestExpandDow(unittest.TestCase):

    def test_list(self):
        self.assertEqual(_expand_dow("1,4"), [1, 4])

    def test_range(self):
        self.assertEqual(_expand_dow("1-5"), [1, 2, 3, 4, 5])

    def test_mixed_and_deduped(self):
        self.assertEqual(_expand_dow("4,1,4,2-3"), [1, 2, 3, 4])

    def test_single(self):
        self.assertEqual(_expand_dow("0"), [0])

    def test_unparsable(self):
        for bad in ("", "MON", "1-", "-5", "7", "1,9", "*/2"):
            self.assertIsNone(_expand_dow(bad), f"{bad!r} 应该判为无法展开")


class TestFormatSchedule(unittest.TestCase):

    def test_cron_wins_over_schedule(self):
        self.assertEqual(_format_schedule(["09:00"], "0 21 * * *"), "每天 21:00")

    def test_schedule_list_fallback(self):
        self.assertEqual(_format_schedule(["09:00", "21:00"], ""),
                         "每天 09:00、21:00")

    def test_schedule_bare_hour_gets_minutes(self):
        self.assertEqual(_format_schedule(["9"], ""), "每天 9:00")

    def test_both_empty(self):
        self.assertEqual(_format_schedule([], ""), "")

    def test_whitespace_only_cron_falls_through_to_schedule(self):
        self.assertEqual(_format_schedule(["08:00"], "   "), "每天 08:00")


if __name__ == "__main__":
    unittest.main()
