"""Task Center — unified task lifecycle tracking for digest operations.

Records every scheduled and manual digest task (group chat + OA) with
status transitions, progress text, and push results.  Backed by SQLite
so tasks survive bot restarts and can be queried via the API.

Design rules:
  - Every public method is wrapped in try/except — TaskCenter failures
    MUST NEVER break digest generation.
  - Uses per-operation connections (same pattern as outbox.py).
  - WAL mode for concurrent reads/writes.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/task_center.db")

BASE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS task_center (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT NOT NULL,           -- 'group_digest' | 'oa_digest'
    source          TEXT NOT NULL,           -- 'scheduler' | 'manual'
    group_id        TEXT NOT NULL,
    group_name      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    progress        TEXT DEFAULT '',         -- semantic progress text
    result          TEXT DEFAULT '',         -- completion summary (truncated digest)
    error           TEXT DEFAULT '',         -- failure reason
    articles_count  INTEGER DEFAULT 0,
    msg_count       INTEGER DEFAULT 0,
    push_status     TEXT DEFAULT '',         -- '' | 'pending_push' | 'success' | 'failed'
    push_error      TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tc_status ON task_center(status);
CREATE INDEX IF NOT EXISTS idx_tc_type ON task_center(task_type);
CREATE INDEX IF NOT EXISTS idx_tc_created ON task_center(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tc_group ON task_center(group_id);
"""


class TaskCenter:
    """Persistent task tracking backed by SQLite.

    Usage:
        tc = TaskCenter()
        tid = tc.create_task('group_digest', 'manual', 'xxx@chatroom', '测试群')
        tc.update_task(tid, status='running', progress='正在获取消息')
        tc.complete_task(tid, result='摘要生成完成', msg_count=42)
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Initialization ────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables and mark stale running tasks as failed (bot restart)."""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.executescript(BASE_SCHEMA)
                conn.commit()
            # Mark any leftover running tasks as failed (bot restarted)
            self._mark_stale_running_failed()
        except Exception as e:
            logger.warning("[TASK-CENTER] DB init failed: %s", e)

    def _mark_stale_running_failed(self) -> None:
        """Mark tasks stuck in 'running' as failed — happens after bot restart."""
        try:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "UPDATE task_center SET status='failed', error='bot 重启，任务中断', "
                    "finished_at=? WHERE status='running'",
                    (_now(),),
                )
                conn.commit()
                if cur.rowcount > 0:
                    logger.info("[TASK-CENTER] Marked %d stale running tasks as failed",
                                cur.rowcount)
        except Exception as e:
            logger.warning("[TASK-CENTER] Failed to mark stale tasks: %s", e)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Core CRUD ─────────────────────────────────────────────────────

    def create_task(self, task_type: str, source: str,
                    group_id: str, group_name: str) -> Optional[int]:
        """Insert a new task with status='pending'. Returns task ID or None on failure."""
        try:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO task_center "
                    "(task_type, source, group_id, group_name, status, progress, created_at) "
                    "VALUES (?, ?, ?, ?, 'pending', '准备中', ?)",
                    (task_type, source, group_id, group_name, _now()),
                )
                conn.commit()
                tid = cur.lastrowid
                logger.info("[TASK-CENTER] Created task #%d type=%s source=%s group=%s",
                            tid, task_type, source, group_name)
                return tid
        except Exception as e:
            logger.warning("[TASK-CENTER] create_task failed: %s", e)
            return None

    def update_task(self, task_id: int, **kwargs) -> bool:
        """Update arbitrary fields on a task. Auto-sets started_at when status→running."""
        if not task_id:
            return False
        try:
            with self._get_conn() as conn:
                # Auto-set started_at when transitioning to running
                new_status = kwargs.get('status', '')
                if new_status == 'running':
                    # Only set started_at if it's not already set
                    row = conn.execute(
                        "SELECT started_at FROM task_center WHERE id=?", (task_id,)
                    ).fetchone()
                    if row and not row['started_at']:
                        kwargs['started_at'] = _now()

                if not kwargs:
                    return False

                set_clause = ", ".join(f"{k}=?" for k in kwargs)
                values = list(kwargs.values()) + [task_id]
                conn.execute(
                    f"UPDATE task_center SET {set_clause} WHERE id=?",
                    values,
                )
                conn.commit()
                logger.debug("[TASK-CENTER] Task #%d updated: %s", task_id, kwargs)
                return True
        except Exception as e:
            logger.warning("[TASK-CENTER] update_task #%d failed: %s", task_id, e)
            return False

    def complete_task(self, task_id: int, result: str = "",
                      articles_count: int = 0, msg_count: int = 0) -> bool:
        """Mark task as completed with result summary."""
        if not task_id:
            return False
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE task_center SET status='completed', progress='完成', "
                    "result=?, articles_count=?, msg_count=?, finished_at=? "
                    "WHERE id=?",
                    (result[:500], articles_count, msg_count, _now(), task_id),
                )
                conn.commit()
                logger.info("[TASK-CENTER] Task #%d completed: result_len=%d",
                            task_id, len(result))
                return True
        except Exception as e:
            logger.warning("[TASK-CENTER] complete_task #%d failed: %s", task_id, e)
            return False

    def fail_task(self, task_id: int, error: str = "") -> bool:
        """Mark task as failed with error description."""
        if not task_id:
            return False
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE task_center SET status='failed', error=?, finished_at=? "
                    "WHERE id=?",
                    (error[:500], _now(), task_id),
                )
                conn.commit()
                logger.info("[TASK-CENTER] Task #%d failed: %s", task_id, error[:100])
                return True
        except Exception as e:
            logger.warning("[TASK-CENTER] fail_task #%d failed: %s", task_id, e)
            return False

    def update_push_result(self, task_id: int, push_status: str,
                           push_error: str = "") -> bool:
        """Update push delivery result for a task."""
        if not task_id:
            return False
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE task_center SET push_status=?, push_error=? WHERE id=?",
                    (push_status, push_error[:500], task_id),
                )
                conn.commit()
                logger.info("[TASK-CENTER] Task #%d push %s", task_id, push_status)
                return True
        except Exception as e:
            logger.warning("[TASK-CENTER] update_push_result #%d failed: %s", task_id, e)
            return False

    # ── Query ─────────────────────────────────────────────────────────

    def list_tasks(self, status: str = "", task_type: str = "",
                   limit: int = 50) -> list[dict]:
        """Return tasks filtered by status/type, newest first."""
        try:
            where = []
            params: list[object] = []
            if status:
                where.append("status = ?")
                params.append(status)
            if task_type:
                where.append("task_type = ?")
                params.append(task_type)
            clause = " WHERE " + " AND ".join(where) if where else ""
            params.append(max(1, min(int(limit or 50), 200)))
            with self._get_conn() as conn:
                rows = conn.execute(
                    f"SELECT * FROM task_center{clause} ORDER BY created_at DESC LIMIT ?",
                    params,
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("[TASK-CENTER] list_tasks failed: %s", e)
            return []

    def get_task(self, task_id: int) -> Optional[dict]:
        """Return a single task by ID."""
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM task_center WHERE id=?", (task_id,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.warning("[TASK-CENTER] get_task #%d failed: %s", task_id, e)
            return None

    def count_running(self) -> int:
        """Count tasks currently in 'running' status (for badge display)."""
        try:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM task_center WHERE status='running'"
                ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.warning("[TASK-CENTER] count_running failed: %s", e)
            return 0

    # ── Maintenance ───────────────────────────────────────────────────

    def cleanup_expired(self, max_age_hours: int = 72) -> int:
        """Delete completed/failed tasks older than max_age_hours.

        Never deletes pending or running tasks.
        Returns count of deleted rows.
        """
        try:
            cutoff = _now_offset(-max_age_hours * 3600)
            with self._get_conn() as conn:
                cur = conn.execute(
                    "DELETE FROM task_center "
                    "WHERE status IN ('completed', 'failed') AND finished_at < ?",
                    (cutoff,),
                )
                conn.commit()
                deleted = cur.rowcount
                if deleted > 0:
                    logger.info("[TASK-CENTER] Cleaned up %d expired tasks", deleted)
                return deleted
        except Exception as e:
            logger.warning("[TASK-CENTER] cleanup_expired failed: %s", e)
            return 0


# ── Helpers ───────────────────────────────────────────────────────────

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _now_offset(offset_sec: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset_sec))
