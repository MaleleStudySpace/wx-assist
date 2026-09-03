"""Digest scheduler — triggers digest generation at configured times."""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.utils.cron import cron_matches

from .config import (
    AssistantConfig,
    DigestGroup,
    OAGroup,
    group_memory_budget,
    load_assistant_config,
    truncate_memory,
    update_digest_group_memory,
)
from .digest import (
    DIGEST_SYSTEM_PROMPT,
    PACKED_OUTPUT_CONTRACT,
    PER_CHAT_NO_HEADING_HINT,
    SINGLE_HEADING_CONTRACT,
    STYLE_PRESETS,
    ChatUnit,
    build_digest_prompt,
    build_packed_prompt,
    concat_sections,
    digest_token_budget,
    estimate_messages_tokens,
    filter_messages,
    generate_memory_update_prompt,
    plan_digest,
)
from src.summarize.errors import LLMContextOverflowError
from .outbox import Outbox
from ..utils.llm_logger import log_llm_interaction

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 60     # Check schedule every 60 seconds
MIN_TRIGGER_GAP_SEC = 120   # Prevent re-trigger within 2 minutes

# Digest latency is driven by OUTPUT length, not input size: measured ~18s
# fixed overhead plus ~14ms per output char, so a packed multi-chat digest
# writing 1500 chars already needs ~40s against the client-level 60s httpx
# timeout.  Widen it for this path only, via a per-request timeout — chat and
# memory-update calls keep the 60s default.
DIGEST_LLM_TIMEOUT_SEC = float(os.getenv("DIGEST_LLM_TIMEOUT_SEC", "180"))

# 降级路径的组内并发。照抄 oa_digest 的 4 但保守取 3：外层 scheduler 线程池
# 已经是 3，最坏 3x3=9 个并发 LLM 调用（OA 侧已有 3x4=12 的先例）。
DIGEST_PER_CHAT_WORKERS = 3

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
    except Exception as e:
        # 防重触状态丢失 → 重启后 3h 窗口内 cron 可能重复触发。
        logger.warning("scheduler state save failed (may re-trigger after restart): %s", e)


def _migrate_state_keys(state: dict, cfg: AssistantConfig) -> tuple[dict, bool]:
    """把防重触状态的 key 从旧的 chat_id / group_name 迁移到 ``dg:{id}``。

    纯函数、幂等。不迁移的后果很直接：升级后旧 key 全部失配，
    `_catch_up_missed_crons` 会认为每个组"从没触发过"，在 3 小时窗口内把
    匹配过的 cron 全部补触发一轮（线上 3 个组 = 升级后 3 条重复推送）。

    Returns:
        (迁移后的 state, 是否发生了改动)
    """
    changed = False
    consumed: set = set()
    for dg in cfg.digest_groups:
        new_key = f"dg:{dg.id}"
        if new_key in state:
            continue                      # 已迁移过 → 幂等短路
        legacy_keys = [c.chat_id for c in dg.chats if c.chat_id]
        if dg.name:
            legacy_keys.append(dg.name)
        stamps = [state[k] for k in legacy_keys if k in state]
        if stamps:
            # 取最近一次：多个旧 key 命中同一组时，拿更早的时间戳会误判"错过了"
            state[new_key] = max(stamps)
            consumed.update(k for k in legacy_keys if k in state)
            changed = True
    # 清理：只保留 dg:* / oa:* 两类 key（旧 chatroom key、已删组的 key 全部丢弃）
    for k in list(state):
        if k in consumed or not (k.startswith("dg:") or k.startswith("oa:")):
            del state[k]
            changed = True
    return state, changed


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
        # 迁移必须在 start() → _catch_up_missed_crons() 之前完成，否则旧 key
        # 全部失配会让每个组都被判定为"从没触发过"而补触发一轮。
        self._last_triggered, _migrated = _migrate_state_keys(self._last_triggered, config)
        if _migrated:
            # 立即落盘，不等 _tick 末尾：进程若在 catch-up 之后崩溃，
            # 下次启动读到的仍是旧 key，会再补触发一轮。
            _save_state(self._last_triggered)
            logger.info("scheduler_state.json 已迁移到 dg:/oa: key 体系")
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
            last_key = f"dg:{dg.id}"
            last_ts = self._last_triggered.get(last_key, 0)
            if last_ts >= cutoff_ts:
                continue  # already triggered recently, no catch-up needed
            # Check if cron matched at any minute in the catch-up window
            if self._cron_missed_in_window(dg.cron_expr, last_ts, now_ts):
                self._last_triggered[last_key] = now_ts
                logger.info("DigestScheduler: catch-up triggering digest for '%s' (missed cron %s)",
                            dg.name, dg.cron_expr)
                # Create TaskCenter task (same as normal _tick flow)
                _tid = None
                try:
                    if self._task_center:
                        _tid = self._task_center.create_task(
                            'group_digest', 'catchup',
                            dg.id, dg.name)
                        self._broadcast_task_update(_tid, 'group_digest', 'pending', '准备中', dg.name)
                except Exception:
                    logger.warning("[TASK] create failed for '%s'", dg.name)
                self._pool.submit(self._run_digest_in_pool, dg.id, _tid)
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
                # Create TaskCenter task (same as normal _tick flow)
                _tid = None
                try:
                    if self._task_center:
                        _tid = self._task_center.create_task(
                            'oa_digest', 'catchup', oa.id, oa.name)
                        self._broadcast_task_update(_tid, 'oa_digest', 'pending', '准备中', oa.name)
                except Exception:
                    logger.warning("[TASK] create failed for '%s'", oa.name)
                self._pool.submit(self._run_oa_digest_in_pool, oa, _tid)
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
            if cron_matches(cron_expr, dt):
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

        # 热更新时"state 里没有的组"= 用户刚在网页端新建的 → 打上 now，
        # 否则接下来的 _tick / start() 会把它当成"错过了一次"而立刻补触发。
        # 刻意只在 update_config 里做、不在 __init__ 里做：__init__ 里打 now
        # 会让 _catch_up_missed_crons 永久失效（它的意义正是补上重启期间错过的 cron）。
        self._last_triggered, _mig = _migrate_state_keys(self._last_triggered, new_config)
        _now_ts = time.time()
        for dg in new_config.digest_groups:
            self._last_triggered.setdefault(f"dg:{dg.id}", _now_ts)
        if _mig:
            _save_state(self._last_triggered)

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
                             dg.name, now_hm, dg.cron_expr, dg.schedule)
                continue

            # Prevent double-fire within MIN_TRIGGER_GAP_SEC
            last_key = f"dg:{dg.id}"
            last = self._last_triggered.get(last_key, 0)
            if now_ts - last < MIN_TRIGGER_GAP_SEC:
                continue

            self._last_triggered[last_key] = now_ts
            logger.info("DigestScheduler: triggering digest for '%s' at %s", dg.name, now_hm)

            # Create TaskCenter task for tracking
            _tid = None
            try:
                if self._task_center:
                    _tid = self._task_center.create_task(
                        'group_digest', 'scheduler',
                        dg.id, dg.name)
                    self._broadcast_task_update(_tid, 'group_digest', 'pending', '准备中', dg.name)
            except Exception:
                logger.warning("[TASK] create failed for '%s'", dg.name)

            # Submit to thread pool for async execution
            _tid_ref = _tid  # capture for closure
            try:
                self._pool.submit(self._run_digest_in_pool, dg.id, _tid_ref)
            except RuntimeError as e:
                logger.error("DigestScheduler: pool submit failed for '%s': %s — "
                             "OA digest loop WILL still run", dg.name, e)

        # ── OA digests ──
        for oa in self._config.oa_groups:
            if not oa.enabled:
                continue
            if not oa.cron_expr:
                continue  # manual trigger only
            try:
                if not cron_matches(oa.cron_expr, now):
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

    def _run_digest_in_pool(self, group_id: str, task_id: int = None) -> None:
        """Wrapper for running group digest in thread pool with error handling.

        入参是 group_id 而不是 DigestGroup 对象：任务可能在队列里等几分钟，
        期间 WebUI 保存过配置就会让捕获的对象属于已被丢弃的旧副本。按 id 在
        运行时重读，从物理上消除"拿着过期对象写回磁盘"这条路。
        """
        dg = self._load_group_fresh_by_id(group_id)
        if dg is None:
            logger.warning("[DIGEST] 分组 %s 已不存在，跳过本次执行", group_id)
            if task_id:
                try:
                    self._task_center.fail_task(task_id, error='分组已被删除')
                    self._broadcast_task_update(task_id, 'group_digest', 'failed', '',
                                                group_id, error='分组已被删除')
                except Exception:
                    pass
            return
        try:
            self._generate_digest(dg, task_id=task_id)
        except Exception:
            logger.exception("Digest generation failed for '%s'", dg.name)
            if task_id:
                try:
                    self._task_center.fail_task(task_id, error='unhandled exception')
                    self._broadcast_task_update(task_id, 'group_digest', 'failed', '', dg.name, error='unhandled exception')
                except Exception:
                    pass

    def _load_group_fresh_by_id(self, group_id: str) -> DigestGroup | None:
        """从磁盘重读配置并按 id 取分组，拿不到返回 None。"""
        if not group_id:
            return None
        try:
            for g in load_assistant_config().digest_groups:
                if g.id == group_id:
                    return g
        except Exception as e:
            logger.warning("[DIGEST] 重读配置失败（group_id=%s）: %s", group_id, e)
        return None

    def _load_group_fresh(self, dg: DigestGroup) -> DigestGroup:
        """持久分组返回磁盘上的最新副本；临时分组（id 为空）原样返回。"""
        if not dg.id:
            return dg
        return self._load_group_fresh_by_id(dg.id) or dg

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
                return cron_matches(dg.cron_expr, now)
            except Exception:
                logger.warning("Invalid cron_expr '%s' for '%s', falling back to schedule",
                               dg.cron_expr, dg.name)
        return now_hm in dg.schedule

    def _generate_digest(self, dg: DigestGroup, task_id: int = None) -> None:
        """分组摘要编排：收集 → 预算判定 → 渲染 → 记忆 → 落库 → 推送。

        签名保持不变（server.py 的手动触发路径直接调它）。
        一次触发 = 一条 outbox 记录 = 一个 task = 一个可重推单元；
        组内是打包还是逐会话属于内部实现细节，用户视角始终是"这个分组的一次摘要"。
        """
        start_ts = time.monotonic()

        # 用磁盘上的最新副本：手动触发路径不经过 _run_digest_in_pool，
        # 而排队中的定时任务也可能在提交后才被 WebUI 改过配置或写过记忆。
        dg = self._load_group_fresh(dg)

        # ── 1/6 收集组内各会话的消息 ──
        units = self._collect_chat_units(dg, task_id)
        if units is None:
            return                      # 无需继续，任务收尾已在内部完成

        # ── 2/6 预算判定：single / packed / per_chat ──
        budget = digest_token_budget()
        memory_tokens = estimate_messages_tokens(
            [{"sender_name": "", "content": dg.memory or ""}])
        plan = plan_digest(units, budget, memory_tokens)
        logger.info("[DIGEST] Step 2/6: '%s' mode=%s budget=%d available=%d total=%d",
                    dg.name, plan.mode, budget, plan.available, plan.total_tokens)
        for note in plan.notes:
            logger.info("[DIGEST]   %s", note)

        # ── 3/6 生成正文 ──
        digest_text, stats = self._render_digest(dg, plan, task_id)

        # ── 4/6 记忆更新（全部失败时跳过，别把错误串浓缩进记忆）──
        if digest_text.startswith("摘要生成失败"):
            logger.warning("[DIGEST] Step 4/6: 摘要失败，跳过记忆更新 for '%s'", dg.name)
        else:
            self._update_group_memory(dg, digest_text)

        # ── 5/6 落 outbox ──
        nid, title, content = self._publish_outbox(dg, digest_text, stats)

        # ── 6/6 推送 + 任务收尾 ──
        self._push_and_finish(dg, nid, title, content, digest_text,
                              task_id, stats, start_ts)

    # ── 1/6 收集 ────────────────────────────────────────────────────

    def _collect_chat_units(self, dg: DigestGroup, task_id=None):
        """取组内每个会话的消息并过滤。

        Returns:
            list[ChatUnit]，或 None 表示本次无需继续（分组无可用会话 /
            窗口内无新消息 / 过滤后无实质内容），任务收尾已在内部完成。
        """
        self._tc_update(task_id, status='running', progress='正在获取消息')

        enabled = [c for c in dg.chats if c.enabled and c.chat_id]
        if not enabled:
            logger.warning("[DIGEST] Step 1/6: 分组 '%s' 没有可用会话", dg.name)
            self._tc_fail(task_id, error='分组没有可用会话，请在网页端重新绑定')
            return None

        since_ts = int(time.time()) - dg.lookback_hours * 3600
        # unread_only 要按会话各自切尾部；WCDB 的 session 拉取需要串行排队，
        # 所以一次拉全表建字典，绝不能每会话拉一次。
        unread_map = self._get_unread_map() if dg.unread_only else {}

        units = []
        for idx, chat in enumerate(enabled, 1):
            self._tc_update(task_id, progress=f'正在获取消息 ({idx}/{len(enabled)})')
            name = chat.name or chat.chat_id
            try:
                raw = self._store.get_messages_since(chat.chat_id, since_ts, limit=500)
            except Exception as e:
                logger.warning("[DIGEST] 取消息失败 '%s' (%s): %s", name, chat.chat_id, e)
                units.append(ChatUnit(chat_id=chat.chat_id, name=name,
                                      error=f"取消息失败: {e}"))
                continue
            if dg.unread_only:
                n = unread_map.get(chat.chat_id, 0)
                raw = raw[-n:] if n > 0 else []
            filtered = filter_messages(raw)
            units.append(ChatUnit(
                chat_id=chat.chat_id,
                name=name,
                messages=filtered,
                est_tokens=estimate_messages_tokens(filtered),
                raw_count=len(raw),
            ))
            logger.info("[DIGEST] Step 1/6: '%s' — raw=%d filtered=%d",
                        name, len(raw), len(filtered))

        total_raw = sum(u.raw_count for u in units)
        total_msgs = sum(len(u.messages) for u in units)
        errored = [u for u in units if u.error]
        if total_raw == 0 and errored:
            # 全部会话取消息失败 ≠ 窗口内没消息。不能伪装成"无新消息"，
            # 否则用户会以为群里真的没人说话。
            detail = "; ".join(f"{u.name}: {u.error}" for u in errored)
            logger.error("[DIGEST] 分组 '%s' 全部 %d 个会话取消息失败: %s",
                         dg.name, len(errored), detail)
            self._tc_fail(task_id, error=f"取消息失败：{detail}")
            return None
        if total_raw == 0:
            # 沿用既有语义：无新消息也写一条 outbox，让用户确认调度确实执行过
            logger.info("[DIGEST] 分组 '%s' 近 %dh 无新消息", dg.name, dg.lookback_hours)
            self._tc_complete(task_id, result='无新内容')
            self._publish_empty_outbox(dg, units)
            return None
        if total_msgs == 0:
            logger.info("[DIGEST] 分组 '%s' 过滤后无实质内容", dg.name)
            self._tc_complete(task_id, result='无实质内容')
            return None
        return units

    def _get_unread_map(self) -> dict:
        """一次拉取全部会话的未读数，返回 {username: unread_count}。

        取代原来的 _get_unread_count(chat_id)：那个实现每会话都要
        get_sessions(limit=1000) 再线性查找，组内 N 个会话就是 N 次全量拉取，
        而 WCDB 的 DLL 调用需要串行排队。
        """
        try:
            from src.web.api_handlers import get_wcdb_client
            client = get_wcdb_client()
            if not client:
                return {}
            return {s.get("username"): int(s.get("unread_count", 0) or 0)
                    for s in client.get_sessions(limit=1000) if s.get("username")}
        except Exception as e:
            logger.warning("Failed to get unread map: %s", e)
            return {}

    # ── 3/6 渲染 ────────────────────────────────────────────────────

    def _render_digest(self, dg: DigestGroup, plan, task_id):
        """按 plan.mode 生成正文。Returns (正文, stats dict)。"""
        if plan.mode == "per_chat":
            self._tc_update(task_id, progress='超出预算，改为逐会话摘要')
            return self._render_per_chat(dg, plan, task_id)
        try:
            text = self._render_once(dg, plan, task_id)
            return text, self._plan_stats(plan, failed=[])
        except LLMContextOverflowError as e:
            # 估算器保守但不保证；provider 的真实反馈才是最终裁判。
            # 就地降级而不是失败 —— 用户仍然能拿到摘要。
            logger.warning("[DIGEST] 打包被 provider 拒绝（%s），就地降级为逐会话", e)
            plan.mode = "per_chat"
            plan.notes.insert(0, f"打包调用被 provider 拒绝，已降级为逐会话：{e}")
            self._tc_update(task_id, progress='打包超限，改为逐会话摘要')
            return self._render_per_chat(dg, plan, task_id)

    def _render_once(self, dg: DigestGroup, plan, task_id) -> str:
        """一次 LLM 调用覆盖整组（single 与 packed 共用）。

        single 模式也点名会话：分组可能配了 5 个会话而本轮只有 1 个有新消息，
        不点名的话摘要正文无法归属，写进组记忆后就成了"我们群如何如何"，
        而这份记忆下一轮会作为共用上下文喂给全部会话。
        """
        active = [u for u in plan.units if u.messages]
        packed = plan.mode == "packed"
        self._tc_update(task_id, progress='AI 生成摘要中')
        self._broadcast_task_update(task_id, 'group_digest', 'running',
                                    'AI 生成摘要中', dg.name)

        system_prompt = self._digest_system_prompt(dg, plan.mode)
        if packed:
            prompt = build_packed_prompt(dg, plan.units)
        else:
            prompt = build_digest_prompt(dg, active[0].messages, active[0].name)
        logger.info("[DIGEST] Step 3/6: '%s' mode=%s system_len=%d user_len=%d",
                    dg.name, plan.mode, len(system_prompt), len(prompt))

        extra = {
            "group_id": dg.id,
            "group_name": dg.name,
            "digest_mode": plan.mode,
            "chat_count": len(active),
            "chats": [u.chat_id for u in active],
            "msg_count": sum(len(u.messages) for u in active),
            "unread_only": dg.unread_only,
            "lookback_hours": dg.lookback_hours,
            "has_custom_prompt": bool(dg.profile and dg.profile.custom_prompt),
        }
        return self._call_digest_llm(dg, system_prompt, prompt, extra, reraise=False)

    def _render_per_chat(self, dg: DigestGroup, plan, task_id):
        """降级路径：逐会话摘要后**纯字符串拼接**，不再调 LLM 汇总。

        骨架对齐 oa_digest._map_reduce_digest（并行 map + 按 idx 复序 +
        单块失败降级），唯一区别是 reduce 阶段零 LLM 调用。
        """
        active = [u for u in plan.units if u.messages]
        system_prompt = self._digest_system_prompt(dg, "per_chat")

        results = {}
        with ThreadPoolExecutor(max_workers=DIGEST_PER_CHAT_WORKERS) as pool:
            fut2idx = {}
            for idx, unit in enumerate(active):
                fut2idx[pool.submit(self._summarize_one_chat, dg, unit, system_prompt)] = idx
            for fut in as_completed(fut2idx):
                idx = fut2idx[fut]
                try:
                    results[idx] = (fut.result(timeout=DIGEST_LLM_TIMEOUT_SEC + 30), "")
                except Exception as e:
                    logger.error("[DIGEST] 会话 '%s' 单独摘要失败: %s", active[idx].name, e)
                    results[idx] = ("", str(e)[:200])
                self._tc_update(task_id, progress=f'逐会话摘要 ({len(results)}/{len(active)})')

        sections = []
        failed = []
        for idx, unit in enumerate(active):
            text, err = results.get(idx, ("", "内部错误：结果丢失"))
            if err:
                failed.append(unit.name)
            sections.append((unit, text, err))

        body = concat_sections(sections)
        if failed and len(failed) == len(active):
            # 全部失败 → 沿用既有的"摘要生成失败"前缀判定，让上层 fail 任务
            detail = "; ".join(f"{u.name}: {e}" for (u, _t, e) in sections if e)
            body = f"摘要生成失败: {detail}"
        elif failed:
            logger.warning("[DIGEST] 分组 '%s' 有 %d/%d 个会话摘要失败: %s",
                           dg.name, len(failed), len(active), failed)
        return body, self._plan_stats(plan, failed=failed)

    def _summarize_one_chat(self, dg: DigestGroup, unit, system_prompt: str) -> str:
        """单会话摘要单元。失败时抛异常，由 _render_per_chat 记为该会话的错误。"""
        prompt = build_digest_prompt(dg, unit.messages, unit.name)
        extra = {
            "group_id": dg.id,
            "group_name": dg.name,
            "digest_mode": "per_chat",
            "chat_id": unit.chat_id,
            "chat_name": unit.name,
            "msg_count": len(unit.messages),
            "dropped": unit.dropped,
            "lookback_hours": dg.lookback_hours,
        }
        return self._call_digest_llm(dg, system_prompt, prompt, extra, reraise=True)

    def _digest_system_prompt(self, dg: DigestGroup, mode: str) -> str:
        """custom_prompt 完全替代默认指令；style 预设追加；结构约束按 mode 追加。

        Args:
            mode: "single" / "packed" / "per_chat"，即 plan.mode。
        """
        if dg.profile and dg.profile.custom_prompt:
            system_prompt = dg.profile.custom_prompt
        else:
            system_prompt = DIGEST_SYSTEM_PROMPT
            style = dg.profile.style if dg.profile else ""
            if style and style in STYLE_PRESETS:
                system_prompt += STYLE_PRESETS[style]
        # 标题层级是**结构约束**而不是风格指令，所以即使 custom_prompt 完全
        # 替代了摘要指令也必须追加，否则外层没法按会话切分、记忆也无法归属。
        if mode == "packed":
            system_prompt += PACKED_OUTPUT_CONTRACT
        elif mode == "per_chat":
            # 外层 concat_sections 自己加 ## 会话名，模型一个标题都不要出
            system_prompt += PER_CHAT_NO_HEADING_HINT
        else:
            system_prompt += SINGLE_HEADING_CONTRACT
        return system_prompt

    def _call_digest_llm(self, dg: DigestGroup, system_prompt: str, prompt: str,
                         extra: dict, reraise: bool) -> str:
        """调 LLM 并记 llm 日志。

        Args:
            reraise: True 时普通失败向上抛（降级路径据此把单个会话标成失败段落）；
                False 时转成"摘要生成失败: ..."字符串（打包/单会话路径沿用旧行为）。
                LLMContextOverflowError 两种情况都向上抛 —— 调用方要据此降级。
        """
        llm_start = time.monotonic()
        try:
            text = self._summarizer._call_digest_api(
                system_prompt,
                [{"role": "user", "content": prompt}],
                timeout=DIGEST_LLM_TIMEOUT_SEC,
            ) or "摘要生成失败"
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=system_prompt, user_prompt=prompt, response=text,
                latency_ms=(time.monotonic() - llm_start) * 1000, extra=extra,
            )
            logger.info("[DIGEST] LLM ok for '%s' — len=%d preview=%s",
                        dg.name, len(text), text[:100].replace('\n', ' '))
            return text
        except LLMContextOverflowError as e:
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=system_prompt, user_prompt=prompt,
                response=f"[ContextOverflow: {e}]",
                latency_ms=(time.monotonic() - llm_start) * 1000,
                extra={**extra, "error": str(e), "prompt_tokens": e.prompt_tokens},
            )
            raise
        except Exception as e:
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=system_prompt, user_prompt=prompt,
                response=f"[Error: {e}]",
                latency_ms=(time.monotonic() - llm_start) * 1000,
                extra={**extra, "error": str(e)},
            )
            logger.error("[DIGEST] LLM call failed for '%s': %s", dg.name, e)
            if reraise:
                raise
            return f"摘要生成失败: {e}"

    @staticmethod
    def _plan_stats(plan, failed) -> dict:
        return {
            "digest_mode": plan.mode,
            "degraded": plan.mode == "per_chat",
            "msg_count": sum(len(u.messages) for u in plan.units),
            "dropped_total": sum(u.dropped for u in plan.units),
            # 取消息阶段就失败的会话也要暴露出来，否则它会从摘要里静默消失
            "failed_chats": list(failed) + [u.name for u in plan.units if u.error],
            "chat_names": [u.name for u in plan.units if u.messages],
            "notes": list(plan.notes),
        }

    @staticmethod
    def _format_chat_names(names: list) -> str:
        if not names:
            return "（无）"
        if len(names) <= 3:
            return "、".join(names)
        return "、".join(names[:3]) + f" 等 {len(names)} 个"

    # ── 4/6 记忆 ────────────────────────────────────────────────────

    def _update_group_memory(self, dg: DigestGroup, digest_text: str) -> None:
        """摘要后更新组级记忆。仅在 memory_enabled 为真时执行。"""
        if not dg.memory_enabled:
            logger.debug("[DIGEST] Step 4/6: 记忆更新已跳过 for '%s' (memory_enabled=false)",
                         dg.name)
            return
        # 会话列表口径必须与 config.group_memory_budget 一致：prompt 里承诺的
        # 字数和落盘时的截断位置得是同一个数，否则 LLM 按要求写满却被切掉一块。
        chat_names = [c.name or c.chat_id for c in dg.chats
                      if c.enabled and c.chat_id]
        budget = group_memory_budget(dg)
        mem_system_prompt = ("你是一个分组聊天记忆助手，负责按会话记录一个摘要分组内"
                             "各个会话的要点。用中文，按 `### 会话名` 分块，"
                             f"不要把一个会话的事写进另一个会话，全文不超过 {budget} 字。")
        mem_start = time.monotonic()
        mem_prompt = generate_memory_update_prompt(dg.memory, digest_text,
                                                   chat_names, budget)
        try:
            new_memory = self._summarizer._call_chat_api(
                mem_system_prompt, [{"role": "user", "content": mem_prompt}])
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest_memory",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=mem_system_prompt, user_prompt=mem_prompt,
                response=new_memory or "",
                latency_ms=(time.monotonic() - mem_start) * 1000,
                extra={"group_id": dg.id, "group_name": dg.name,
                       "existing_memory_len": len(dg.memory or "")},
            )
            if new_memory:
                # 按 id 写回磁盘，不改内存对象再整份覆盖写配置（见 config.mutate_config）
                rev = update_digest_group_memory(dg.id, new_memory)
                # 与落盘同一套截断口径，否则 dg.memory 和磁盘长期不一致
                dg.memory = truncate_memory(new_memory, budget)
                dg.memory_rev = rev
                logger.info("[DIGEST] Step 4/6: 记忆已更新 for '%s' (%d chars, rev=%d)",
                            dg.name, len(dg.memory), rev)
            else:
                logger.warning("[DIGEST] Step 4/6: LLM 返回空记忆，保留原值 for '%s'", dg.name)
        except Exception as e:
            log_llm_interaction(
                backend=getattr(self._summarizer, "_backend_name", "unknown"),
                call_type="group_digest_memory",
                model=getattr(self._summarizer, "model", "unknown"),
                system_prompt=mem_system_prompt, user_prompt=mem_prompt,
                response=f"[Error: {e}]",
                latency_ms=(time.monotonic() - mem_start) * 1000,
                extra={"group_id": dg.id, "group_name": dg.name, "error": str(e)},
            )
            logger.warning("[DIGEST] Step 4/6: 记忆更新失败 for '%s': %s", dg.name, e)

    # ── 5/6 + 6/6 落库与推送 ────────────────────────────────────────

    def _publish_empty_outbox(self, dg: DigestGroup, units: list) -> None:
        mode_label = "未读" if dg.unread_only else f"{dg.lookback_hours}h"
        names = [u.name for u in units]
        self._outbox.add(
            notif_type="group_digest",
            chat_id=dg.id,
            group_name=dg.name,
            title=f"📋 群聊摘要 · {dg.name} ({mode_label})",
            content=json.dumps({
                "group": dg.name,
                "chats": names,
                "lookback_hours": dg.lookback_hours,
                "mode": mode_label,
                "msg_count": 0,
                "digest_mode": "none",
                "degraded": False,
                "dropped_total": 0,
                "digest": "该时间窗口内无新消息，摘要跳过。",
                "display": (f"📋 **分组:** {dg.name}\n"
                            f"👥 **会话:** {self._format_chat_names(names)}\n"
                            f"📊 **消息:** 0 条 | ⏰ **时间:** 近 {dg.lookback_hours}h\n\n"
                            f"> 该时间窗口内无新消息，摘要跳过。"),
            }, ensure_ascii=False),
            priority="normal",
        )

    def _publish_outbox(self, dg: DigestGroup, digest_text: str, stats: dict):
        """写 outbox。Returns (nid, title, content)。

        chat_id 存**组 id**（与 OA 摘要用 oa.id 一致）：一次触发 = 一条记录
        = 一个可重推单元。旧记录的 chat_id 仍是会话 id。
        """
        mode_label = "未读" if dg.unread_only else f"{dg.lookback_hours}h"
        title = f"📋 群聊摘要 · {dg.name} ({mode_label})"
        names = stats.get("chat_names", [])
        content = json.dumps({
            "group": dg.name,
            "chats": names,
            "lookback_hours": dg.lookback_hours,
            "mode": mode_label,
            "msg_count": stats["msg_count"],
            "digest_mode": stats["digest_mode"],
            "degraded": stats["degraded"],
            "dropped_total": stats["dropped_total"],
            "failed_chats": stats["failed_chats"],
            "digest": digest_text,
            "display": (f"📋 **分组:** {dg.name}\n"
                        f"👥 **会话:** {self._format_chat_names(names)}\n"
                        f"📊 **消息:** {stats['msg_count']} 条 | ⏰ **时间:** 近 {dg.lookback_hours}h"
                        f"{' | ⚠️ 已降级为逐会话' if stats['degraded'] else ''}\n\n{digest_text}"),
        }, ensure_ascii=False)
        nid = self._outbox.add(
            notif_type="group_digest",
            chat_id=dg.id,
            group_name=dg.name,
            title=title,
            content=content,
            priority="normal",
        )
        logger.info("[DIGEST] Step 5/6: Outbox entry #%d created for '%s'", nid, dg.name)
        return nid, title, content

    def _push_and_finish(self, dg: DigestGroup, nid: int, title: str, content: str,
                         digest_text: str, task_id, stats: dict, start_ts: float) -> None:
        self._tc_update(task_id, outbox_id=nid)
        self._tc_update(task_id, progress='推送中')

        # 推给所有已扫码绑定的渠道；legacy push_target 只是旧的 opt-in 标记。
        try:
            from src.im.delivery import DeliveryRequest, DeliveryService, get_delivery_service
            from src.im.targets import bound_push_targets
            bound = bound_push_targets()
            if not bound:
                self._outbox.update_push_result(nid, "", "skipped", "未绑定任何推送渠道")
                self._tc_push_result(task_id, "skipped", "未绑定任何推送渠道")
            else:
                push_data = json.loads(content) if isinstance(content, str) else content
                push_text = push_data.get("display", content)
                # 所有绑定渠道用同一套产品级文本格式，不由第一个渠道决定。
                push_msg = DeliveryService.format_text("", title, push_text)
                result = get_delivery_service().send_text(DeliveryRequest(
                    platform=bound[0],
                    text=push_msg,
                    source_type="group_digest",
                    source_id=str(nid),
                    outbox_id=nid,
                    task_id=task_id or 0,
                    auto_route=True,
                    conversation_key=dg.id,
                ))
                push_ok = result.get("success", False)
                # 三态聚合的唯一实现是 delivery.aggregate_status，业务侧不得重写
                push_status = result.get("status") or ("success" if push_ok else "failed")
                push_err = "" if push_status == "success" else result.get("error", "")
                self._outbox.update_push_result(
                    nid, DeliveryService.outbox_channel(result, bound),
                    push_status, push_err,
                )
                self._tc_push_result(task_id, push_status, push_err)
                logger.info("Digest IM push %s for '%s'", push_status, dg.name)
                try:
                    from src.web.api_handlers import broadcast_event
                    broadcast_event("digest_push_result", {
                        "group_name": dg.name,
                        "success": push_ok,
                        "error": push_err,
                        "session_expired": "session_expired" in push_err,
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.warning("Digest IM push error for '%s': %s", dg.name, e)
            try:
                self._outbox.update_push_result(nid, "", "failed", str(e))
            except Exception:
                pass

        elapsed = (time.monotonic() - start_ts) * 1000
        if digest_text.startswith("摘要生成失败"):
            self._tc_fail(task_id, error=digest_text)
            self._broadcast_task_update(task_id, 'group_digest', 'failed', '', dg.name,
                                        error=digest_text[:100])
        else:
            # result 存完整正文不截断 —— 重推的三级兜底会用到它
            self._tc_complete(task_id, result=digest_text,
                              msg_count=stats["msg_count"])
            self._broadcast_task_update(task_id, 'group_digest', 'completed', '完成', dg.name)
        logger.info("[DIGEST] Step 6/6: '%s' pipeline completed in %.0fms (mode=%s, %d 条消息, 裁剪 %d 条, 失败 %d 个会话)",
                    dg.name, elapsed, stats["digest_mode"], stats["msg_count"],
                    stats["dropped_total"], len(stats["failed_chats"]))

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

        # 失败自动重试一次
        if not result.get("success", False):
            error_msg = result.get("error", "unknown")
            logger.warning("[OA-DIGEST] '%s' 首次生成失败(%s)，30s 后重试...", oa.name, error_msg)
            self._tc_update(task_id, progress='首次失败，30s 后重试')
            self._broadcast_task_update(task_id, 'oa_digest', 'running', '重试中', oa.name)
            import time as _rt
            _rt.sleep(30)
            result = service.generate_digest(oa.id, force=True)

        if not result.get("success", False):
            error_msg = result.get("error", "摘要生成失败")
            logger.error("[OA-DIGEST] '%s' 重试仍失败: %s", oa.name, error_msg)
            self._tc_fail(task_id, error=error_msg)
            self._broadcast_task_update(task_id, 'oa_digest', 'failed', '', oa.name, error=error_msg)
            # 推送失败通知到 ilink
            self._notify_oa_digest_failure(oa, error_msg, task_id=task_id)
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
        self._tc_update(task_id, outbox_id=nid)

        # Auto-deliver OA digest to every QR-bound channel.
        try:
            import json as _json
            from src.im.delivery import DeliveryRequest, DeliveryService, get_delivery_service
            from src.im.targets import bound_push_targets
            bound = bound_push_targets()
            if not bound:
                self._outbox.update_push_result(nid, "", "skipped", "未绑定任何推送渠道")
                self._tc_push_result(task_id, "skipped", "未绑定任何推送渠道")
            else:
                push_data = _json.loads(content) if isinstance(content, str) else content
                push_text = push_data.get("display", content)
                # All bound channels use the same product-wide WeChat/iLink
                # text format; it is not selected by the first bound channel.
                fmt_target = bound[0]
                msg = DeliveryService.format_text("", title, push_text)
                push_result = get_delivery_service().send_text(DeliveryRequest(
                    platform=fmt_target,
                    text=msg,
                    source_type="oa_digest",
                    source_id=str(nid),
                    outbox_id=nid,
                    task_id=task_id or 0,
                    auto_route=True,
                    conversation_key=oa.name,
                ))
                push_ok = push_result.get("success", False)
                push_status = push_result.get("status") or ("success" if push_ok else "failed")
                push_err = "" if push_status == "success" else push_result.get("error", "")
                self._outbox.update_push_result(
                    nid, DeliveryService.outbox_channel(push_result, bound),
                    push_status, push_err,
                )
                self._tc_push_result(task_id, push_status, push_err)
                logger.info("[OA-DIGEST] IM push %s for '%s'", push_status, oa.name)
                try:
                    from src.web.api_handlers import broadcast_event
                    broadcast_event("oa_digest_push_result", {
                        "group_name": oa.name, "success": push_ok, "error": push_err,
                        "session_expired": "session_expired" in push_err,
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[OA-DIGEST] IM push error for '%s': %s", oa.name, e)
            try:
                self._outbox.update_push_result(nid, "", "failed", str(e))
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
        self._tc_complete(task_id, result=digest_text, articles_count=articles_count)
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

    def _notify_oa_digest_failure(self, oa, error_msg: str, task_id=None) -> None:
        """推送 OA 摘要生成失败通知到 ilink + outbox。Never raises."""
        try:
            title = f"⚠️ 公众号摘要失败 · {oa.name}"
            display = f"⚠️ **摘要生成失败**\n📰 **公众号:** {oa.name}\n❌ **原因:** {error_msg}\n\n请稍后手动重试。"
            # 记录到 outbox
            failure_nid = 0
            try:
                failure_nid = int(self._outbox.add(
                    notif_type="oa_digest",
                    chat_id=oa.id,
                    group_name=oa.name,
                    title=title,
                    content=json.dumps({
                        "group": oa.name,
                        "error": error_msg,
                        "display": display,
                    }, ensure_ascii=False),
                    priority="high",
                ) or 0)
            except Exception as e:
                logger.warning("[OA-DIGEST] 失败通知写入 outbox 失败: %s", e)
            # Push the failure notice through every currently bound channel.
            from src.im.delivery import DeliveryRequest, DeliveryService, get_delivery_service
            from src.im.targets import bound_push_targets
            bound = bound_push_targets()
            if bound:
                fmt_target = bound[0]
                msg = DeliveryService.format_text("", title, display)
                result = get_delivery_service().send_text(DeliveryRequest(
                    platform=fmt_target, text=msg, source_type="oa_digest_failure",
                    source_id=str(failure_nid or task_id or ""),
                    notification_id=str(failure_nid or ""),
                    outbox_id=int(failure_nid or 0), task_id=int(task_id or 0),
                    conversation_key=oa.name,
                    auto_route=True,
                ))
                push_ok = bool(result.get("success", False))
                push_status = result.get("status") or ("success" if push_ok else "failed")
                push_err = "" if push_status == "success" else result.get("error", "")
                if failure_nid:
                    self._outbox.update_push_result(
                        failure_nid, DeliveryService.outbox_channel(result, bound),
                        push_status, push_err,
                    )
                if task_id and self._task_center:
                    self._tc_push_result(
                        task_id, push_status, push_err,
                    )
                logger.info("[OA-DIGEST] 失败通知已推送: '%s' status=%s", oa.name, push_status)
            else:
                if failure_nid:
                    self._outbox.update_push_result(
                        failure_nid, "", "skipped", "未绑定任何推送渠道",
                    )
                if task_id and self._task_center:
                    self._tc_push_result(
                        task_id, "skipped", "未绑定任何推送渠道",
                    )
                logger.info("[OA-DIGEST] 失败通知跳过: 未绑定任何推送渠道")
        except Exception as e:
            logger.warning("[OA-DIGEST] _notify_oa_digest_failure 异常: %s", e)
