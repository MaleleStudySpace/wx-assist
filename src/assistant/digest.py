"""Digest engine — generates timed group chat summaries with filtering and memory."""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import DigestGroup, memory_char_budget

logger = logging.getLogger(__name__)

# Messages shorter than this (chars) after trimming are filtered out
MIN_MESSAGE_LENGTH = 2

# Common meaningless replies (case-insensitive exact match after stripping)
NOISE_REPLIES = {
    "收到", "好的", "ok", "1", "嗯", "好", "哈哈", "哈哈哈",
    "哦", "知道了", "明白", "👌", "👍", "okay", "yes", "no",
    "顶", "up", "来了", "在", "到",
}

# Keywords that indicate a system/non-content message
SYSTEM_KEYWORDS = (
    "修改群名", "加入了群聊", "退出了群聊",
    "撤回了一条消息", "被移除", "开启了朋友验证",
    "邀请", "移出了群聊",
)

# ── System prompt for scheduled group digest ──────────────────────
# This is the DEFAULT system prompt for the scheduled digest feature.
# When custom_prompt is set in GroupProfile, it COMPLETELY REPLACES this prompt.
DIGEST_SYSTEM_PROMPT = """\
你是一个微信聊天定时摘要助手，为用户配置的「摘要分组」生成摘要。
一个分组里可能只有 1 个会话，也可能有多个互不相干的群聊/私聊；本次要摘要哪几个会话，用户消息里会明确列出。

## 核心任务
根据提供的近期记忆和最新消息，**逐个会话**生成结构化摘要。

## 标题层级（严格遵守）
- 每个会话的段落第一行是 `## 会话名`，会话名照抄用户消息里给出的名字。
- 会话内部的话题用 `###` 三级标题或 `-` 列表，**不要再用 `##`**——否则分不清话题属于哪个会话。
- 只有一个会话时也要写 `## 会话名`。

## 输出要求
- 用中文，简洁自然，像给同事转述一样。
- 突出可行动的信息：待办事项、决定、截止时间、联系方式、价格/费率等数字。
- 忽略闲聊、表情、无实质内容的消息。
- 近期记忆是**整个分组共用**的历史浓缩，里面可能混着其他会话的事：只在与当前会话明确相关时才引用，不要张冠李戴；与最新消息冲突时以最新消息为准。
- 某个会话没有实质内容时，一句话说清楚即可，不要硬编，也不要借别的会话的内容来凑。
- 不要输出 wxid_xxx——始终用消息里的昵称。"""

# ── Style preset instructions (appended to DIGEST_SYSTEM_PROMPT) ──
STYLE_PRESETS = {
    "行动项优先": "\n\n## 摘要风格\n只输出可行动的信息：待办事项、决定、截止时间、负责人、联系方式。省略闲聊和讨论过程。如果没有任何行动项，直接说「无待办」。",
    "完整复盘": "\n\n## 摘要风格\n完整复盘所有话题，包括讨论过程、不同观点和最终结论。保留金句和有趣互动。",
    "极简速览": "\n\n## 摘要风格\n极简输出，每条摘要不超过一句话，只保留最重要的3-5个要点。用 • 列表格式。",
}

# ── Media type to LLM-safe placeholder ────────────────────────────
# WCDB 4.x stores raw XML/JSON in content for non-text messages.
# Replace with structured placeholders so LLM knows context without
# seeing binary/encoded payloads.  When multimodal support is added,
# replace the placeholder with the actual decoded content.
MEDIA_PLACEHOLDERS = {
    3: "{{ image }}",
    34: "{{ voice }}",
    43: "{{ video }}",
    47: "{{ sticker }}",
    49: "{{ app_message }}",
}
MEDIA_RAW_TYPES = frozenset({3, 34, 43, 47, 49})

# Media payloads carrying no text worth showing the LLM.
PURE_MEDIA_TYPES = frozenset({3, 34, 43, 47})

# WCDB stores 群接龙/引用回复/名片/聊天记录/图片/表情 as msg_type=1 with the full
# XML payload in content, so MEDIA_RAW_TYPES never catches them and raw XML goes
# straight into the LLM prompt.  Measured over 72h of real traffic: 2632 of 4793
# long messages (55%) were such XML totalling 7,265,605 chars; collapsing each to
# a short label leaves 45,659 (-99.4%).  A single message reached 17,015 chars.
# XML_MIN_LEN keeps short text like "<3 你" untouched.
XML_MIN_LEN = 60
_APPMSG_TITLE_RE = re.compile(r'<title>(.*?)</title>', re.IGNORECASE | re.DOTALL)
_MAIL_SUBJECT_RE = re.compile(r'<subject>(.*?)</subject>', re.IGNORECASE | re.DOTALL)
_CDATA_RE = re.compile(r'<!\[CDATA\[(.*?)\]\]>', re.DOTALL)
_XML_HEAD_TAGS = ("<appmsg", "<msg", "<sysmsg", "<refermsg", "<recordinfo", "<weixin")

# Of those 2632, 1353 had no <title>: 814 <img>, 440 <emoji>, 27 <pushmail>
# (which do carry real text in <subject>), 14 <voicemsg>, 13 <videomsg>,
# 5 contact cards, 2 <location>.  So msg_type cannot be trusted to label media —
# the payload's root element can.  Labels reuse MEDIA_PLACEHOLDERS values so the
# LLM sees one consistent vocabulary instead of two.
_XML_MEDIA_TAG_RE = re.compile(r'<(img|emoji|videomsg|voicemsg|location)\b', re.IGNORECASE)
_XML_MEDIA_LABELS = {
    "img": MEDIA_PLACEHOLDERS[3],
    "emoji": MEDIA_PLACEHOLDERS[47],
    "videomsg": MEDIA_PLACEHOLDERS[43],
    "voicemsg": MEDIA_PLACEHOLDERS[34],
    "location": "{{ location }}",
}
_CONTACT_CARD_MARKER = "bigheadimgurl"
_XML_STRUCT_HEAD_LEN = 400

# WCDB 4.x sometimes stores messages with msg_type=1 (text) but content
# is actually encrypted ciphertext (a long hex string).  These leak
# raw encrypted data to LLM if not caught.  Minimum hex length to avoid
# false-positives on short numeric strings like "123abc".
ENCRYPTED_MIN_LEN = 50
_ENCRYPTED_HEX_RE = re.compile(r'[a-fA-F0-9]{' + str(ENCRYPTED_MIN_LEN) + r',}$')


def _strip_ids(text: str) -> str:
    """Remove raw wxid/gh_ identifiers from message content.

    The WCDB _standardize() function already handles @wxid_xxx → @昵称,
    but standalone wxid_xxx (e.g. from contact cards, reference replies)
    still appears in content.  These are pure noise for LLM and matching.
    """
    if not text:
        return ""
    # Standalone wxid_xxx (not @wxid_xxx which was already resolved)
    text = re.sub(r'wxid_[a-zA-Z0-9]+', '', text)
    # OA account IDs
    text = re.sub(r'gh_[a-zA-Z0-9]+', '', text)
    # Clean up artifacts from removal: "wxid_xxx: 你好" → " : 你好" → ": 你好"
    text = re.sub(r'\s+:\s*', ': ', text)
    text = re.sub(r':\s+:', ':', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _extract_tag_text(pattern, content: str) -> str:
    """Pull a tag's text out of XML, unwrapping CDATA and collapsing whitespace."""
    m = pattern.search(content)
    if not m:
        return ""
    text = m.group(1).strip()
    cdata = _CDATA_RE.search(text)
    if cdata:
        text = cdata.group(1).strip()
    return " ".join(text.split())[:120]


def _clean_xml_content(content: str) -> Optional[str]:
    """Collapse WCDB XML payloads disguised as plain text into a short label.

    Returns None when the content is not XML, meaning "leave it alone".
    """
    if not content or len(content) < XML_MIN_LEN:
        return None
    head = content.lstrip()[:64].lower()
    if not head.startswith("<"):
        return None
    if not any(tag in head for tag in _XML_HEAD_TAGS) and "</" not in content:
        return None

    title = _extract_tag_text(_APPMSG_TITLE_RE, content)
    if title:
        return "{{app: " + title + "}}"

    subject = _extract_tag_text(_MAIL_SUBJECT_RE, content)
    if subject:
        return "{{mail: " + subject + "}}"

    # Only inspect the opening region: an appmsg body may mention these tags.
    struct_head = content[:_XML_STRUCT_HEAD_LEN]
    m = _XML_MEDIA_TAG_RE.search(struct_head)
    if m:
        return _XML_MEDIA_LABELS[m.group(1).lower()]
    if _CONTACT_CARD_MARKER in struct_head.lower():
        return "{{ contact_card }}"

    return "{{app_message}}"


def filter_messages(messages: list[dict], ignore_keywords: Optional[list[str]] = None) -> list[dict]:
    """Filter low-value messages from a list.

    Removes:
    - System messages (join/leave/rename/etc.)
    - Very short messages
    - Common meaningless replies
    - Messages matching ignore keywords

    For non-text media messages (image/voice/video/sticker/app), replaces
    raw XML/JSON content with a clean placeholder so LLM only sees context.
    """
    ignore_set = set(kw.lower() for kw in (ignore_keywords or []))

    result = []
    for msg in messages:
        content = (msg.get("content", "") or "").strip()
        if not content:
            continue

        # Skip system messages
        if any(kw in content for kw in SYSTEM_KEYWORDS):
            continue

        # Skip pure emoji / very short
        if len(content) < MIN_MESSAGE_LENGTH:
            continue

        # Skip noise replies
        if content.lower() in NOISE_REPLIES:
            continue

        # Skip messages matching ignore keywords
        content_lower = content.lower()
        if any(ik in content_lower for ik in ignore_set):
            continue

        # WCDB encrypted content: msg_type=1 but content is raw ciphertext hex
        if len(content) >= ENCRYPTED_MIN_LEN and _ENCRYPTED_HEX_RE.fullmatch(content):
            msg["content"] = "{{ encrypted }}"
            result.append(msg)
            continue

        msg_type = msg.get("msg_type", 1)

        # Pure media (image/voice/video/sticker) — fixed placeholder, unchanged
        if msg_type in PURE_MEDIA_TYPES:
            msg["content"] = MEDIA_PLACEHOLDERS.get(msg_type, "{{ media }}")
            result.append(msg)
            continue

        # appmsg XML disguised as text: msg_type=1 接龙/引用/名片, and 49 with XML
        cleaned = _clean_xml_content(content)
        if cleaned is not None:
            msg["content"] = cleaned
            result.append(msg)
            continue

        # Remaining raw media types (49 whose content is not XML)
        if msg_type in MEDIA_RAW_TYPES:
            msg["content"] = MEDIA_PLACEHOLDERS.get(msg_type, "{{ media }}")
            result.append(msg)
            continue

        result.append(msg)

    return result


def build_digest_prompt(group_cfg: DigestGroup, messages: list[dict],
                        chat_name: str = "") -> str:
    """Build the user prompt for AI digest generation.

    Provides CONTEXT only (chat identity + shared memory + messages).
    Instructions belong in the system prompt (DIGEST_SYSTEM_PROMPT or custom_prompt).

    chat_name 必须给：组内多个会话共用一份记忆，不点名当前会话，
    LLM 会把别的会话的历史当成这个会话的，摘要也就无法归属。
    """
    name = chat_name or group_cfg.name
    memory = group_cfg.memory or "（暂无历史记忆）"

    # Format messages with wxid stripped + media placeholder
    msg_lines = []
    for m in messages:
        sender = m.get("sender_name", "?")
        content = m.get("content", "")
        # Strip raw wxid/gh_ identifiers from message text
        content = _strip_ids(content)
        ts = m.get("timestamp", 0)
        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        if content:
            msg_lines.append(f"[{time_str}] {sender}: {content}")

    messages_text = "\n".join(msg_lines)

    logger.info("[DIGEST-PROMPT] chat=%s group=%s memory_len=%d messages=%d",
                 name, group_cfg.name, len(memory), len(messages))

    return f"""## 本次摘要的会话
{name}

## 近期记忆（分组「{group_cfg.name}」共用，可能含其他会话的事，只在与「{name}」相关时引用）
{memory}

## 「{name}」最近 {len(messages)} 条消息
{messages_text}"""


def generate_memory_update_prompt(previous_memory: str, digest_text: str,
                                  chat_names: list = None,
                                  budget: int = None) -> str:
    """Build the prompt for AI to update the group's digest memory.

    记忆是**分组级**的：一份记忆服务组内所有会话，下一轮又会作为共用上下文
    喂回每个会话。不按会话分块，LLM 就会写成"我们群如何如何"的单群口吻，
    事实串了也查不出来是哪个会话的。
    """
    prev = previous_memory if previous_memory else "（暂无）"
    names = [str(n) for n in (chat_names or []) if n]
    if budget is None:
        budget = memory_char_budget(len(names))
    scope = (f"本分组包含 {len(names)} 个会话：{'、'.join(names)}。"
             if names else "本分组包含的会话见下方摘要里的 `##` 小标题。")
    return f"""## 之前的摘要记忆
{prev}

## 本次摘要
{digest_text}

{scope}
请更新这个**分组**的摘要记忆，全文不超过 {budget} 字，要求：
- 按会话分块，每块以 `### 会话名` 开头（会话名照抄），只写这个会话自己的事，禁止把 A 会话的内容写进 B 会话。
- 每块记录：核心要点、近期重要事件与趋势变化、活跃度与氛围。
- 本次没有新内容的会话，保留上一版里它的要点并标注「（无新增）」，不要整块删掉。
- 之前的记忆里若有无法归属到具体会话的内容（早期按单群口吻写的），归入开头的 `### 分组共性` 一块。
- 超出字数时优先保留最近的事件和未完结的事，删掉已经过期的细节；不要写到一半截断。
直接输出记忆文本，不要 JSON 包装。"""


# ── 多会话打包与预算降级 ────────────────────────────────────────────
#
# 一次调度把一个分组内所有会话的消息打包成**一次** LLM 调用，输出按会话分段。
# 打包超出 token 预算时降级为逐会话摘要，然后**纯字符串拼接**（不再调 LLM 汇总）。
#
# 预算口径复用 AbstractSummarizer._estimate_tokens（1.5 字符/token + 每消息 40
# 字符开销 + 500 固定）。实测这批语料真实比例是 0.42 token/字符，即估算值比真实值
# **保守约 1.6 倍** —— 保守正是需要的方向，所以不改那个函数、也不动 token_budget
# 类属性（src/web/ai_chat.py 拿它算上下文压缩阈值）。

DEFAULT_DIGEST_TOKEN_BUDGET = 150_000
# 天花板 400K：估算 400K ≈ 真实 250K token，正好是实测的 provider 拒绝线。
# 下限 10K 同时也是 kill switch —— DIGEST_TOKEN_BUDGET=1 会被夹到这里，
# 任何多会话分组都必然超限从而走 per_chat，出问题时改 env 重启即可，不用回滚代码。
BUDGET_FLOOR = 10_000
BUDGET_CEIL = 400_000

SYSTEM_RESERVE_TOKENS = 800      # system prompt + 输出契约的余量（估算器已含 500）
MIN_AVAILABLE_TOKENS = 2_000     # 记忆过长时的地板，避免 available 变负
SECTION_OVERHEAD_TOKENS = 40     # 每个 "=== [i] 名称 (N 条) ===" 分节头
MIN_UNIT_TOKENS = 200            # 均摊裁剪时每会话最少保留
PACKED_TRIM_SLACK_RATIO = 0.20   # 超限 ≤20% 才尝试裁剪后仍打包，否则直接降级

PACKED_OUTPUT_CONTRACT = """

## 多会话打包输出契约（结构约束，必须遵守）
用户消息里给出 N 个会话，每个会话以 `=== [序号] 会话名 (条数) ===` 分隔。
你必须：
1. 按输入顺序，为每一个会话输出一个独立段落，段落第一行是 `## 会话名`（会话名原样照抄）。
2. 会话内部只用 `###` 或 `-`，**禁止再用 `##`**——否则分不清话题属于哪个会话。
3. 禁止跨会话合并话题，禁止写总览/综述/开场白/结尾总结。
4. 禁止遗漏任何一个会话；某会话没有实质内容时，输出 `## 会话名` 加一行 `（无实质内容）`。
5. 每个会话的摘要控制在 200 字以内。"""

PER_CHAT_NO_HEADING_HINT = """

## 输出格式
本次只给你一个会话，外层会自动补上 `## 会话名`。
禁止输出任何 `#`/`##`/`###` 标题、禁止输出开场白，直接输出摘要正文；需要分点时用 `-` 列表。"""

# single 模式（组内只有 1 个会话有新消息）同样必须点名会话：否则摘要正文
# 无法归属，写进组记忆后就分不清是哪个会话的事，而这份记忆下一轮会喂给全组。
SINGLE_HEADING_CONTRACT = """

## 输出格式（结构约束，必须遵守）
第一行是 `## 会话名`（照抄用户消息里「本次摘要的会话」给出的名字）。
会话内部只用 `###` 或 `-`，禁止再用 `##`。"""


@dataclass
class ChatUnit:
    """分组内一个会话的待摘要数据。"""
    chat_id: str
    name: str
    messages: list = field(default_factory=list)   # 已过 filter_messages
    est_tokens: int = 0
    dropped: int = 0        # 为适配预算被裁掉的最旧消息条数
    raw_count: int = 0      # 过滤前条数，用于日志与统计
    error: str = ""         # 取消息或摘要阶段的错误


@dataclass
class DigestPlan:
    """预算判定结果。mode: "single" | "packed" | "per_chat"。"""
    mode: str
    units: list
    budget: int
    available: int
    total_tokens: int
    notes: list = field(default_factory=list)


def digest_token_budget() -> int:
    """读 env `DIGEST_TOKEN_BUDGET`，默认 150_000，非法值夹到 [10_000, 400_000]。

    每次调用都重读 env（不缓存成模块常量），这样测试能用 patch.dict 覆盖，
    运维也能只改 .env 重启就切换 kill switch。
    """
    raw = os.getenv("DIGEST_TOKEN_BUDGET", "").strip()
    if not raw:
        return DEFAULT_DIGEST_TOKEN_BUDGET
    try:
        value = int(raw)
    except ValueError:
        logger.warning("DIGEST_TOKEN_BUDGET=%r 不是整数，回落默认 %d",
                       raw, DEFAULT_DIGEST_TOKEN_BUDGET)
        return DEFAULT_DIGEST_TOKEN_BUDGET
    if not BUDGET_FLOOR <= value <= BUDGET_CEIL:
        clamped = min(max(value, BUDGET_FLOOR), BUDGET_CEIL)
        logger.warning("DIGEST_TOKEN_BUDGET=%d 超出 [%d, %d]，夹到 %d",
                       value, BUDGET_FLOOR, BUDGET_CEIL, clamped)
        return clamped
    return value


def estimate_messages_tokens(messages: list) -> int:
    """复用 AbstractSummarizer._estimate_tokens 的口径（静态方法，无需实例）。"""
    from src.summarize.base import AbstractSummarizer
    return AbstractSummarizer._estimate_tokens(messages)


def trim_oldest(messages: list, budget: int) -> tuple:
    """丢掉最旧的消息直到估算 token ≤ budget。

    Returns:
        (保留的消息, 丢弃条数)。保留部分仍按时间升序，两个调用方
        （摘要 prompt、agent 工具的时间范围计算）都依赖这一点。
        预算小到连一条都装不下时至少保留最新 1 条，绝不返回空。
    """
    n = len(messages)
    if n == 0:
        return [], 0
    if budget <= 0:
        return [], n
    if estimate_messages_tokens(messages) <= budget:
        return messages, 0

    lo, hi = 0, n            # 求最小的 i 使 messages[i:] 装得下
    while lo < hi:
        mid = (lo + hi) // 2
        if estimate_messages_tokens(messages[mid:]) <= budget:
            hi = mid
        else:
            lo = mid + 1
    if lo >= n:              # 单条消息本身就超预算
        return messages[-1:], n - 1
    return messages[lo:], lo


def plan_digest(units: list, budget: int, memory_tokens: int = 0) -> DigestPlan:
    """判定这一轮该打包还是降级，并按需裁剪最旧消息。

    四个分支：
      1. 只有一个有内容的会话 → `single`，追加 `SINGLE_HEADING_CONTRACT` 点名会话
         （避免记忆跨会话污染；不再保证与改造前逐字节一致）
      2. 全部装得下 → `packed`，一次调用
      3. 小幅超限（≤ PACKED_TRIM_SLACK_RATIO）→ 按占比均摊裁最旧，仍 `packed`
      4. 大幅超限 → `per_chat`，每会话独享完整 available，自身超限再裁

    注意 _estimate_tokens 每条消息带 500 的固定项，所以多会话求和会略微高估；
    方向是保守的（更早降级），可以接受。
    """
    active = [u for u in units if u.messages]
    available = budget - memory_tokens - SYSTEM_RESERVE_TOKENS
    notes: list = []
    if available < MIN_AVAILABLE_TOKENS:
        logger.warning("记忆占 %d token、预算 %d 过紧，available 抬到地板 %d",
                       memory_tokens, budget, MIN_AVAILABLE_TOKENS)
        available = MIN_AVAILABLE_TOKENS

    if len(active) <= 1:
        if active:
            u = active[0]
            if u.est_tokens > available:
                u.messages, u.dropped = trim_oldest(u.messages, available)
                u.est_tokens = estimate_messages_tokens(u.messages)
                notes.append(f"「{u.name}」超出 token 预算，已省略最早 {u.dropped} 条消息")
            return DigestPlan("single", units, budget, available, u.est_tokens, notes)
        return DigestPlan("single", units, budget, available, 0, notes)

    def _total() -> int:
        return (sum(u.est_tokens for u in active)
                + SECTION_OVERHEAD_TOKENS * len(active))

    total = _total()

    if total <= available:
        return DigestPlan("packed", units, budget, available, total, notes)

    over_ratio = (total - available) / available if available else float("inf")
    if over_ratio <= PACKED_TRIM_SLACK_RATIO:
        ratio = available / total
        for u in active:
            quota = max(int(u.est_tokens * ratio), MIN_UNIT_TOKENS)
            if u.est_tokens > quota:
                u.messages, u.dropped = trim_oldest(u.messages, quota)
                u.est_tokens = estimate_messages_tokens(u.messages)
                notes.append(f"「{u.name}」已省略最早 {u.dropped} 条消息以适配打包预算")
        new_total = _total()
        if new_total <= available:
            notes.insert(0, f"打包需 {total} token、超出预算 {available}，"
                            f"已按比例裁剪最旧消息（保留约 {int(ratio * 100)}%）")
            return DigestPlan("packed", units, budget, available, new_total, notes)
        # MIN_UNIT_TOKENS 地板导致仍超限 → 落到降级分支

    notes.insert(0, f"打包需 {total} token、超出预算 {available}，降级为逐会话摘要后拼接")
    for u in active:
        if u.est_tokens > available:
            u.messages, u.dropped = trim_oldest(u.messages, available)
            u.est_tokens = estimate_messages_tokens(u.messages)
            notes.append(f"「{u.name}」自身超出预算，已省略最早 {u.dropped} 条消息")
    return DigestPlan("per_chat", units, budget, available, _total(), notes)


def _format_msg_lines(messages: list) -> str:
    lines = []
    for m in messages:
        content = _strip_ids(m.get("content", "") or "")
        if not content:
            continue
        ts = m.get("timestamp", 0)
        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        lines.append(f"[{time_str}] {m.get('sender_name', '?')}: {content}")
    return "\n".join(lines)


def build_packed_prompt(group_cfg: DigestGroup, units: list) -> str:
    """多会话打包的 user prompt：记忆共用一份，消息按会话分节。"""
    memory = group_cfg.memory or "（暂无历史记忆）"
    active = [u for u in units if u.messages]
    sections = []
    for i, u in enumerate(active, 1):
        suffix = f"，已省略最早 {u.dropped} 条" if u.dropped else ""
        sections.append(
            f"=== [{i}] {u.name} ({len(u.messages)} 条{suffix}) ===\n"
            f"{_format_msg_lines(u.messages)}"
        )
    return f"""## 本次摘要的分组
{group_cfg.name}（{len(active)} 个会话）

## 近期记忆（本分组共用，可能含其他会话的事，只与对应会话相关时才引用）
{memory}

## 待摘要的 {len(active)} 个会话

{chr(10).join(sections)}"""


def _strip_leading_heading(text: str, name: str) -> str:
    """剥掉 LLM 自己加的会话名标题，避免拼接后出现两层标题。"""
    lines = (text or "").split("\n")
    if not lines:
        return text or ""
    first = lines[0].strip()
    if first.startswith("#"):
        bare = first.lstrip("#").strip()
        if bare in (name, f"群：{name}", f"会话：{name}", f"群:{name}", f"会话:{name}"):
            return "\n".join(lines[1:]).strip()
    return (text or "").strip()


def concat_sections(sections: list) -> str:
    """把逐会话摘要拼成最终正文 —— **纯字符串操作，不调用任何 LLM**。

    Args:
        sections: [(ChatUnit, 摘要正文, 错误串)]，按会话顺序。
    """
    blocks = []
    for unit, text, err in sections:
        lines = [f"## {unit.name}"]
        if unit.dropped:
            lines.append(f"> 已省略最早 {unit.dropped} 条消息（超出 token 预算）")
        if err:
            lines.append(f"（本次摘要失败：{err}）")
        else:
            body = _strip_leading_heading(text, unit.name)
            lines.append(body or "（无实质内容）")
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)
