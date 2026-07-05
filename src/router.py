"""Message router — stores incoming messages and triggers memory consolidation.

iLink DM messages are routed to Agent for processing.
WCDB group messages only trigger memory consolidation (no auto-reply).
"""

import logging
from typing import Optional

from .memory.consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)


class MessageRouter:
    """Routes incoming WeChat messages to persistence and (for iLink DM) Agent.

    Usage:
        router = MessageRouter(
            store=message_store,
            summarizer=summarizer,
            config=bot_config,
            agent_engine=agent_engine,  # optional
        )

        def on_message(msg: dict) -> str | None:
            return router.handle(msg)
    """

    def __init__(self, store, summarizer, config, agent_engine=None):
        """
        Args:
            store: MessageStore instance for persistence and queries.
            summarizer: AbstractSummarizer instance (used by memory consolidation).
            config: BotConfig instance.
            agent_engine: Optional AgentEngine for iLink DM routing.
        """
        self._store = store
        self._config = config
        self._agent_engine = agent_engine
        self._memory = MemoryConsolidator(store, summarizer)
        # Health monitoring: count unique messages processed (post-dedup)
        self.messages_processed: int = 0

    def set_agent_engine(self, engine):
        """Set/update agent_engine after router creation.

        Used when AgentEngine is created after Router (dependency order).
        """
        self._agent_engine = engine

    def handle(self, msg: dict) -> Optional[str]:
        """Process an incoming message.

        For iLink DM: persist + route to Agent for reply.
        For WCDB group: persist + memory consolidation (no auto-reply).

        Returns:
            Reply text (for iLink DM Agent path) or None.
        """
        # Skip messages from the bot itself (prevent infinite loops).
        bot_name = "群聊小助手"
        if bot_name and msg["sender_name"].strip() == bot_name:
            return None

        # Always persist the message
        stored = self._store.insert_message(msg)
        if not stored:
            return None  # Duplicate — nothing more to do
        self.messages_processed += 1

        # ── iLink DM → Agent path ──
        if msg["chat_id"].startswith("ilink_"):
            return self._handle_dm(msg)

        # ── WCDB group message → memory consolidation ──
        if self._config.memory_consolidation_enabled:
            self._memory.check_and_consolidate(msg["chat_id"])

        return None

    def _handle_dm(self, msg: dict) -> Optional[str]:
        """Handle an iLink DM message via Agent."""
        if not self._config.ai_agent_enabled or not self._agent_engine:
            return "Agent 功能未启用，请在系统配置中开启。"

        clean = msg["content"].strip()
        if not clean:
            return None

        try:
            return self._agent_engine.run(user_message=clean)
        except Exception as e:
            logger.exception("Agent run failed")
            return f"处理失败：{e}"
