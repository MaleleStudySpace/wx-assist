"""CronScheduler — 通用定时调度引擎。

任务统一通过 skill 引用执行。
数据存储: data/cron_jobs.json（不进 git）
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from src.utils.cron import cron_matches

CHECK_INTERVAL = 60       # tick 间隔（秒）
MIN_TRIGGER_GAP = 120     # 防止重复触发（秒）
SILENT = "[SILENT]"       # 输出此标记时跳过推送

STATE_PATH = Path("data/cron_jobs.json")


def _load_jobs() -> list[dict]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            return data.get("jobs", [])
    except Exception as e:
        logger.warning("[CRON] 加载 jobs 失败: %s", e)
    return []


def _save_jobs(jobs: list[dict]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATE_PATH)


class CronScheduler:
    """通用定时调度引擎。

    所有任务通过 skill 引用执行，不关心技能是脚本还是 AI。
    """

    def __init__(self, skill_engine=None, outbox=None, task_center=None):
        self._skill_engine = skill_engine
        self._outbox = outbox
        self._task_center = task_center
        self._jobs: list[dict] = []
        self._last_triggered: dict[str, float] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=3)
        self._lock = threading.Lock()

    def start(self) -> None:
        self._jobs = _load_jobs()
        enabled = [j for j in self._jobs if j.get("enabled")]
        if not enabled:
            logger.info("[CRON] 无可用的定时任务，跳过")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="cron-scheduler")
        self._thread.start()
        logger.info("[CRON] 已启动 (%d 个任务)", len(enabled))

    def stop(self) -> None:
        self._running = False
        self._pool.shutdown(wait=True, cancel_futures=False)
        self._pool = ThreadPoolExecutor(max_workers=3)
        logger.info("[CRON] 已停止")

    def reload(self) -> None:
        self._jobs = _load_jobs()
        logger.info("[CRON] 已重载 (%d 个任务)", len(self._jobs))

    # ── CRUD ────────────────────────────────────────────────────────

    def list_jobs(self) -> list[dict]:
        return list(self._jobs)

    def get_job(self, job_id: str) -> dict | None:
        for j in self._jobs:
            if j.get("id") == job_id:
                return dict(j)
        return None

    def add_job(self, job: dict) -> str:
        import uuid
        job.setdefault("id", uuid.uuid4().hex[:12])
        job.setdefault("enabled", True)
        job.setdefault("push", {"enabled": True, "target": "ilink"})
        job.setdefault("created_at", datetime.now().isoformat())
        job.setdefault("last_run", None)
        job.setdefault("status", "idle")
        job.setdefault("run_count", 0)
        job.setdefault("error_count", 0)
        with self._lock:
            self._jobs.append(job)
            _save_jobs(self._jobs)
        logger.info("[CRON] 新增: %s (%s) skill=%s cron=%s",
                    job.get("name"), job["id"],
                    job.get("skill"), job.get("cron"))
        return job["id"]

    def update_job(self, job_id: str, updates: dict) -> bool:
        with self._lock:
            for j in self._jobs:
                if j.get("id") == job_id:
                    j.update(updates)
                    _save_jobs(self._jobs)
                    return True
        return False

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if j.get("id") != job_id]
            if len(self._jobs) < before:
                _save_jobs(self._jobs)
                logger.info("[CRON] 删除: %s", job_id)
                return True
        return False

    def run_now(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if not job:
            return f"[CRON] 任务 {job_id} 不存在"
        logger.info("[CRON] run_now: %s (%s)", job.get("name"), job_id)
        return self._execute_job(job)

    # ── Ticker ──────────────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error("[CRON] tick 异常: %s", e)
            for _ in range(CHECK_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    def _tick(self) -> None:
        now = datetime.now()
        now_ts = time.time()
        for job in self._jobs:
            if not job.get("enabled"):
                continue
            expr = job.get("cron", "")
            if not expr or not cron_matches(expr, now):
                continue
            jid = job["id"]
            last = self._last_triggered.get(jid, 0)
            if now_ts - last < MIN_TRIGGER_GAP:
                continue
            self._last_triggered[jid] = now_ts
            logger.info("[CRON] 触发: %s (%s)", job.get("name"), jid)
            self._pool.submit(self._execute_and_push, job)

    # ── 执行 ────────────────────────────────────────────────────────

    def _execute_and_push(self, job: dict) -> None:
        job_id = job.get("id", "?")
        job_name = job.get("name", "?")
        skill_name = job.get("skill", "?")

        tid = None
        if self._task_center:
            try:
                tid = self._task_center.create_task(
                    "cron", "scheduler", job_id, job_name)
                if tid:
                    self._task_center.update_task(
                        tid, status="running", progress="执行中")
            except Exception as e:
                logger.warning("[CRON] task_center 创建失败: %s", e)

        text = ""
        t0 = time.monotonic()
        try:
            text = self._execute_job(job)
            elapsed = time.monotonic() - t0
            logger.info("[CRON] 完成: %s (%.2fs) len=%d",
                        job_name, elapsed, len(text))
            if tid:
                self._task_center.complete_task(
                    tid, result=text[:200] if text != SILENT else "")
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("[CRON] 失败: %s (%s) 耗时=%.2fs: %s",
                         job_name, job_id, elapsed, e)
            text = f"[CRON 错误] {job_name}: {e}"
            if tid:
                self._task_center.fail_task(tid, error=str(e)[:500])

        is_silent = text.strip() == SILENT
        is_error = text.startswith("[CRON 错误]")
        self.update_job(job_id, {
            "last_run": datetime.now().isoformat(),
            "status": "idle",
            "run_count": job.get("run_count", 0) + 1,
            "error_count": job.get("error_count", 0) + (1 if is_error else 0),
        })

        if is_silent:
            logger.info("[CRON] %s 无新内容，跳过推送", job_name)
            return
        push_cfg = job.get("push", {})
        if not push_cfg.get("enabled", True):
            return
        self._push(job, text)

    def _execute_job(self, job: dict) -> str:
        """通过 skill 引擎执行。不关心 skill 是脚本还是 AI。"""
        if not self._skill_engine:
            raise RuntimeError("skill_engine 未注入")
        skill_name = job.get("skill", "")
        if not skill_name:
            raise RuntimeError("job 未指定 skill")
        return self._skill_engine.execute(skill_name, job.get("args", {}))

    def _push(self, job: dict, text: str) -> None:
        if not self._outbox:
            logger.warning("[CRON] outbox 未注入，跳过推送")
            return
        try:
            job_name = job.get("name", "定时任务")
            nid = self._outbox.add(
                notif_type="cron",
                chat_id=job.get("chat_id", ""),
                group_name=job_name,
                title=f"⏰ {job_name}",
                content=text,
                priority="normal",
            )
            push_cfg = job.get("push", {})
            if push_cfg.get("target") == "ilink":
                from src.wechat.ilink_push import get_ilink_push, format_for_wechat
                ilink = get_ilink_push()
                if ilink and ilink.is_available():
                    msg = format_for_wechat(f"⏰ {job_name}", text)
                    result = ilink.send_message(msg)
                    ok = result.get("success", False)
                    err = result.get("error", "") if not ok else ""
                    self._outbox.update_push_result(
                        nid, "ilink", "success" if ok else "failed", err)
                    logger.info("[CRON] 推送 %s: %s",
                                "成功" if ok else "失败", job_name)
        except Exception as e:
            logger.warning("[CRON] 推送失败: %s", e)
