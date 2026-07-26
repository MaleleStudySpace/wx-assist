"""共享 cron 模块 — 匹配引擎 + 校验

提供三个导出函数：
  cron_matches(expr, now)        — 匹配引擎，所有定时器共用
  validate_cron_syntax(expr)     — 纯语法校验（CronScheduler / Agent / API 用）
  validate_daily_cron(expr)      — 日定时领域校验（OA / 群聊摘要用）
"""

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


# ── 内部：单字段匹配 ────────────────────────────────────────────────────

def _field_matches(field: str, value: int) -> bool:
    """Check if a single cron field matches a value.

    Supports: *, 5, 1-5, */15, 0-30/5, 5/15, 1,3,5
    """
    for part in field.split(','):
        part = part.strip()
        if part == '*':
            return True
        if '/' in part:
            range_part, step_str = part.split('/', 1)
            step = int(step_str)
            if range_part == '*':
                start, end = 0, 59
            elif '-' in range_part:
                start, end = map(int, range_part.split('-'))
            else:
                start = int(range_part)
                end = 59
            if value >= start and (value - start) % step == 0:
                return True
        elif '-' in part:
            start, end = map(int, part.split('-'))
            if start <= value <= end:
                return True
        else:
            if int(part) == value:
                return True
    return False


# ── 匹配引擎 ──────────────────────────────────────────────────────────

def cron_matches(expr: str, now: datetime) -> bool:
    """多行 cron 匹配。任一匹配即返回 True。

    每行为独立 5 字段 cron 表达式：分 时 日 月 周
    周日 = 0（isoweekday() % 7）
    """
    for line in expr.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 5:
            continue
        values = [
            now.minute,          # 0-59
            now.hour,            # 0-23
            now.day,             # 1-31
            now.month,           # 1-12
            now.isoweekday() % 7,  # 0-6 (Sunday=0)
        ]
        if all(_field_matches(f, v) for f, v in zip(fields, values)):
            return True
    return False


# ── 校验 ──────────────────────────────────────────────────────────────

def validate_cron_syntax(expr: str) -> str:
    """纯语法校验：5 字段、数值范围合法、格式正确。

    支持 * / 步进(/15) / 范围(1-5) / 列表(1,3,5)。
    返回 "" = 合法，非空 = 错误信息。
    """
    if not expr or not expr.strip():
        return "cron 表达式不能为空"

    lines = [line.strip() for line in expr.strip().split('\n') if line.strip()]
    if not lines:
        return "cron 表达式不能为空"

    for i, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != 5:
            return f"第{i}行：必须有5个字段（分 时 日 月 周），当前={line}"

        minute, hour, day, month, dow = parts

        # 分钟：0-59
        if not _validate_field_value(minute, 0, 59):
            return f"第{i}行：分钟={minute} 格式错误，应为0-59的整数、*、步进、范围或列表"
        # 小时：0-23
        if not _validate_field_value(hour, 0, 23):
            return f"第{i}行：小时={hour} 格式错误，应为0-23的整数、*、步进、范围或列表"
        # 日：1-31
        if not _validate_field_value(day, 1, 31):
            return f"第{i}行：日={day} 格式错误，应为1-31的整数、*、步进、范围或列表"
        # 月：1-12
        if not _validate_field_value(month, 1, 12):
            return f"第{i}行：月={month} 格式错误，应为1-12的整数、*、步进、范围或列表"
        # 周：0-6
        if not _validate_field_value(dow, 0, 6):
            return f"第{i}行：周={dow} 格式错误，应为0-6的整数、*、步进、范围或列表"

    return ""


def _validate_field_value(field: str, lo: int, hi: int) -> bool:
    """校验单个 cron 字段的语法和数值范围。

    支持: *, 5, 1-5, */15, 0-30/5, 5/15, 1,3,5
    """
    pattern = r'^(\d+(-\d+)?(/\d+)?|\*/?\d*)(,\d+(-\d+)?(/\d+)?|\*/?\d*)*$'
    if not re.match(pattern, field):
        return False
    # 对每个子段检查数值范围
    for part in field.split(','):
        part = part.strip()
        if part == '*':
            continue
        if '/' in part:
            range_part, step_str = part.split('/', 1)
            if not step_str.isdigit() or int(step_str) <= 0:
                return False
            if range_part == '*':
                continue
            if '-' in range_part:
                vals = range_part.split('-')
                if not all(v.isdigit() for v in vals):
                    return False
                if not (lo <= int(vals[0]) <= hi and lo <= int(vals[1]) <= hi):
                    return False
            else:
                if not range_part.isdigit():
                    return False
                if not (lo <= int(range_part) <= hi):
                    return False
        elif '-' in part:
            vals = part.split('-')
            if len(vals) != 2 or not all(v.isdigit() for v in vals):
                return False
            if not (lo <= int(vals[0]) <= hi and lo <= int(vals[1]) <= hi):
                return False
        else:
            if not part.isdigit():
                return False
            if not (lo <= int(part) <= hi):
                return False
    return True


def validate_daily_cron(expr: str, field_name: str = "cron") -> str:
    """日定时领域校验 — OA/群聊摘要 使用。

    在语法校验基础上追加：
    - 分/时必须是单整数
    - 日/月必须是 *
    - 周支持 *、范围、列表

    返回 "" = 合法，非空 = 错误信息。
    """
    if not expr or not expr.strip():
        return ""

    lines = [line.strip() for line in expr.strip().split('\n') if line.strip()]
    if not lines:
        return f"{field_name}: cron表达式不能为空"

    for i, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != 5:
            return f"{field_name}: 第{i}行必须有5个字段（分 时 日 月 周），当前={line}"

        minute, hour, day, month, dow = parts

        # 分：必须为单整数 0-59
        try:
            minute_i = int(minute)
        except ValueError:
            return f"{field_name}: 第{i}行分/时必须是单个数字，不能使用逗号、*、范围或步进"
        if not (0 <= minute_i <= 59):
            return f"{field_name}: 第{i}行分钟={minute} 超出范围，应为0-59"

        # 时：必须为单整数 0-23
        try:
            hour_i = int(hour)
        except ValueError:
            return f"{field_name}: 第{i}行分/时必须是单个数字，不能使用逗号、*、范围或步进"
        if not (0 <= hour_i <= 23):
            return f"{field_name}: 第{i}行小时={hour} 超出范围，应为0-23"

        # 日/月：必须为 *
        if day != "*":
            return f"{field_name}: 第{i}行日字段必须是 *"
        if month != "*":
            return f"{field_name}: 第{i}行月字段必须是 *"

        # 周：* 或范围/列表
        if dow != "*" and not re.match(r'^(\d+(-\d+)?)(,\d+(-\d+)?)*$', dow):
            return f"{field_name}: 第{i}行周={dow} 格式错误，支持 *、1-5、1,2,3,4,5"

    return ""
