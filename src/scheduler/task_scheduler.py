"""Task scheduler — APScheduler-based cron engine for scheduled bot tasks.

Provides a unified scheduler for recurring tasks such as:
  - oa_digest: 公众号摘要 (Official Account digest)
  - fav_export: 收藏导出 (Favorites export)

Task definitions are persisted to data/assistant_config.json under the
``scheduler_tasks`` key so they survive bot restarts.

Usage:
    from src.scheduler.task_scheduler import TaskScheduler

    scheduler = TaskScheduler()
    scheduler.load_tasks()
    scheduler.start()
    ...
    scheduler.stop()
"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/assistant_config.json")

# ── Task type constants ────────────────────────────────────────────────

TASK_TYPE_OA_DIGEST = "oa_digest"    # 公众号摘要
TASK_TYPE_FAV_EXPORT = "fav_export"  # 收藏导出

VALID_TASK_TYPES = {TASK_TYPE_OA_DIGEST, TASK_TYPE_FAV_EXPORT}


# ── Data model ─────────────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """A single scheduled task definition.

    Attributes:
        id:         Unique task identifier (auto-generated uuid4 if empty).
        name:       Human-readable task name.
        task_type:  One of VALID_TASK_TYPES (e.g. "oa_digest").
        cron_expr:  Cron expression string, 5 fields (min hour day month dow).
        function_ref: Dotted import path of the callable to execute,
                      e.g. "src.assistant.digest.generate_oa_digest".
        enabled:    Whether the task is active.
        last_run_time: ISO-8601 timestamp of the last successful run (or "").
        status:     Current runtime status — "idle" | "running" | "error".
    """

    id: str = ""
    name: str = ""
    task_type: str = ""
    cron_expr: str = ""
    function_ref: str = ""
    enabled: bool = True
    last_run_time: str = ""
    status: str = "idle"

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]


# ── Internal: function resolver ────────────────────────────────────────

def _resolve_callable(dotted_path: str) -> Callable:
    """Import and return the callable at *dotted_path*.

    ``"src.assistant.digest.generate_oa_digest"`` resolves to the
    ``generate_oa_digest`` function inside ``src.assistant.digest``.
    """
    module_path, _, attr_name = dotted_path.rpartition(".")
    if not module_path or not attr_name:
        raise ValueError(f"Invalid function_ref: {dotted_path!r}")
    import importlib
    module = importlib.import_module(module_path)
    func = getattr(module, attr_name)
    if not callable(func):
        raise TypeError(f"{dotted_path!r} is not callable")
    return func


# ── Internal: config persistence ───────────────────────────────────────

def _load_config_dict() -> dict:
    """Read assistant_config.json and return the raw dict (or empty dict)."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", CONFIG_PATH, exc)
        return {}


def _save_config_dict(data: dict) -> None:
    """Atomically write *data* back to assistant_config.json."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
    logger.debug("Config persisted to %s", CONFIG_PATH)


def _task_to_dict(task: ScheduledTask) -> dict:
    """Serialize a ScheduledTask to a JSON-safe dict."""
    return {
        "id": task.id,
        "name": task.name,
        "task_type": task.task_type,
        "cron_expr": task.cron_expr,
        "function_ref": task.function_ref,
        "enabled": task.enabled,
        "last_run_time": task.last_run_time,
        "status": task.status,
    }


def _dict_to_task(data: dict) -> ScheduledTask:
    """Deserialize a dict to a ScheduledTask."""
    return ScheduledTask(
        id=data.get("id", ""),
        name=data.get("name", ""),
        task_type=data.get("task_type", ""),
        cron_expr=data.get("cron_expr", ""),
        function_ref=data.get("function_ref", ""),
        enabled=data.get("enabled", True),
        last_run_time=data.get("last_run_time", ""),
        status=data.get("status", "idle"),
    )


# ── Main class ─────────────────────────────────────────────────────────

class TaskScheduler:
    """APScheduler-backed task manager for wx-assist scheduled tasks.

    Tasks are loaded from / persisted to ``data/assistant_config.json``
    under the ``scheduler_tasks`` key.

    Usage:
        scheduler = TaskScheduler()
        scheduler.load_tasks()
        scheduler.start()

        # Add a task at runtime
        task = ScheduledTask(
            name="公众号早报",
            task_type="oa_digest",
            cron_expr="0 8 * * *",
            function_ref="src.assistant.digest.generate_oa_digest",
        )
        scheduler.add_task(task)

        # Later …
        scheduler.stop()
    """

    def __init__(self, store=None, wcdb_client=None):
        self._scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        self._tasks: dict[str, ScheduledTask] = {}  # id → task
        self._store = store
        self._wcdb_client = wcdb_client

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background scheduler.

        Only enabled tasks are scheduled; disabled tasks are kept in
        memory but not added to APScheduler.
        """
        if self._scheduler.running:
            logger.warning("TaskScheduler: already running, ignoring start()")
            return

        for task in self._tasks.values():
            if task.enabled:
                self._schedule_job(task)

        self._scheduler.start()
        logger.info(
            "TaskScheduler started (%d tasks, %d enabled)",
            len(self._tasks),
            sum(1 for t in self._tasks.values() if t.enabled),
        )

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)
        logger.info("TaskScheduler stopped")

    # ── Persistence ────────────────────────────────────────────────

    def load_tasks(self) -> None:
        """Load task definitions from assistant_config.json.

        Should be called before ``start()``.  Any in-memory tasks are
        replaced with the loaded set.
        """
        data = _load_config_dict()
        raw_list = data.get("scheduler_tasks", [])
        self._tasks.clear()
        for item in raw_list:
            try:
                task = _dict_to_task(item)
                self._tasks[task.id] = task
            except Exception as exc:
                logger.warning("Skipping malformed scheduler task: %s", exc)
        logger.info("TaskScheduler: loaded %d tasks from config", len(self._tasks))

    def save_tasks(self) -> None:
        """Persist current task definitions to assistant_config.json.

        Only the ``scheduler_tasks`` key is rewritten; other keys are
        preserved.
        """
        data = _load_config_dict()
        data["scheduler_tasks"] = [_task_to_dict(t) for t in self._tasks.values()]
        _save_config_dict(data)
        logger.info("TaskScheduler: persisted %d tasks", len(self._tasks))

    # ── Task CRUD ──────────────────────────────────────────────────

    def add_task(self, task: ScheduledTask) -> str:
        """Register a new task, persist it, and (if enabled) schedule it.

        Returns the task id.
        """
        if task.task_type not in VALID_TASK_TYPES:
            raise ValueError(
                f"Invalid task_type {task.task_type!r}. "
                f"Expected one of {VALID_TASK_TYPES}"
            )
        if not task.cron_expr:
            raise ValueError("cron_expr is required")
        if not task.function_ref:
            raise ValueError("function_ref is required")

        # Ensure unique id
        if not task.id or task.id in self._tasks:
            task.id = uuid.uuid4().hex[:8]

        self._tasks[task.id] = task

        if task.enabled and self._scheduler.running:
            self._schedule_job(task)

        self.save_tasks()
        logger.info(
            "TaskScheduler: added task %s (%s) cron=%s",
            task.id, task.name, task.cron_expr,
        )
        return task.id

    def remove_task(self, task_id: str) -> bool:
        """Remove a task by id.  Returns True if found and removed."""
        task = self._tasks.pop(task_id, None)
        if task is None:
            logger.warning("TaskScheduler: task %s not found", task_id)
            return False

        self._unschedule_job(task_id)

        self.save_tasks()
        logger.info("TaskScheduler: removed task %s (%s)", task_id, task.name)
        return True

    def update_task(self, task_id: str, **kwargs) -> bool:
        """Update fields of an existing task.

        Accepts any field of ScheduledTask as a keyword argument.
        If cron_expr or enabled changes, the APScheduler job is
        rescheduled accordingly.

        Returns True if the task was found and updated.
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.warning("TaskScheduler: task %s not found for update", task_id)
            return False

        # Validate task_type if provided
        new_type = kwargs.get("task_type", task.task_type)
        if new_type not in VALID_TASK_TYPES:
            raise ValueError(f"Invalid task_type {new_type!r}")

        needs_reschedule = False
        for key, value in kwargs.items():
            if not hasattr(task, key):
                logger.warning("TaskScheduler: unknown field %s, skipping", key)
                continue
            setattr(task, key, value)
            if key in ("cron_expr", "enabled", "function_ref"):
                needs_reschedule = True

        if needs_reschedule:
            self._unschedule_job(task_id)
            if task.enabled and self._scheduler.running:
                self._schedule_job(task)

        self.save_tasks()
        logger.info("TaskScheduler: updated task %s (%s)", task_id, task.name)
        return True

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Return a task by id, or None."""
        return self._tasks.get(task_id)

    def list_tasks(self, task_type: str = "") -> list[ScheduledTask]:
        """Return all tasks, optionally filtered by task_type."""
        tasks = list(self._tasks.values())
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        return tasks

    # ── Job execution wrapper ──────────────────────────────────────

    def _run_task(self, task_id: str) -> None:
        """APScheduler job callback — resolve, call, and update status."""
        task = self._tasks.get(task_id)
        if task is None:
            logger.error("TaskScheduler: task %s not found at run time", task_id)
            return

        task.status = "running"
        logger.info("TaskScheduler: executing %s (%s)", task.id, task.name)

        try:
            func = _resolve_callable(task.function_ref)
            func()
            task.status = "idle"
            task.last_run_time = datetime.now().isoformat(timespec="seconds")
            logger.info(
                "TaskScheduler: %s (%s) completed successfully",
                task.id, task.name,
            )
        except Exception as exc:
            task.status = "error"
            logger.exception(
                "TaskScheduler: %s (%s) failed: %s", task.id, task.name, exc,
            )
        finally:
            # Persist updated status / last_run_time
            self.save_tasks()

    # ── APScheduler plumbing ───────────────────────────────────────

    def _schedule_job(self, task: ScheduledTask) -> None:
        """Add an APScheduler job for the given task."""
        try:
            cron_fields = task.cron_expr.strip().split()
            if len(cron_fields) != 5:
                raise ValueError(
                    f"cron_expr must have 5 fields, got {len(cron_fields)}: "
                    f"{task.cron_expr!r}"
                )
            trigger = CronTrigger(
                minute=cron_fields[0],
                hour=cron_fields[1],
                day=cron_fields[2],
                month=cron_fields[3],
                day_of_week=cron_fields[4],
                timezone="Asia/Shanghai",
            )
            self._scheduler.add_job(
                func=self._run_task,
                trigger=trigger,
                id=task.id,
                name=task.name,
                kwargs={"task_id": task.id},
                replace_existing=True,
            )
            logger.debug("TaskScheduler: scheduled job %s (%s)", task.id, task.cron_expr)
        except Exception as exc:
            logger.error(
                "TaskScheduler: failed to schedule %s (%s): %s",
                task.id, task.name, exc,
            )
            task.status = "error"

    def _unschedule_job(self, task_id: str) -> None:
        """Remove the APScheduler job for the given task id."""
        try:
            self._scheduler.remove_job(task_id)
            logger.debug("TaskScheduler: unscheduled job %s", task_id)
        except Exception:
            # Job may not exist (e.g. task was disabled) — that's fine
            pass


# ── Module-level singleton ────────────────────────────────────────────

_instance: Optional["TaskScheduler"] = None


def get_task_scheduler(store=None, wcdb_client=None) -> "TaskScheduler":
    """Get or create the global TaskScheduler singleton.

    On first call, creates the instance with optional store/wcdb_client
    references and loads persisted tasks. Subsequent calls return the
    same instance, ignoring new store/wcdb_client args.
    """
    global _instance
    if _instance is None:
        _instance = TaskScheduler(store=store, wcdb_client=wcdb_client)
        _instance.load_tasks()
    return _instance
