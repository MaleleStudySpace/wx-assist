"""Message router — stores incoming messages and triggers memory consolidation.

NOTE: The @mention → AI chat feature was fully removed as disabled functionality.
The router now only persists messages and optionally triggers memory consolidation.
"""

import logging
from typing import Optional

from .memory.consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)


class MessageRouter:
    """Routes incoming WeChat messages to persistence and memory consolidation.

    Usage:
        router = MessageRouter(
            store=message_store,
            summarizer=summarizer,
            config=bot_config,
        )

        def on_message(msg: dict) -> str | None:
            return router.handle(msg)
    """

    def __init__(self, store, summarizer, config):
        """
        Args:
            store: MessageStore instance for persistence and queries.
            summarizer: AbstractSummarizer instance (used by memory consolidation).
            config: BotConfig instance.
        """
        self._store = store
        self._config = config
        self._memory = MemoryConsolidator(store, summarizer)
        # Health monitoring: count unique messages processed (post-dedup)
        self.messages_processed: int = 0

    def handle(self, msg: dict) -> Optional[str]:
        """Process an incoming group chat message.

        Stores the message and triggers memory consolidation if enabled.

        Returns None (no automatic replies — the @mention AI chat feature
        has been removed).
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

        # Check memory consolidation trigger (fast no-op unless threshold hit)
        if self._config.memory_consolidation_enabled:
            self._memory.check_and_consolidate(msg["chat_id"])

        return None
