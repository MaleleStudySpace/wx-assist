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

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "data" / "task_center.db"

BASE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS task_center (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT NOT NULL,           -- 'group_digest' | 'oa_digest' | 'cron'
    source          TEXT NOT NULL,           -- 'scheduler' | 'manual'
    group_id        TEXT NOT NULL,
    group_name      TEXT NOT NULL,
    config          TEXT DEFAULT '',         -- JSON: 执行时的完整任务配置快照
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    progress        TEXT DEFAULT '',         -- semantic progress text
    result          TEXT DEFAULT '',         -- completion summary (digest full text, no truncation)
    error           TEXT DEFAULT '',         -- failure reason
    articles_count  INTEGER DEFAULT 0,
    msg_count       INTEGER DEFAULT 0,
    push_status     TEXT DEFAULT '',         -- '' | 'pending_push' | 'success' | 'partial' | 'failed'
    push_error      TEXT DEFAULT '',
    outbox_id       INTEGER DEFAULT 0,       -- 关联 outbox 记录（重推时取完整推送内容）
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
        tid = tc.create_task('group_digest', 'manual', 'dg_001', '聚沙成塔')
        tc.update_task(tid, status='running', progress='正在获取消息 (1/3)')
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
                # Schema migration: add columns if missing
                cols = {row[1] for row in conn.execute("PRAGMA table_info(task_center)").fetchall()}
                for col_name, alter_sql in (
                    ("config", "ALTER TABLE task_center ADD COLUMN config TEXT DEFAULT ''"),
                    ("outbox_id", "ALTER TABLE task_center ADD COLUMN outbox_id INTEGER DEFAULT 0"),
                ):
                    if col_name not in cols:
                        try:
                            conn.execute(alter_sql)
                        except sqlite3.OperationalError:
                            pass
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
                    group_id: str, group_name: str,
                    config: str = "", outbox_id: int = 0) -> Optional[int]:
        """Insert a new task with status='pending'. Returns task ID or None on failure."""
        try:
            with self._get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO task_center "
                    "(task_type, source, group_id, group_name, config, outbox_id, status, progress, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', '准备中', ?)",
                    (task_type, source, group_id, group_name, config, int(outbox_id or 0), _now()),
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
        """Mark task as completed with result summary.

        result 不再截断到 500 字 —— 重推功能需要完整摘要内容。
        仍保留 50000 字符安全上限（防 cron 超长输出撑爆数据库）。
        """
        if not task_id:
            return False
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE task_center SET status='completed', progress='完成', "
                    "result=?, articles_count=?, msg_count=?, finished_at=? "
                    "WHERE id=?",
                    (result[:50000], articles_count, msg_count, _now(), task_id),
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
                   limit: int = 50, exclude: str = "") -> list[dict]:
        """Return tasks filtered by status/type, newest first.

        Args:
            exclude: 逗号分隔的 task_type 前缀，SQL 层排除噪音任务
                （如 'cache_' 排除 cache_oa_*/cache_fav_* 等后台同步任务）。
                limit 只作用于排除后的真实任务 —— 修复"全部"列表被
                同步噪音挤占、真实任务显示不全的问题。
        """
        try:
            where = []
            params: list[object] = []
            if status:
                if status == "failed":
                    # "失败"筛选包含两类：任务失败(status=failed) 或 推送失败(push_status=failed)
                    where.append("(status = 'failed' OR push_status = 'failed')")
                else:
                    where.append("status = ?")
                    params.append(status)
            if task_type:
                types = [t.strip() for t in task_type.split(",") if t.strip()]
                if len(types) == 1:
                    where.append("task_type = ?")
                    params.append(types[0])
                else:
                    placeholders = ",".join("?" * len(types))
                    where.append(f"task_type IN ({placeholders})")
                    params.extend(types)
            if exclude:
                prefixes = [p.strip() for p in exclude.split(",") if p.strip()]
                if prefixes:
                    where.append("(" + " AND ".join("task_type NOT LIKE ?" for _ in prefixes) + ")")
                    params.extend(p + "%" for p in prefixes)
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

    def get_failed_push_tasks(self, hours: int = 24) -> list[dict]:
        """Return retryable push-failed tasks within the last N hours.

        可重推 = 推送全渠道失败：
          - push_status='failed'（内容已生成，但一个渠道都没发出去）
          - task_type='oa_article_alert' AND status='failed'
            （即时提醒在推送完成前被中断，例如 bot 重启，此时 push_status
              仍为空；正常推送结果已统一记在 push_status 轴上）

        push_status='partial' 刻意排除 —— 部分成功是终态：已有渠道收到
        消息，重推会让这些渠道收到重复内容。

        Returns:
            按时间从旧到新排序的任务列表（供批量重推逐条处理）。
        """
        try:
            cutoff = _now_offset(-max(1, int(hours or 24)) * 3600)
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, task_type, group_id, group_name, status, result, "
                    "error, push_status, push_error, outbox_id, created_at "
                    "FROM task_center "
                    "WHERE created_at >= ? AND "
                    "(push_status = 'failed' OR "
                    " (task_type = 'oa_article_alert' AND status = 'failed')) "
                    "ORDER BY created_at ASC",
                    (cutoff,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("[TASK-CENTER] get_failed_push_tasks failed: %s", e)
            return []

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

    def count_failed_since(self, since: str = "") -> int:
        """Count failed tasks created after a given timestamp.

        Used by the frontend badge to show "unread failed" count.
        If since is empty, counts all failed tasks.

        Args:
            since: ISO-8601 timestamp string (e.g. "2026-08-03T12:00:00.000Z").
                   Only tasks with created_at > since are counted.
                   内部统一转为本地时间字符串再与 created_at (本地时间) 比较,
                   避免 UTC ISO vs 本地时间 字符串字典序错位导致的过滤失效
                   (例: 用户在 UTC+8, DB created_at="2026-08-09T21:00:00",
                   frontend since="2026-08-09T13:00:00.000Z", 字符串比较会
                   误判 created_at > since 为 True, 即便真实时间任务更早)。

        Returns:
            Number of matching failed tasks.
        """
        try:
            with self._get_conn() as conn:
                if since:
                    # 前端传 UTC ISO (含 Z 或 +00:00), 转为本地时间字符串
                    # 与 created_at 同格式 (time.strftime %Y-%m-%dT%H:%M:%S)
                    local_since = _iso_to_local_str(since) or since
                    row = conn.execute(
                        "SELECT COUNT(*) FROM task_center "
                    "WHERE (status='failed' OR push_status='failed') "
                        "AND created_at > ?",
                        (local_since,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM task_center "
                        "WHERE status='failed' OR push_status='failed'",
                    ).fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.warning("[TASK-CENTER] count_failed_since failed: %s", e)
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


def _iso_to_local_str(iso: str) -> Optional[str]:
    """把 ISO-8601 (含 'Z' 或 '+HH:MM') 转为本地时间字符串, 格式与 _now() 一致。

    用于 count_failed_since: 前端 localStorage 存的是 Date.toISOString()
    (UTC 带毫秒和 Z 后缀), 而 DB created_at 是 _now() (本地时间无后缀),
    直接字符串字典序比较会因为时区错位误判 (UTC+8 用户下午创建的
    任务本地小时 > UTC 小时, 永远大于前端 since)。统一转本地再比较。

    Returns None if input 不可解析 (调用方 fallback 用原字符串)。
    """
    if not iso:
        return None
    try:
        from datetime import datetime
        # Python 3.11+ 支持 'Z' 直接 parse; 老版本需替换
        s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # 无时区信息, 视为本地时间, 直接截断到秒
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        return dt.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None
