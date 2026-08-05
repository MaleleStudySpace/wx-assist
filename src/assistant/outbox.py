"""Assistant Outbox — persistent notification queue.

Stores keyword alerts and digest summaries for external agents or services to
pull and deliver to the user.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/assistant_outbox.db")

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    priority TEXT DEFAULT 'normal',
    group_name TEXT,
    title TEXT,
    content TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outbox_status ON assistant_outbox(status);
CREATE INDEX IF NOT EXISTS idx_outbox_created ON assistant_outbox(created_at);
"""

UPGRADE_STMTS = (
    "ALTER TABLE assistant_outbox ADD COLUMN chat_id TEXT",
    "ALTER TABLE assistant_outbox ADD COLUMN push_channel TEXT DEFAULT ''",
    "ALTER TABLE assistant_outbox ADD COLUMN push_status TEXT DEFAULT ''",
    "ALTER TABLE assistant_outbox ADD COLUMN push_error TEXT DEFAULT ''",
    "ALTER TABLE assistant_outbox ADD COLUMN push_at TEXT",
    # ── 用于 OAMonitorEngine dedup 精确查询（按文章 url） ──
    "ALTER TABLE assistant_outbox ADD COLUMN url TEXT DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_outbox_chat_id ON assistant_outbox(chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_type ON assistant_outbox(type)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_push_status ON assistant_outbox(push_status)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_url ON assistant_outbox(url)",
)


class Outbox:
    """Persistent notification outbox backed by SQLite.

    Usage:
        outbox = Outbox()
        nid = outbox.add("keyword_alert", "123@chatroom", "抢单群A", "新订单", "张三: 急单...")
        pending = outbox.get_pending(limit=20)
        outbox.ack(pending[0]["id"])
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.executescript(BASE_SCHEMA)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(assistant_outbox)").fetchall()
            }
            # Migration: add columns if missing
            migrations = [
                ("chat_id", "ALTER TABLE assistant_outbox ADD COLUMN chat_id TEXT"),
                ("push_channel", "ALTER TABLE assistant_outbox ADD COLUMN push_channel TEXT DEFAULT ''"),
                ("push_status", "ALTER TABLE assistant_outbox ADD COLUMN push_status TEXT DEFAULT ''"),
                ("push_error", "ALTER TABLE assistant_outbox ADD COLUMN push_error TEXT DEFAULT ''"),
                ("push_at", "ALTER TABLE assistant_outbox ADD COLUMN push_at TEXT"),
                ("url", "ALTER TABLE assistant_outbox ADD COLUMN url TEXT DEFAULT ''"),
            ]
            for col_name, alter_sql in migrations:
                if col_name not in columns:
                    try:
                        conn.execute(alter_sql)
                    except sqlite3.OperationalError:
                        pass
            # Add indexes
            for stmt in (
                "CREATE INDEX IF NOT EXISTS idx_outbox_chat_id ON assistant_outbox(chat_id)",
                "CREATE INDEX IF NOT EXISTS idx_outbox_type ON assistant_outbox(type)",
                "CREATE INDEX IF NOT EXISTS idx_outbox_push_status ON assistant_outbox(push_status)",
                "CREATE INDEX IF NOT EXISTS idx_outbox_url ON assistant_outbox(url)",
            ):
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass
            # 回填旧记录的 url（从 content JSON 提取）——
            # 旧版本没有 url 列，推送记录只有 content JSON 里带 url。
            # 不回填则 OAMonitorEngine.query_by_url 匹配不到旧推送，
            # 导致已推送过的文章被判 is_known=False 而重复推送。
            _backfilled = 0
            try:
                import json as _json  # 局部导入（文件顶部未 import json）
                _rows = conn.execute(
                    "SELECT id, content FROM assistant_outbox "
                    "WHERE type='oa_article_alert' AND (url IS NULL OR url='')"
                ).fetchall()
                for _rid, _content in _rows:
                    try:
                        _d = _json.loads(_content)
                        _u = (_d.get("url") or "").strip()
                        if _u:
                            conn.execute(
                                "UPDATE assistant_outbox SET url=? WHERE id=?",
                                (_u, _rid),
                            )
                            _backfilled += 1
                    except Exception as e:
                        # 批量循环内单条失败：该条 url 永空 → 重复推送。
                        logger.debug("Outbox url 回填单条失败 (id=%s): %s", _rid, e)
            except Exception as e:
                # 回填整体中断，_backfilled 计数丢失。
                logger.warning("Outbox url 回填中断: %s", e)
            if _backfilled:
                logger.info("Outbox: 回填 %d 条历史推送记录的 url（防重复推送）", _backfilled)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, notif_type: str, group_name: str, title: str,
            content: str, priority: str = "normal", chat_id: str = "",
            url: str = "") -> int:
        """Add a notification and return its ID.

        Args:
            url: Optional URL for dedup (used by OAMonitorEngine to check
                 if an OA article was already alerted).
        """
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO assistant_outbox (type, priority, chat_id, group_name, title, content, url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (notif_type, priority, chat_id, group_name, title, content, url, _now()),
            )
            conn.commit()
            nid = cur.lastrowid
            logger.info("Outbox: #%d type=%s group=%s title=%s", nid, notif_type, group_name, title)
            return nid

    def get_pending(self, limit: int = 20) -> list[dict]:
        """Return pending notifications, oldest first."""
        return self.list_notifications(status="pending", limit=limit)

    def get_notification(self, notif_id: int) -> Optional[dict]:
        """Return a single notification by ID.

        供任务中心"重新推送"使用 —— 通过 task.outbox_id 取完整
        title + content，重推原始推送内容。
        """
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM assistant_outbox WHERE id=?", (notif_id,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.warning("Outbox: get_notification #%d failed: %s", notif_id, e)
            return None

    def list_notifications(self, chat_id: str = "", group_name: str = "",
                           notif_type: str = "", status: str = "",
                           limit: int = 50) -> list[dict]:
        """Return notifications filtered for Dashboard browsing."""
        where = []
        params: list[object] = []
        if chat_id:
            where.append("chat_id = ?")
            params.append(chat_id)
        if group_name:
            where.append("group_name = ?")
            params.append(group_name)
        if notif_type:
            where.append("type = ?")
            params.append(notif_type)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        params.append(max(1, min(int(limit or 50), 200)))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM assistant_outbox{clause} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def query_by_url(self, url: str, notif_type: str = "",
                     only_success: bool = False) -> bool:
        """Check whether a notification with this URL already exists.

        Used by OAMonitorEngine for accurate dedup. Empty url → False.

        Args:
            url: Article URL.
            notif_type: Optional filter (e.g. "oa_article_alert").
            only_success: True 时只认"推送成功"的记录（push_status='success'）。
                推送失败/进行中的记录不算已推送 → 调用方可在窗口内重试。
        """
        if not url:
            return False
        with self._get_conn() as conn:
            if only_success:
                row = conn.execute(
                    "SELECT 1 FROM assistant_outbox WHERE url=? AND type=? "
                    "AND push_status='success' LIMIT 1",
                    (url, notif_type or ""),
                ).fetchone()
            elif notif_type:
                row = conn.execute(
                    "SELECT 1 FROM assistant_outbox WHERE url=? AND type=? LIMIT 1",
                    (url, notif_type),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM assistant_outbox WHERE url=? LIMIT 1",
                    (url,),
                ).fetchone()
            return row is not None

    def ack(self, notif_id: int) -> bool:
        """Mark a notification as delivered."""
        return self._update_status(notif_id, "delivered")

    def ignore(self, notif_id: int) -> bool:
        """Mark a notification as ignored."""
        return self._update_status(notif_id, "ignored")

    def _update_status(self, notif_id: int, status: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE assistant_outbox SET status = ?, delivered_at = ? WHERE id = ?",
                (status, _now(), notif_id),
            )
            conn.commit()
            if cur.rowcount > 0:
                logger.info("Outbox: #%d → %s", notif_id, status)
                return True
            logger.warning("Outbox: #%d not found for status update", notif_id)
            return False

    def cleanup_expired(self, retention_hours: int = 24) -> int:
        """Delete notifications older than retention_hours.

        Only cleans delivered/ignored/failed — never pending.
        Returns count of deleted rows.
        """
        cutoff = _now_offset(-retention_hours * 3600)
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM assistant_outbox WHERE status != 'pending' AND created_at < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = cur.rowcount
            if deleted > 0:
                logger.info("Outbox: cleaned up %d expired notifications", deleted)
            return deleted

    def count_pending(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM assistant_outbox WHERE status = 'pending'"
            ).fetchone()
            return row[0] if row else 0

    def update_push_result(self, notif_id: int, push_channel: str,
                           push_status: str, push_error: str = "") -> bool:
        """Update push delivery result for a notification.

        注意：此处只更新推送结果字段（push_status/push_error/push_at），
        不修改 status —— status（pending/delivered/ignored）是投递生命周期，
        由外部调用方通过 ack()/ignore() 管理，与微信推送结果无关。

        Args:
            notif_id: Notification ID
            push_channel: 'ilink' etc.
            push_status: 'success' / 'failed' / 'pending_push'
            push_error: Error message (truncated to 500 chars)
        """
        push_error = (push_error or "")[:500]
        push_at = _now() if push_status != "pending_push" else None
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE assistant_outbox SET push_channel=?, push_status=?, push_error=?, push_at=? WHERE id=?",
                (push_channel, push_status, push_error, push_at, notif_id),
            )
            conn.commit()
            if cur.rowcount > 0:
                logger.info("Outbox: #%d push %s via %s", notif_id, push_status, push_channel)
                return True
            return False

    def list_push_history(self, notif_type: str = "", push_status: str = "",
                          limit: int = 50, offset: int = 0,
                          date_from: str = "", date_to: str = "") -> list[dict]:
        """Return notifications with push info, for push history page."""
        where = []
        params: list[object] = []
        # Only include notifications that have push info
        where.append("push_channel != ''")
        if notif_type:
            where.append("type = ?")
            params.append(notif_type)
        if push_status:
            where.append("push_status = ?")
            params.append(push_status)
        if date_from:
            where.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("created_at <= ?")
            params.append(date_to + "T23:59:59")
        clause = " WHERE " + " AND ".join(where) if where else ""
        params.append(max(1, min(int(limit or 50), 200)))
        params.append(max(0, int(offset or 0)))
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM assistant_outbox{clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_push_stats(self) -> dict:
        """Return push statistics: today's count, success rate, 7-day trend."""
        today = time.strftime("%Y-%m-%d", time.localtime())
        with self._get_conn() as conn:
            # Today's stats
            row = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN push_status='success' THEN 1 ELSE 0 END) as success "
                "FROM assistant_outbox WHERE push_channel != '' AND created_at >= ?",
                (today,),
            ).fetchone()
            today_total = row[0] if row else 0
            today_success = row[1] if row else 0

            # 7-day trend
            cutoff = _now_offset(-7 * 86400)
            rows = conn.execute(
                "SELECT SUBSTR(created_at,1,10) as day, "
                "COUNT(*) as total, "
                "SUM(CASE WHEN push_status='success' THEN 1 ELSE 0 END) as success "
                "FROM assistant_outbox WHERE push_channel != '' AND created_at >= ? "
                "GROUP BY day ORDER BY day",
                (cutoff,),
            ).fetchall()
            trend = [{"date": r[0], "total": r[1], "success": r[2]} for r in rows]

            return {
                "today_total": today_total,
                "today_success": today_success,
                "today_rate": round(today_success / today_total * 100, 1) if today_total > 0 else 0,
                "trend": trend,
            }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _now_offset(offset_sec: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset_sec))
