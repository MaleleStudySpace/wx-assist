"""Digest scheduler — triggers digest generation at configured times."""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .config import AssistantConfig, DigestGroup, OAGroup, save_assistant_config
from .digest import filter_messages, build_digest_prompt, generate_memory_update_prompt, DIGEST_SYSTEM_PROMPT, STYLE_PRESETS
from .outbox import Outbox
from ..utils.llm_logger import log_llm_interaction

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 60     # Check schedule every 60 seconds
MIN_TRIGGER_GAP_SEC = 120   # Prevent re-trigger within 2 minutes

# ── Startup catch-up ──────────────────────────────────────────────────
# When the scheduler starts (after bot restart), check if any cron was
# missed within this window.  Without this, a restart at 09:01 would
# miss the 09:00 trigger entirely and wait until the next cron.
_CATCHUP_MAX_AGE_HOURS = 3  # only catch up crons within this window

_STATE_PATH = "data/scheduler_state.json"


def _load_state() -> dict[str, float]:
    """Load persisted last_triggered timestamps."""
    try:
        import json as _j
        from pathlib import Path as _P
        data = _j.loads(_P(_STATE_PATH).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_state(state: dict[str, float]) -> None:
    """Persist last_triggered timestamps atomically."""
    try:
        import json as _j
        import os as _os
        from pathlib import Path as _P
        p = _P(_STATE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            _j.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        _os.replace(tmp, p)
    except Exception:
        pass


def _cron_matches(cron_expr: str, now: datetime) -> bool:
    """Check if a 5-field cron expression matches the current time.

    Supports multi-line cron (any line matching = true).
    Fields: minute hour day month day_of_week
    Supports: *, specific values, ranges (1-5), steps (*/15), lists (1,3,5)
    """
    for line in cron_expr.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if _single_cron_matches(line, now):
            return True
    return False


def _single_cron_matches(cron_expr: str, now: datetime) -> bool:
    """Check if a single 5-field cron expression matches the current time."""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False

    now_fields = [
        now.minute,         # 0-59
        now.hour,           # 0-23
        now.day,            # 1-31
        now.month,          # 1-12
        now.isoweekday() % 7,  # 0-6 (Sunday=0)
    ]

    for cron_field, now_val in zip(fields, now_fields):
        if not _field_matches(cron_field, now_val):
            return False
    return True


def _field_matches(field: str, value: int) -> bool:
    """Check if a single cron field matches a value.

    Supports: *, 5, 1-5, */15, 1,3,5
    """
    # List of sub-expressions (comma-separated)
    for part in field.split(','):
        part = part.strip()
        if part == '*':
            return True  # wildcard always matches
        if '/' in part:
            # Step expression: */15 or 0-30/5
            range_part, step_str = part.split('/', 1)
            step = int(step_str)
            if range_part == '*':
                start, end = 0, 59  # reasonable max for minute/hour
            elif '-' in range_part:
                start, end = map(int, range_part.split('-'))
            else:
                start = int(range_part)
                end = 59
            if value >= start and (value - start) % step == 0:
                return True
        elif '-' in part:
            # Range: 1-5
            start, end = map(int, part.split('-'))
            if start <= value <= end:
                return True
        else:
            # Single value
            if int(part) == value:
                return True
    return False


class DigestScheduler:
    """Background scheduler that triggers digest generation.

    Runs in a daemon thread. Checks every 60s if any digest_group's
    schedule includes the current HH:MM.

    Usage:
        scheduler = DigestScheduler(config, outbox, summarizer, store)
        scheduler.start()
        # ... bot runs ...
        scheduler.stop()
    """

    def __init__(self, config: AssistantConfig, outbox: Outbox,
                 summarizer, store, wcdb_client=None, task_center=None):
        self._config = config
        self._outbox = outbox
        self._summarizer = summarizer
        self._store = store
        self._wcdb_client = wcdb_client
        self._task_center = task_center
        self._running = False
        self._thread: threading.Thread | None = None
        # Track last trigger time per group to prevent double-fires
        # Persisted to scheduler_state.json so the startup catch-up
        # mechanism can detect crons missed during downtime.
        self._last_triggered: dict[str, float] = _load_state()
        self._tick_count = 0  # for periodic cleanup
        # Thread pool for async digest execution (scheduler triggers + manual triggers)
        self._pool = ThreadPoolExecutor(max_workers=3)

    # ── Public API ──────────────────────────────────────────────────

    def start(self) -> None:
        dg_count = sum(1 for dg in self._config.digest_groups if dg.enabled)
        oa_count = sum(1 for oa in self._config.oa_groups if oa.enabled and oa.cron_expr)
        if dg_count == 0 and oa_count == 0 and not self._config.assistant_enabled:
            logger.info("DigestScheduler: no enabled digest/OA groups and assistant disabled, not starting")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="digest-scheduler")
        self._thread.start()
        logger.info("DigestScheduler started (%d digest groups, %d OA groups, interval=%ds)",
                     dg_count, oa_count, CHECK_INTERVAL_SEC)
        # Catch up crons missed during downtime (bot restart / long pause).
        # Runs AFTER the thread starts so the first _tick doesn't race.
        self._catch_up_missed_crons()

    def _catch_up_missed_crons(self) -> None:
        """Check if any cron was missed in the last N hours and trigger once.

        Scans each enabled group with a cron expression.  If the cron
        would have matched at any point within [_CATCHUP_MAX_AGE_HOURS, now],
        submits one digest generation (per group, at most one catch-up).
        This prevents permanent loss of a scheduled digest after a bot restart.
        """
        import calendar
        now = datetime.now()
        now_ts = time.time()
        cutoff_ts = now_ts - _CATCHUP_MAX_AGE_HOURS * 3600
        any_caught_up = False

        # ── Group chat digests ──
        for dg in self._config.digest_groups:
            if not dg.enabled:
                continue
            if not dg.cron_expr:
                continue
            last_key = dg.chat_id or dg.group_name
            last_ts = self._last_triggered.get(last_key, 0)
            if last_ts >= cutoff_ts:
                continue  # already triggered recently, no catch-up needed
            # Check if cron matched at any minute in the catch-up window
            if self._cron_missed_in_window(dg.cron_expr, last_ts, now_ts):
                self._last_triggered[last_key] = now_ts
                logger.info("DigestScheduler: catch-up triggering digest for '%s' (missed cron %s)",
                            dg.group_name, dg.cron_expr)
                from concurrent.futures import wait
                self._pool.submit(self._run_digest_in_pool, dg, None)
                any_caught_up = True

        # ── OA digests ──
        for oa in self._config.oa_groups:
            if not oa.enabled:
                continue
            if not oa.cron_expr:
                continue
            last_key = f"oa:{oa.id}"
            last_ts = self._last_triggered.get(last_key, 0)
            if last_ts >= cutoff_ts:
                continue
            if self._cron_missed_in_window(oa.cron_expr, last_ts, now_ts):
                self._last_triggered[last_key] = now_ts
                logger.info("DigestScheduler: catch-up triggering OA digest for '%s' (missed cron %s)",
                            oa.name, oa.cron_expr)
                self._pool.submit(self._run_oa_digest_in_pool, oa, None)
                any_caught_up = True

        if any_caught_up:
            _save_state(self._last_triggered)

    @staticmethod
    def _cron_missed_in_window(cron_expr: str, last_ts: float, now_ts: float) -> bool:
        """Check if a cron expression matched at any point in [last_ts, now_ts].

        Uses a sampling approach: checks the cron at 1-minute granularity
        from max(last_ts + 60, now - CATCHUP_MAX_AGE_HOURS) to now.
        Returns True the first time a match is found.
        """
        import calendar
        start = max(last_ts + 60, now_ts - _CATCHUP_MAX_AGE_HOURS * 3600)
        # Round up to next full minute for the start
        start_min = int(start // 60) * 60 + 60
        now_min = int(now_ts // 60) * 60
        step = 60  # check every minute
        for ts in range(start_min, now_min + step, step):
            if ts > now_ts:
                break
            dt = datetime.fromtimestamp(ts)
            if _cron_matches(cron_expr, dt):
                return True
        return False

    def stop(self) -> None:
        self._running = False
        # Shutdown pool gracefully — wait for in-flight tasks to finish
        try:
            self._pool.shutdown(wait=True, cancel_futures=False)
        except Exception:
            pass
        # Recreate pool so start() can submit new tasks after a stop→start cycle
        self._pool = ThreadPoolExecutor(max_workers=3)
        logger.info("DigestScheduler stopped")

    def update_config(self, new_config: AssistantConfig) -> None:
        """Hot-reload the scheduler's config without restarting the thread.

        Handles:
        - Updated digest_groups (schedule, cron, enabled state)
        - assistant_enabled toggle (start/stop the thread)
        - New or removed digest groups
        """
        was_enabled = self._config.assistant_enabled
        self._config = new_config

        # If assistant was toggled off, stop the scheduler thread
        if was_enabled and not new_config.assistant_enabled:
            self.stop()
            logger.info("DigestScheduler: assistant disabled, stopping scheduler")
            return

        # If assistant was toggled on, start the scheduler thread
        if not was_enabled and new_config.assistant_enabled:
            self.start()
            logger.info("DigestScheduler: assistant enabled, starting scheduler")
            return

        # If running, just log the update (the _tick loop reads
        # self._config.digest_groups / oa_groups on each iteration,
        # so the new schedule/cron values are picked up automatically)
        if self._running:
            dg_count = sum(1 for dg in self._config.digest_groups if dg.enabled)
            oa_count = sum(1 for oa in self._config.oa_groups if oa.enabled and oa.cron_expr)
            logger.info("DigestScheduler: config updated (%d digest groups, %d OA groups)", dg_count, oa_count)

    # ── Internals ───────────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("DigestScheduler tick failed")
            # Periodic cleanup: every ~60 ticks (≈1 hour)
            self._tick_count += 1
            if self._tick_count % 60 == 0 and self._task_center:
                try:
                    self._task_center.cleanup_expired()
                except Exception:
                    logger.warning("[TASK-CENTER] periodic cleanup failed")
            # Sleep in small increments for responsive shutdown
            for _ in range(CHECK_INTERVAL_SEC):
                if not self._running:
                    break
                time.sleep(1)

    def _tick(self) -> None:
        now = datetime.now()
        now_hm = now.strftime("%H:%M")

        # If assistant is globally disabled, skip all digest work
        if not self._config.assistant_enabled:
            logger.debug("DigestScheduler: assistant disabled, skipping tick at %s", now_hm)
            return
        now_ts = time.time()

        # Log a periodic heartbeat so we can verify the scheduler thread is alive
        logger.debug("DigestScheduler: tick at %s (%d digest groups, %d OA groups)",
                     now_hm,
                     sum(1 for dg in self._config.digest_groups if dg.enabled),
                     sum(1 for oa in self._config.oa_groups if oa.enabled and oa.cron_expr))

        # ── Group chat digests ──
        for dg in self._config.digest_groups:
            if not dg.enabled:
                continue
            if not self._should_trigger(dg, now, now_hm):
                logger.debug("DigestScheduler: '%s' not triggered at %s (cron=%s schedule=%s)",
                             dg.group_name, now_hm, dg.cron_expr, dg.schedule)
                continue

            # Prevent double-fire within MIN_TRIGGER_GAP_SEC
            last_key = dg.chat_id or dg.group_name
            last = self._last_triggered.get(last_key, 0)
            if now_ts - last < MIN_TRIGGER_GAP_SEC:
                continue

            self._last_triggered[last_key] = now_ts
            logger.info("DigestScheduler: triggering digest for '%s' at %s", dg.group_name, now_hm)

            # Create TaskCenter task for tracking
            _tid = None
            try:
                if self._task_center:
                    _tid = self._task_center.create_task(
                        'group_digest', 'scheduler',
                        dg.chat_id or dg.group_name, dg.group_name)
                    self._broadcast_task_update(_tid, 'group_digest', 'pending', '准备中', dg.group_name)
            except Exception:
                logger.warning("[TASK] create failed for '%s'", dg.group_name)

            # Submit to thread pool for async execution
            _tid_ref = _tid  # capture for closure
            try:
                self._pool.submit(self._run_digest_in_pool, dg, _tid_ref)
            except RuntimeError as e:
                logger.error("DigestScheduler: pool submit failed for '%s': %s — "
                             "OA digest loop WILL still run", dg.group_name, e)

        # ── OA digests ──
        for oa in self._config.oa_groups:
            if not oa.enabled:
                continue
            if not oa.cron_expr:
                continue  # manual trigger only
            try:
                if not _cron_matches(oa.cron_expr, now):
                    continue
            except Exception:
                logger.warning("Invalid cron_expr '%s' for OA group '%s', skipping",
                               oa.cron_expr, oa.name)
                continue

            # Prevent double-fire within MIN_TRIGGER_GAP_SEC
            last_key = f"oa:{oa.id}"
            last = self._last_triggered.get(last_key, 0)
            if now_ts - last < MIN_TRIGGER_GAP_SEC:
                continue

            self._last_triggered[last_key] = now_ts
            logger.info("DigestScheduler: triggering OA digest for '%s' at %s", oa.name, now_hm)

            # Create TaskCenter task for tracking
            _tid = None
            try:
                if self._task_center:
                    _tid = self._task_center.create_task(
                        'oa_digest', 'scheduler', oa.id, oa.name)
                    self._broadcast_task_update(_tid, 'oa_digest', 'pending', '准备中', oa.name)
            except Exception:
                logger.warning("[TASK] create failed for '%s'", oa.name)

            # Submit to thread pool for async execution
            _tid_ref = _tid  # capture for closure
            try:
                self._pool.submit(self._run_oa_digest_in_pool, oa, _tid_ref)
            except RuntimeError as e:
                logger.error("DigestScheduler: pool submit failed for OA '%s': %s",
                             oa.name, e)

        # Persist last_triggered timestamps for startup catch-up
        _save_state(self._last_triggered)

    def _run_digest_in_pool(self, dg: DigestGroup, task_id: int = None) -> None:
        """Wrapper for running group digest in thread pool with error handling."""
        try:
            self._generate_digest(dg, task_id=task_id)
        except Exception:
            logger.exception("Digest generation failed for '%s'", dg.group_name)
            if task_id:
                try:
                    self._task_center.fail_task(task_id, error='unhandled exception')
                    self._broadcast_task_update(task_id, 'group_digest', 'failed', '', dg.group_name, error='unhandled exception')
                except Exception:
                    pass

    def _run_oa_digest_in_pool(self, oa: OAGroup, task_id: int = None) -> None:
        """Wrapper for running OA digest in thread pool with error handling."""
        try:
            self._generate_oa_digest(oa, task_id=task_id)
        except Exception:
            logger.exception("OA digest generation failed for '%s'", oa.name)
            if task_id:
                try:
                    self._task_center.fail_task(task_id, error='unhandled exception')
                    self._broadcast_task_update(task_id, 'oa_digest', 'failed', '', oa.name, error='unhandled exception')
                except Exception:
                    pass

    def _should_trigger(self, dg: DigestGroup, now: datetime, now_hm: str) -> bool:
        """Check if a digest group should trigger now.

        If cron_expr is set (high-precision mode), use cron matching.
        Otherwise fall back to simple HH:MM schedule matching.
        """
        if dg.cron_expr:
            try:
                return _cron_matches(dg.cron_expr, now)
            except Exception:
                logger.warning("Invalid cron_expr '%s' for '%s', falling back to schedule",
                               dg.cron_expr, dg.group_name)
        return now_hm in dg.schedule

    def _generate_digest(self, dg: DigestGroup, task_id: int = None) -> None:
        """Fetch messages, filter, summarize, update memory, push to outbox."""
        start_ts = time.monotonic()

        # Task progress: running
        self._tc_update(task_id, status='running', progress='正在获取消息')

        # 1. Fetch messages within lookback window
        since_ts = int(time.time()) - dg.lookback_hours * 3600
        chat_id = dg.chat_id or self._resolve_chat_id(dg.group_name)
        if not chat_id:
            logger.warning("[DIGEST] Step 1/7: cannot resolve chat_id for '%s'", dg.group_name)
            return

        raw_messages = self._store.get_messages_since(chat_id, since_ts, limit=500)
        logger.info("[DIGEST] Step 1/7: Fetched %d raw messages for '%s' (lookback=%dh)",
                     len(raw_messages), dg.group_name, dg.lookback_hours)
        if not raw_messages:
            logger.info("Digest: no messages for '%s' in last %dh", dg.group_name, dg.lookback_hours)
            # Task: completed with no content
            self._tc_complete(task_id, result='无新内容')
            # Still record in outbox so user sees the trigger happened
            mode_label = "未读" if dg.unread_only else f"{dg.lookback_hours}h"
            self._outbox.add(
                notif_type="group_digest",
                chat_id=chat_id,
                group_name=dg.group_name,
                title=f"📋 群聊摘要 · {dg.group_name} ({mode_label})",
                content=json.dumps({
                    "group": dg.group_name,
                    "lookback_hours": dg.lookback_hours,
                    "mode": mode_label,
                    "msg_count": 0,
                    "digest": "该时间窗口内无新消息，摘要跳过。",
                    "display": f"📋 **群聊:** {dg.group_name}\n📊 **消息数量:** 0 | ⏰ **时间范围:** 近 {dg.lookback_hours}h\n\n> 该时间窗口内无新消息，摘要跳过。",
                }, ensure_ascii=False),
                priority="normal",
            )
            return

        # 2. If unread_only, filter to unread portion
        if dg.unread_only:
            unread_count = self._get_unread_count(chat_id)
            if unread_count == 0:
                logger.info("[DIGEST] Step 2/7: unread_only mode, no unread messages for '%s', skipping", dg.group_name)
                self._tc_complete(task_id, result='无未读消息')
                return
            raw_messages = raw_messages[-unread_count:]
            logger.info("[DIGEST] Step 2/7: unread_only filter for '%s' — %d unread messages",
                         dg.group_name, unread_count)

        # 3. Filter
        ignore_kw = dg.profile.ignore if dg.profile else []
        filtered = filter_messages(raw_messages, ignore_kw)
        logger.info("[DIGEST] Step 3/7: Noise filter for '%s' — %d → %d messages (ignore_kw=%s)",
                     dg.group_name, len(raw_messages), len(filtered), ignore_kw)
        if not filtered:
            self._tc_complete(task_id, result='无实质内容')
            return

        # 4. Build prompt and summarize
        # Task progress: AI generating
        self._tc_update(task_id, progress='AI 生成摘要中')
        self._broadcast_task_update(task_id, 'group_digest', 'running', 'AI 生成摘要中', dg.group_name)
        # Unified architecture: system_prompt + user_prompt
        # - custom_prompt set → COMPLETELY REPLACES default system prompt
        # - style preset → appended to default system prompt
        # - build_digest_prompt() provides context only (profile + memory + messages)
        has_custom = dg.profile and dg.profile.custom_prompt
        prompt = build_digest_prompt(dg, filtered)

        # Determine system prompt
        if has_custom:
            system_prompt = dg.profile.custom_prompt
            logger.info("[DIGEST] Using custom system prompt for '%s' (len=%d)",
                        dg.group_name, len(system_prompt))
        else:
            system_prompt = DIGEST_SYSTEM_PROMPT
            # Append style preset if configured
            style = dg.profile.style if dg.profile else ""
            if style and style in STYLE_PRESETS:
                system_prompt += STYLE_PRESETS[style]
                logger.info("[DIGEST] Using default system prompt + style '%s' for '%s'",
                            style, dg.group_name)
            else:
                logger.info("[DIGEST] Using default system prompt for '%s'", dg.group_name)

        logger.info("[DIGEST] System prompt len=%d, User prompt len=%d for '%s'",
                    len(system_prompt), len(prompt), dg.group_name)

        try:
            llm_start = time.monotonic()
            digest_text = self._summarizer._call_digest_api(
                system_prompt,
                [{"role": "user", "content": prompt}],
            ) or "摘要生成失败"
            llm_latency = (time.monotonic() - llm_start) * 1000
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=system_prompt,
                user_prompt=prompt,
                response=digest_text,
                latency_ms=llm_latency,
                extra={
                    "group_id": chat_id,
                    "group_name": dg.group_name,
                    "chat_id": chat_id,
                    "msg_count": len(filtered),
                    "unread_only": dg.unread_only,
                    "lookback_hours": dg.lookback_hours,
                    "has_custom_prompt": bool(has_custom),
                },
            )
            logger.info("[DIGEST] Step 4/7: LLM call success for '%s' — result len=%d, preview=%s",
                         dg.group_name, len(digest_text), digest_text[:100].replace('\n', ' '))
        except Exception as e:
            llm_latency = (time.monotonic() - llm_start) * 1000 if "llm_start" in locals() else 0
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=system_prompt,
                user_prompt=prompt,
                response=f"[Error: {e}]",
                latency_ms=llm_latency,
                extra={
                    "group_id": chat_id,
                    "group_name": dg.group_name,
                    "chat_id": chat_id,
                    "msg_count": len(filtered),
                    "error": str(e),
                },
            )
            logger.error("[DIGEST] Step 4/7: LLM call failed for '%s': %s", dg.group_name, e)
            digest_text = f"摘要生成失败: {e}"

        # 5. Update memory
        mem_system_prompt = "你是一个群聊记忆助手，负责记录群聊摘要要点。用中文，≤500字。"
        try:
            mem_prompt = generate_memory_update_prompt(dg.memory, digest_text)
            mem_start = time.monotonic()
            new_memory = self._summarizer._call_chat_api(
                mem_system_prompt,
                [{"role": "user", "content": mem_prompt}],
            )
            mem_latency = (time.monotonic() - mem_start) * 1000
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest_memory",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=mem_system_prompt,
                user_prompt=mem_prompt,
                response=new_memory or "",
                latency_ms=mem_latency,
                extra={
                    "group_id": dg.chat_id or dg.group_name,
                    "group_name": dg.group_name,
                    "existing_memory_len": len(dg.memory or ""),
                },
            )
            dg.memory = new_memory[:500] if new_memory else dg.memory
            save_assistant_config(self._config)
            logger.info("[DIGEST] Step 5/7: Memory updated for '%s' (%d → %d chars)",
                         dg.group_name, len(dg.memory or ""), len(new_memory or ""))
        except Exception as e:
            mem_latency = (time.monotonic() - mem_start) * 1000 if "mem_start" in locals() else 0
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest_memory",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=mem_system_prompt,
                user_prompt=generate_memory_update_prompt(dg.memory, digest_text) if dg.memory else "",
                response=f"[Error: {e}]",
                latency_ms=mem_latency,
                extra={
                    "group_id": dg.chat_id or dg.group_name,
                    "group_name": dg.group_name,
                    "error": str(e),
                },
            )
            logger.warning("[DIGEST] Step 5/7: Memory update failed for '%s': %s", dg.group_name, e)

        # 6. Push to outbox
        mode_label = "未读" if dg.unread_only else f"{dg.lookback_hours}h"
        title = f"📋 群聊摘要 · {dg.group_name} ({mode_label})"
        content = json.dumps({
            "group": dg.group_name,
            "lookback_hours": dg.lookback_hours,
            "mode": mode_label,
            "msg_count": len(filtered),
            "digest": digest_text,
            "display": f"📋 **群聊:** {dg.group_name}\n📊 **消息:** {len(filtered)} 条 | ⏰ **时间:** 近 {dg.lookback_hours}h\n\n{digest_text}",
        }, ensure_ascii=False)
        nid = self._outbox.add(
            notif_type="group_digest",
            chat_id=chat_id,
            group_name=dg.group_name,
            title=title,
            content=content,
            priority="normal",
        )
        logger.info("[DIGEST] Step 6/7: Outbox entry #%d created for '%s'", nid, dg.group_name)

        # Task progress: pushing
        self._tc_update(task_id, progress='推送中')

        # 7. Push to WeChat via iLink (if configured)
        if dg.push_target == "ilink":
            try:
                from src.wechat.ilink_push import get_ilink_push, format_for_wechat
                import json as _json
                ilink = get_ilink_push()
                if ilink.is_available():
                    push_data = _json.loads(content) if isinstance(content, str) else content
                    push_text = push_data.get("display", content)
                    msg = format_for_wechat(title, push_text)
                    result = ilink.send_message(msg)
                    # Update push audit in outbox
                    push_ok = result.get("success", False)
                    push_err = result.get("error", "") if not push_ok else ""
                    self._outbox.update_push_result(
                        nid, "ilink",
                        "success" if push_ok else "failed",
                        push_err,
                    )
                    # Task: update push result
                    self._tc_push_result(task_id, "success" if push_ok else "failed", push_err)
                    if push_ok:
                        logger.info("Digest pushed to WeChat for '%s'", dg.group_name)
                    else:
                        logger.warning("WeChat push failed for '%s': %s", dg.group_name, push_err)
                    # Broadcast push result to WebSocket clients
                    try:
                        from src.web.api_handlers import broadcast_event
                        broadcast_event("digest_push_result", {
                            "group_name": dg.group_name,
                            "success": push_ok,
                            "error": push_err,
                            "session_expired": "session_expired" in push_err,
                        })
                    except Exception:
                        pass  # broadcast failure should not break digest
                else:
                    logger.warning("WeChat push skipped for '%s': iLink not bound", dg.group_name)
            except Exception as e:
                logger.warning("WeChat push error for '%s': %s", dg.group_name, e)
                try:
                    self._outbox.update_push_result(nid, "ilink", "failed", str(e))
                except Exception:
                    pass

        elapsed = (time.monotonic() - start_ts) * 1000
        # Task: completed or failed (if LLM error)
        if digest_text.startswith("摘要生成失败"):
            self._tc_fail(task_id, error=digest_text)
            self._broadcast_task_update(task_id, 'group_digest', 'failed', '', dg.group_name, error=digest_text[:100])
        else:
            self._tc_complete(task_id, result=digest_text[:200] if filtered else '',
                              msg_count=len(filtered) if filtered else 0)
            self._broadcast_task_update(task_id, 'group_digest', 'completed', '完成', dg.group_name)
        logger.info("[DIGEST] Pipeline completed for '%s' in %.0fms", dg.group_name, elapsed)

    def _get_unread_count(self, chat_id: str) -> int:
        """Get unread count for a chat from WCDB sessions."""
        try:
            from src.web.api_handlers import get_wcdb_client
            client = get_wcdb_client()
            if not client:
                return 0
            sessions = client.get_sessions(limit=1000)
            for s in sessions:
                if s.get("username") == chat_id:
                    return int(s.get("unread_count", 0) or 0)
        except Exception as e:
            logger.warning("Failed to get unread count for %s: %s", chat_id, e)
        return 0

    def _resolve_chat_id(self, group_name: str) -> str | None:
        """Resolve group display name to chat_id via the store.

        Returns None if resolution fails (instead of falling back to
        group_name, which would cause invalid queries).
        """
        try:
            # Try message store's group mapping
            if hasattr(self._store, "get_chat_id_by_name"):
                result = self._store.get_chat_id_by_name(group_name)
                if result:
                    return result
        except Exception:
            pass
        return None

    def _generate_oa_digest(self, oa: OAGroup, task_id: int = None) -> None:
        """Generate OA digest for a scheduled OA group.

        Uses OADigestService to generate the digest, then pushes to
        outbox and optionally to WeChat via iLink.
        """
        from .oa_digest import OADigestService

        # Task progress: running
        self._tc_update(task_id, status='running', progress='正在获取文章')
        self._broadcast_task_update(task_id, 'oa_digest', 'running', '正在获取文章', oa.name)

        # Get WCDB client — either from constructor or from api_handlers
        client = self._wcdb_client
        if not client:
            try:
                from src.web.api_handlers import get_wcdb_client
                client = get_wcdb_client()
            except Exception:
                pass
        if not client:
            logger.warning("[OA-DIGEST] No WCDB client available for '%s', skipping", oa.name)
            self._tc_fail(task_id, error='WCDB 不可用')
            return

        # Task progress: AI generating
        self._tc_update(task_id, progress='AI 生成摘要中')
        self._broadcast_task_update(task_id, 'oa_digest', 'running', 'AI 生成摘要中', oa.name)

        service = OADigestService(self._config, client, summarizer=self._summarizer)
        result = service.generate_digest(oa.id)

        if not result.get("success", False):
            logger.error("[OA-DIGEST] Digest generation failed for '%s': %s",
                         oa.name, result.get("error", "unknown"))
            self._tc_fail(task_id, error=result.get("error", "摘要生成失败"))
            self._broadcast_task_update(task_id, 'oa_digest', 'failed', '', oa.name, error=result.get("error", ""))
            return

        digest_text = result.get("digest_text", "")
        articles_count = result.get("articles_count", 0)

        logger.info("[OA-DIGEST] Scheduled digest generated for '%s': %d articles, %d chars",
                     oa.name, articles_count, len(digest_text))

        if not digest_text or digest_text.startswith("没有") or digest_text.startswith("所有"):
            # No new content — still record in outbox
            # Task: completed with no content
            self._tc_complete(task_id, result='无新内容', articles_count=articles_count)
            self._broadcast_task_update(task_id, 'oa_digest', 'completed', '无新内容', oa.name)
            self._outbox.add(
                notif_type="oa_digest",
                chat_id=oa.id,
                group_name=oa.name,
                title=f"📰 公众号摘要 · {oa.name}",
                content=json.dumps({
                    "group": oa.name,
                    "articles_count": articles_count,
                    "digest": digest_text,
                    "display": f"📰 **公众号:** {oa.name}\n📄 **文章:** {articles_count} 篇\n\n{digest_text}",
                }, ensure_ascii=False),
                priority="normal",
            )
            return

        # Push to outbox
        title = f"📰 公众号摘要 · {oa.name}"
        # Task progress: pushing
        self._tc_update(task_id, progress='推送中')
        self._broadcast_task_update(task_id, 'oa_digest', 'running', '推送中', oa.name)
        content = json.dumps({
            "group": oa.name,
            "articles_count": articles_count,
            "digest": digest_text,
            "display": f"📰 **公众号:** {oa.name}\n📄 **文章:** {articles_count} 篇\n\n{digest_text}",
        }, ensure_ascii=False)
        nid = self._outbox.add(
            notif_type="oa_digest",
            chat_id=oa.id,
            group_name=oa.name,
            title=title,
            content=content,
            priority="normal",
        )

        # Push to WeChat via iLink (if configured)
        if oa.push_target == "ilink":
            try:
                from src.wechat.ilink_push import get_ilink_push, format_for_wechat
                import json as _json
                ilink = get_ilink_push()
                if ilink.is_available():
                    push_data = _json.loads(content) if isinstance(content, str) else content
                    push_text = push_data.get("display", content)
                    msg = format_for_wechat(title, push_text)
                    push_result = ilink.send_message(msg)
                    push_ok = push_result.get("success", False)
                    push_err = push_result.get("error", "") if not push_ok else ""
                    self._outbox.update_push_result(
                        nid, "ilink",
                        "success" if push_ok else "failed",
                        push_err,
                    )
                    # Task: update push result
                    self._tc_push_result(task_id, "success" if push_ok else "failed", push_err)
                    if push_ok:
                        logger.info("[OA-DIGEST] Pushed to WeChat for '%s'", oa.name)
                    else:
                        logger.warning("[OA-DIGEST] WeChat push failed for '%s': %s", oa.name, push_err)
                    # Broadcast push result
                    try:
                        from src.web.api_handlers import broadcast_event
                        broadcast_event("oa_digest_push_result", {
                            "group_name": oa.name,
                            "success": push_ok,
                            "error": push_err,
                            "session_expired": "session_expired" in push_err,
                        })
                    except Exception:
                        pass
                else:
                    logger.warning("[OA-DIGEST] WeChat push skipped for '%s': iLink not bound", oa.name)
            except Exception as e:
                logger.warning("[OA-DIGEST] WeChat push error for '%s': %s", oa.name, e)
                try:
                    self._outbox.update_push_result(nid, "ilink", "failed", str(e))
                except Exception:
                    pass

        # Broadcast completion via WebSocket
        try:
            from src.web.api_handlers import broadcast_event
            broadcast_event("oa_digest_progress", {
                "status": "completed",
                "group_id": oa.id,
                "articles_count": articles_count,
                "digest_text": digest_text,
            })
        except Exception:
            pass

        # Task: completed
        self._tc_complete(task_id, result=digest_text[:200], articles_count=articles_count)
        self._broadcast_task_update(task_id, 'oa_digest', 'completed', '完成', oa.name)

    # ── TaskCenter helpers ────────────────────────────────────────────

    def _tc_update(self, task_id, **kwargs):
        """Safe wrapper: update task, never raises."""
        if not task_id or not self._task_center:
            return
        try:
            self._task_center.update_task(task_id, **kwargs)
        except Exception:
            logger.warning("[TASK] update_task #%d failed", task_id, exc_info=True)

    def _tc_complete(self, task_id, result='', **kwargs):
        """Safe wrapper: complete task, never raises."""
        if not task_id or not self._task_center:
            return
        try:
            self._task_center.complete_task(task_id, result=result, **kwargs)
        except Exception:
            logger.warning("[TASK] complete_task #%d failed", task_id, exc_info=True)

    def _tc_fail(self, task_id, error=''):
        """Safe wrapper: fail task, never raises."""
        if not task_id or not self._task_center:
            return
        try:
            self._task_center.fail_task(task_id, error=error)
        except Exception:
            logger.warning("[TASK] fail_task #%d failed", task_id, exc_info=True)

    def _tc_push_result(self, task_id, push_status, push_error=''):
        """Safe wrapper: update push result, never raises."""
        if not task_id or not self._task_center:
            return
        try:
            self._task_center.update_push_result(task_id, push_status, push_error)
        except Exception:
            logger.warning("[TASK] update_push_result #%d failed", task_id, exc_info=True)

    def _broadcast_task_update(self, task_id, task_type, status, progress, group_name, error=''):
        """Broadcast task_update WebSocket event. Never raises."""
        if not task_id:
            return
        try:
            from src.web.api_handlers import broadcast_event
            payload = {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "progress": progress,
                "group_name": group_name,
            }
            if error:
                payload["error"] = error
            broadcast_event("task_update", payload)
        except Exception:
            pass
