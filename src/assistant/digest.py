"""Digest engine — generates timed group chat summaries with filtering and memory."""

import logging
import re
import time
from typing import Optional

from .config import AssistantConfig, DigestGroup

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
你是一个微信群聊定时摘要助手，负责定期为用户生成群聊摘要。

## 核心任务
根据提供的近期记忆和最新消息，生成一份结构化摘要。

## 输出要求
- 用中文，简洁自然，像给同事转述一样。
- 按话题分类，每个话题用 ## 二级标题。
- 突出可行动的信息：待办事项、决定、截止时间、联系方式。
- 忽略闲聊、表情、无实质内容的消息。
- 近期记忆是历史摘要的浓缩，用于保持话题连贯；如与最新消息冲突，以最新消息为准。
- 如果没有实质性内容，一句话说清楚即可。
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


def build_digest_prompt(group_cfg: DigestGroup, messages: list[dict]) -> str:
    """Build the user prompt for AI digest generation.

    Provides CONTEXT only (profile + memory + messages).
    Instructions belong in the system prompt (DIGEST_SYSTEM_PROMPT or custom_prompt).
    """
    profile = group_cfg.profile
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

    logger.info("[DIGEST-PROMPT] Building digest prompt: profile=%s, memory_len=%d, messages=%d",
                 bool(profile), len(memory), len(messages))

    return f"""## 近期记忆
{memory}

## 最近 {len(messages)} 条消息
{messages_text}"""


def generate_memory_update_prompt(previous_memory: str, digest_text: str) -> str:
    """Build the prompt for AI to update the group's digest memory."""
    prev = previous_memory if previous_memory else "（暂无）"
    return f"""## 之前的摘要记忆
{prev}

## 本次摘要
{digest_text}

请用第一人称写一段 2000 字以内的"摘要记忆"，记录:
- 本次摘要的核心要点
- 近期重要事件/趋势变化
- 群聊氛围和活跃度
直接输出记忆文本，不要 JSON 包装。"""
