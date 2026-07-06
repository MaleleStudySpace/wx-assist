"""Database schema and initialization.

Idempotent — safe to call on every startup.
"""

import logging
import sqlite3
from pathlib import Path


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;

-- Every message received from any monitored group chat
-- message_id 是主键（基于微信 server_id 的 MD5），不自增
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT    PRIMARY KEY,
    chat_id     TEXT    NOT NULL,
    sender_id   TEXT    NOT NULL,
    sender_name TEXT    NOT NULL,
    content     TEXT    NOT NULL DEFAULT '',
    msg_type    INTEGER NOT NULL DEFAULT 1,
    timestamp   INTEGER NOT NULL,
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Fast lookup: "all messages in chat X since time T"
CREATE INDEX IF NOT EXISTS idx_msg_chat_time
    ON messages(chat_id, timestamp DESC);

-- Fast lookup: "last message from user S in chat X"
CREATE INDEX IF NOT EXISTS idx_msg_chat_sender_time
    ON messages(chat_id, sender_id, timestamp DESC);

-- Materialized last-message-time per user per chat.
-- Updated via UPSERT on every message insert.
CREATE TABLE IF NOT EXISTS user_last_message (
    chat_id        TEXT    NOT NULL,
    sender_id      TEXT    NOT NULL,
    sender_name    TEXT    NOT NULL,
    last_timestamp INTEGER NOT NULL,
    PRIMARY KEY (chat_id, sender_id)
);

-- Prevent duplicate trigger responses.
CREATE TABLE IF NOT EXISTS trigger_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id            TEXT    NOT NULL,
    requester_id       TEXT    NOT NULL,
    trigger_message_id TEXT    NOT NULL,
    processed_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

-- Per-group long-term memory. Consolidates conversation history into
-- a first-person diary-style text that is injected into every prompt,
-- giving the bot a persistent sense of identity and group awareness.
CREATE TABLE IF NOT EXISTS group_memory (
    chat_id           TEXT    PRIMARY KEY,
    memory_text       TEXT    NOT NULL DEFAULT '',
    message_count     INTEGER NOT NULL DEFAULT 0,
    last_message_id   TEXT,
    last_consolidated REAL,
    created_at        REAL    NOT NULL DEFAULT (unixepoch()),
    updated_at        REAL    NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_trigger_chat_time
    ON trigger_log(chat_id, processed_at DESC);
"""


def initialize_db(db_path: str) -> sqlite3.Connection:
    """Initialize the SQLite database, creating tables if they don't exist.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sqlite3.Connection with WAL mode enabled and row_factory set.
    """
    # Ensure the parent directory exists
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # 检测并迁移旧版 messages 表（去掉自增 id，改用 message_id 做主键）
    _migrate_old_schema(conn)

    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _migrate_old_schema(conn: sqlite3.Connection) -> None:
    """将旧版 messages 表（id AUTOINCREMENT）迁移到新版（message_id PRIMARY KEY）。

    旧表有 id INTEGER PRIMARY KEY AUTOINCREMENT + message_id TEXT UNIQUE，
    INSERT OR IGNORE 遇到重复时 id 照涨，使用自增 id 作游标会导致 1.36 亿的空档。
    新版去掉自增 id，message_id 直接做主键，rowid 始终等于实际行数。
    """
    try:
        # 检测是否还在用旧 schema（存在 id 列）
        columns = [col[1] for col in
                   conn.execute("PRAGMA table_info(messages)").fetchall()]
        if "id" not in columns:
            return  # 已经是新 schema

        logger = logging.getLogger(__name__)
        logger.info("[DB] 检测到旧版 messages 表，正在迁移到 message_id PRIMARY KEY...")

        # 创建新表
        conn.execute("""
            CREATE TABLE messages_new (
                message_id  TEXT    PRIMARY KEY,
                chat_id     TEXT    NOT NULL,
                sender_id   TEXT    NOT NULL,
                sender_name TEXT    NOT NULL,
                content     TEXT    NOT NULL DEFAULT '',
                msg_type    INTEGER NOT NULL DEFAULT 1,
                timestamp   INTEGER NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)

        # 复制数据（INSERT OR IGNORE 去重）
        conn.execute("""
            INSERT OR IGNORE INTO messages_new
                (message_id, chat_id, sender_id, sender_name,
                 content, msg_type, timestamp, created_at)
            SELECT message_id, chat_id, sender_id, sender_name,
                   content, msg_type, timestamp, created_at
            FROM messages
        """)
        row_count = conn.execute("SELECT COUNT(*) FROM messages_new").fetchone()[0]

        # 重建索引
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_msg_chat_time
                ON messages_new(chat_id, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_msg_chat_sender_time
                ON messages_new(chat_id, sender_id, timestamp DESC)
        """)

        # 切换表
        conn.execute("DROP TABLE messages")
        conn.execute("ALTER TABLE messages_new RENAME TO messages")
        conn.commit()

        logger.info("[DB] 迁移完成: %d 条消息，schema: message_id PRIMARY KEY", row_count)

        # 清空旧版 rag_state（last_indexed_msg_id 指向旧的自增 id=1.36 亿，
        # 迁移后 rowid 已重置为 1-22000，旧值无效，冷启动需要从 0 开始）
        try:
            state_file = Path("data/rag_state.json")
            if state_file.exists():
                state_file.unlink()
                logger.info("[DB] 已清空 rag_state.json，冷启动将从头索引")
        except Exception:
            pass
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("[DB] schema 迁移失败（可忽略，继续使用旧表）: %s", e)
