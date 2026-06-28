"""Digest engine — generates timed group chat summaries with filtering and memory."""

import logging
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
根据提供的群信息、近期记忆和最新消息，生成一份结构化摘要。

## 输出要求
- 用中文，简洁自然，像给同事转述一样。
- 按话题分类，每个话题用 ## 二级标题。
- 突出可行动的信息：待办事项、决定、截止时间、联系方式。
- 忽略闲聊、表情、无实质内容的消息。
- 如果群档案标注了关注点，优先总结相关内容。
- 如果群档案标注了忽略内容，跳过相关消息。
- 如果没有实质性内容，一句话说清楚即可。
- 不要输出 wxid_xxx——始终用消息里的昵称。
- 不要加"群聊气象"小结。
- 不要加前缀如"@xxx 你错过的："。"""

# ── Style preset instructions (appended to DIGEST_SYSTEM_PROMPT) ──
STYLE_PRESETS = {
    "行动项优先": "\n\n## 摘要风格\n只输出可行动的信息：待办事项、决定、截止时间、负责人、联系方式。省略闲聊和讨论过程。如果没有任何行动项，直接说「无待办」。",
    "完整复盘": "\n\n## 摘要风格\n完整复盘所有话题，包括讨论过程、不同观点和最终结论。保留金句和有趣互动。",
    "极简速览": "\n\n## 摘要风格\n极简输出，每条摘要不超过一句话，只保留最重要的3-5个要点。用 • 列表格式。",
}


def filter_messages(messages: list[dict], ignore_keywords: Optional[list[str]] = None) -> list[dict]:
    """Filter low-value messages from a list.

    Removes:
    - System messages (join/leave/rename/etc.)
    - Very short messages
    - Common meaningless replies
    - Messages matching ignore keywords
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

        # Skip pure image/voice/video placeholders
        if content in ("[图片]", "[语音]", "[视频]", "[表情]", "[文件]", "[链接]"):
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

    # Format profile section
    profile_lines = []
    if profile:
        if profile.summary:
            profile_lines.append(f"群简介: {profile.summary}")
        # Fallback for legacy data without summary
        elif profile.purpose or profile.description:
            if profile.purpose:
                profile_lines.append(f"群用途: {profile.purpose}")
            if profile.description:
                profile_lines.append(f"群说明: {profile.description}")
        if profile.focus:
            profile_lines.append(f"关注点: {', '.join(profile.focus)}")
        if profile.ignore:
            profile_lines.append(f"忽略内容: {', '.join(profile.ignore)}")
    profile_text = "\n".join(profile_lines) if profile_lines else "（未配置群档案）"

    # Format messages
    msg_lines = []
    for m in messages:
        sender = m.get("sender_name", "?")
        content = m.get("content", "")
        ts = m.get("timestamp", 0)
        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        if content:
            msg_lines.append(f"[{time_str}] {sender}: {content}")

    messages_text = "\n".join(msg_lines)

    logger.info("[DIGEST-PROMPT] Building digest prompt: profile=%s, memory_len=%d, messages=%d",
                 bool(profile), len(memory), len(messages))

    return f"""## 群信息
{profile_text}

## 近期记忆
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

请用第一人称写一段 500 字以内的"摘要记忆"，记录:
- 本次摘要的核心要点
- 近期重要事件/趋势变化
- 群聊氛围和活跃度
直接输出记忆文本，不要 JSON 包装。"""
