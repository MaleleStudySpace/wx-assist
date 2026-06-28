"""Message router — receives standardized messages and dispatches to handlers.

Routes messages to three response modes:
1. Admin commands: @bot mention + admin wxid → AdminCommandHandler
2. AI chat: @bot mention → summarizer.chat()
3. Memory consolidation trigger (configurable, default off)
"""

import logging
import re
import time
from typing import Optional

from .memory.consolidator import MemoryConsolidator
from .utils.op_logger import op_log

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────
CHAT_CONTEXT_WINDOW_SEC = 600      # fetch last N seconds of chat as context for @mentions
MAX_CONTENT_LENGTH = 997           # max chars per message sent to AI (997 + "..." = 1000)
MAX_CONTENT_LINES = 20             # max context lines fed to AI chat prompt
AT_MENTION_MAX_AGE_SEC = 300       # ignore @mentions older than 5 minutes (startup safety)

# Markdown patterns to strip before sending to WeChat.
# These regexes may miss edge cases like nested formatting or asterisks at
# line boundaries.  AI output typically uses simple bold/italic/code/
# strikethrough — these patterns handle 99% of cases.
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE = re.compile(r"~~(.+?)~~")
_MD_CODE = re.compile(r"`(.+?)`")


class MessageRouter:
    """Routes incoming WeChat messages to the correct handler.

    Usage:
        router = MessageRouter(
            store=message_store,
            detector=trigger_detector,
            summarizer=summarizer,
            admin_handler=admin_handler,
            nickname_service=nickname_service,
            config=bot_config,
        )

        def on_message(msg: dict) -> str | None:
            return router.handle(msg)
    """

    def __init__(self, store, detector, summarizer, admin_handler,
                 nickname_service, config):
        """
        Args:
            store: MessageStore instance for persistence and queries.
            detector: TriggerDetector instance for keyword matching.
            summarizer: AbstractSummarizer instance for AI responses.
            admin_handler: AdminCommandHandler instance.
            nickname_service: NicknameService instance.
            config: BotConfig instance.
        """
        self._store = store
        self._detector = detector
        self._summarizer = summarizer
        self._admin = admin_handler
        self._nicks = nickname_service
        self._config = config
        self._memory = MemoryConsolidator(store, summarizer)
        # Health monitoring: count unique messages processed (post-dedup)
        self.messages_processed: int = 0

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove markdown formatting characters that WeChat can't render."""
        text = _MD_BOLD.sub(r"\1", text)
        text = _MD_ITALIC.sub(r"\1", text)
        text = _MD_STRIKE.sub(r"\1", text)
        text = _MD_CODE.sub(r"\1", text)
        return text.strip()

    def handle(self, msg: dict) -> Optional[str]:
        """Process an incoming group chat message.

        Returns reply text if a reply should be sent, or None.
        """
        # Skip messages from the bot itself (prevent infinite loops).
        # Use a forgiving match — WeChat display names can vary slightly
        # (extra spaces, punctuation, emoji suffixes) from what's in .env.
        bot_name = "群聊小助手"
        if bot_name and msg["sender_name"].strip() == bot_name:
            return None

        # Always persist the message
        stored = self._store.insert_message(msg)
        if not stored:
            return None  # Duplicate — nothing more to do
        self.messages_processed += 1

        # Check memory consolidation trigger (fast no-op unless threshold hit)
        if self._config.memory_consolidation_enabled:
            self._memory.check_and_consolidate(msg["chat_id"])

        # ── Route: @mention → AI chat ────────────────────────────
        is_at = msg["is_at_mentioned"]

        if is_at:
            # ── @mention path ────────────────────────────────────

            # Guard: ignore stale @mentions
            msg_age_sec = int(time.time()) - msg.get("timestamp", 0)
            if msg_age_sec > AT_MENTION_MAX_AGE_SEC:
                op_log("MSG-RECV", "忽略过期@提及 sender='%s' age=%ds", msg["sender_name"], msg_age_sec)
                logger.info(
                    "Ignoring stale @mention from '%s' (age=%ds, max=%ds)",
                    msg["sender_name"], msg_age_sec, AT_MENTION_MAX_AGE_SEC,
                )
                return None

            logger.info(
                "Trigger in %s by '%s': %s",
                msg["chat_id"], msg["sender_name"], msg["content"][:80],
            )

            clean_content = msg["content"]
            # WeChat @mentions use @wxid<invisible_separator>text format.
            # The separator (U+2005, U+200B, U+FEFF, etc.) is not removed
            # by str.strip().  Use a regex to strip @bot_name + any
            # trailing non-word chars in one pass.
            clean_content = re.sub(
                re.escape("@群聊小助手") + r"[^\w]*",
                "", msg["content"]
            ).strip()

            # Strip WeChat reply-quote prefix: wxid_xxx:\n
            # When a user replies to a message then @mentions the bot,
            # WeChat prepends "wxid_<replied_user_id>:\n" to the content.
            clean_content = re.sub(
                r'^\s*wxid_[a-zA-Z0-9]+:\s*', '', clean_content,
            )

            reply: Optional[str] = None

            if clean_content.strip() in ("帮助", "help", "命令"):
                reply = self._admin.handle(clean_content, msg["sender_name"])

            if reply is None and (
                self._config.admin_wxid
                and msg["sender_id"] == self._config.admin_wxid
            ):
                reply = self._admin.handle(clean_content, msg["sender_name"])

            if reply is None and clean_content:
                reply = self._handle_chat(msg, clean_content)

        else:
            return None

        # ── Strip markdown — WeChat can't render it ──────────────
        return self._strip_markdown(reply) if reply else None

    # ── Memory helper ────────────────────────────────────────────

    def _get_group_memory(self, chat_id: str) -> str:
        """Return the group's memory text, or empty string if none."""
        mem = self._store.get_group_memory(chat_id)
        return mem["memory_text"] if mem else ""

    # ── AI Chat handler ──────────────────────────────────────────

    def _handle_chat(self, msg: dict, clean_content: str) -> str | None:
        """Handle a conversational @bot mention."""
        # Resolve custom nickname from file (via sender_id=wxid),
        # not sender_name which may already be a WeFlow default.
        display_name = self._nicks.resolve_name(msg["sender_id"])
        if display_name == msg["sender_id"]:
            display_name = msg["sender_name"]

        logger.info(
            "[LLM-ROUTE] chat | requester=%s | group=%s | content='%s'",
            display_name, msg.get("group_name", msg["chat_id"][:20]), clean_content[:60],
        )
        logger.info(
            "AI chat: '%s' asks '%s'",
            display_name, clean_content[:60],
        )

        # Always fetch recent chat context for @mentions.
        # The bot is @mentioned inside a group conversation — the surrounding
        # chat is almost always relevant.  Keyword-based gating (e.g. "刚才",
        # "之前") is too brittle: natural language has countless ways to
        # reference prior chat without those specific words ("挑一件事评价一下",
        # "怎么看", "那件事", etc.).
        since = int(time.time()) - CHAT_CONTEXT_WINDOW_SEC
        context = self._store.get_messages_since(
            msg["chat_id"], since, limit=20,
        )
        if context:
            for m in context:
                custom = self._nicks.resolve_name(m["sender_id"])
                if custom != m["sender_id"]:
                    m["sender_name"] = custom
            logger.info(
                "Chat context: %d messages for '%s'",
                len(context), display_name,
            )

        try:
            ai_reply = self._summarizer.chat(
                message=clean_content,
                context_messages=context,
                requester_name=display_name,
                bot_name="群聊小助手",
                group_name=msg.get("group_name", msg.get("chat_id", "群聊")),
                group_memory=self._get_group_memory(msg["chat_id"]),
            )
            ai_reply = self._nicks.resolve_wxids(ai_reply)
            # Guard against empty AI reply — sending a bare @mention is confusing
            if not ai_reply or not ai_reply.strip():
                logger.warning(
                    "AI chat returned empty for '%s' in %s",
                    display_name, msg.get("group_name", msg["chat_id"][:20]),
                )
                return None
            return f"@{display_name} {ai_reply}"
        except Exception as e:
            logger.error("AI chat failed: %s", e)
            return f"@{display_name} 大脑短路了，稍等再试～"

