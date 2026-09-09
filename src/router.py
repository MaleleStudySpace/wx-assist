"""Message router — stores incoming messages and triggers memory consolidation.

iLink DM messages are routed to Agent for processing.
WCDB group messages only trigger memory consolidation (no auto-reply).
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .memory.consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)

# ── Welcome system ──────────────────────────────────────────────────
WELCOME_FILE = Path("data/welcomed_users.json")

WELCOME_TEMPLATE = """\
您好呀，我是摘星，您的微信小助手 ☺️

微信连接成功啦！从现在起，这些事都可以交给我：

{tool_examples}

不确定从哪儿开始？随便跟我说一句试试，我立刻就办 ✨"""


_WELCOME_EXAMPLES = (
    "帮我看看系统状态",
    "现在有哪些定时摘要",
    "我盯着哪些群了",
    "有哪些公众号分组和实时提醒",
    "总结一下项目群",
    "看看这个公众号说了什么",
    "搜索聊天记录、公众号文章、朋友圈或收藏",
    "帮我管理定时任务和常用 AI 技能",
)


def _build_welcome_text(_tool_descriptions: str = "") -> str:
    """Build a short user-facing welcome without exposing tool schemas."""
    tool_section = "\n".join(f"- {example}" for example in _WELCOME_EXAMPLES)
    return WELCOME_TEMPLATE.format(tool_examples=tool_section)


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
        self._rag = None
        self._memory = MemoryConsolidator(store, summarizer)
        # Health monitoring: count unique messages processed (post-dedup)
        self.messages_processed: int = 0

    def set_agent_engine(self, engine):
        """Set/update agent_engine after router creation.

        Used when AgentEngine is created after Router (dependency order).
        """
        self._agent_engine = engine

    def set_rag(self, rag):
        """Set RAGEngine for incremental indexing. Called after Router init."""
        self._rag = rag

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

        # ── RAG incremental indexing (optional, zero impact) ──
        if self._rag:
            self._rag.ingest_one(msg, source="msg")

        # ── IM DM → Agent path ──
        chat_id = str(msg.get("chat_id", ""))
        is_qq_dm = chat_id.startswith("qqbot:") and self._message_is_dm(msg)
        is_feishu_dm = chat_id.startswith("feishu:") and self._message_is_dm(msg)
        if chat_id.startswith("ilink_") or is_qq_dm or is_feishu_dm:
            return self._handle_dm(msg)

        # ── WCDB group message → memory consolidation ──
        if self._config.memory_consolidation_enabled:
            self._memory.check_and_consolidate(msg["chat_id"])

        return None

    @staticmethod
    def _message_is_dm(msg: dict) -> bool:
        """Use explicit chat_type when present; never infer QQ groups as DMs."""
        return msg.get("chat_type") == "dm" or msg.get("is_group") is False

    def _handle_dm(self, msg: dict) -> Optional[str]:
        """Handle an iLink DM message via Agent."""
        if not self._agent_engine:
            logger.warning("DM received but Agent engine not initialized")
            return "处理失败：Agent 引擎未就绪，请稍后再试。"

        clean = msg["content"].strip()
        if not clean:
            return None

        # ── Welcome on first DM ──
        welcome_text = self._check_welcome(msg["chat_id"])

        try:
            chat_id = str(msg.get("chat_id", ""))
            if chat_id.startswith("qqbot:"):
                source_platform = "qqbot"
            elif chat_id.startswith("feishu:"):
                source_platform = "feishu"
            else:
                source_platform = "wechat"
            reply = self._agent_engine.run(
                user_message=clean,
                source_platform=source_platform,
                source_target=chat_id if source_platform != "wechat" else None,
                conversation_key=chat_id or "wechat:unknown",
            )
        except Exception as e:
            logger.exception("Agent run failed")
            return f"处理失败：{e}"

        if welcome_text:
            reply = f"{welcome_text}\n\n---\n\n{reply}"

        return reply

    # ── Welcome system ──────────────────────────────────────────────

    def _check_welcome(self, chat_id: str) -> str:
        """Return welcome text if this is the user's first DM."""
        welcomed = set()
        try:
            if WELCOME_FILE.exists():
                data = json.loads(WELCOME_FILE.read_text(encoding="utf-8"))
                welcomed = set(data.get("welcomed", []))
        except Exception as e:
            # 欢迎记录解析失败 → 该用户下次 DM 会被重复欢迎（自愈靠下方重写文件），
            # 但根因不可见，记录 warning。
            logger.warning("欢迎记录解析失败，将重建: %s", e)

        if chat_id in welcomed:
            return ""

        # Mark as welcomed
        welcomed.add(chat_id)
        try:
            WELCOME_FILE.parent.mkdir(parents=True, exist_ok=True)
            WELCOME_FILE.write_text(
                json.dumps({"welcomed": list(welcomed)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save welcomed user: %s", e)

        # Build welcome
        return _build_welcome_text()
