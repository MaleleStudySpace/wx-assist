"""Bot orchestrator — wires all components and manages the bot lifecycle.

This is the central class that initializes, starts, and gracefully shuts down
the WeChat summarizer bot. It replaces the inline wiring previously in main.py.
"""

import json
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .config import BotConfig, PROJECT_ROOT
from .db import initialize_db, MessageStore
from .summarize import create_summarizer
from .router import MessageRouter
from .utils.logging_config import setup_logging
from .utils.op_logger import op_log

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Background health heartbeat: periodic logging + JSON status file.

    Runs in a daemon thread so it never blocks shutdown.
    """

    def __init__(self, summarizer, router, conn, backend, config: BotConfig,
                 on_tick=None):
        self._summarizer = summarizer
        self._router = router
        self._conn = conn
        self._backend = backend
        self._config = config
        self._on_tick = on_tick or (lambda **kw: None)
        self._start_time = time.time()
        self._running = False
        self._thread: threading.Thread | None = None

    # ── Public API ──────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Health monitor started (interval=5m, daemon)")

    def stop(self) -> None:
        self._running = False

    # ── Internals ───────────────────────────────────────────────────

    _FAST_TICK_SEC = 30    # push message count to dashboard every 30s
    _FULL_TICK_CYCLES = 10  # full health check every 10 fast ticks (5 min)

    def _run(self) -> None:
        cycle = 0
        while self._running:
            time.sleep(self._FAST_TICK_SEC)
            if not self._running:
                break
            cycle += 1
            try:
                # Fast tick (every 30s): push live stats to dashboard
                self._on_tick(
                    messages_processed=self._router.messages_processed,
                    last_api_call_time=self._summarizer.last_api_call_time,
                    last_api_call_sec_ago=(
                        int(time.time() - self._summarizer.last_api_call_time)
                        if self._summarizer.last_api_call_time > 0 else -1
                    ),
                    wechat_online=self._check_wechat_online(),
                    ai_ok=self._check_ai_ok(),
                    model_name=self._get_model_name(),
                    group_count=self._get_group_count(),
                )
                # Full tick (every 300s): logging + JSON + health checks
                if cycle % self._FULL_TICK_CYCLES == 0:
                    self._tick()
            except Exception:
                logger.exception("Health monitor tick failed")

    def _tick(self) -> None:
        uptime_sec = int(time.time() - self._start_time)
        uptime_min = uptime_sec // 60
        msgs = self._router.messages_processed

        db_status = self._check_db()
        wechat_status = self._check_wechat_hwnd()
        last_api_str = self._last_api_ago()

        # Push to Web UI
        self._on_tick(
            uptime_sec=uptime_sec,
            messages_processed=msgs,
            db_ok=db_status == "OK",
            wechat_online=self._check_wechat_online(),
            ai_ok=self._check_ai_ok(),
            model_name=self._get_model_name(),
            group_count=self._get_group_count(),
            last_api_call_time=self._summarizer.last_api_call_time,
            last_api_call_sec_ago=int(time.time() - self._summarizer.last_api_call_time)
                if self._summarizer.last_api_call_time > 0 else -1,
        )

        logger.info(
            "HEARTBEAT: uptime=%dm, msgs=%d, db=%s, wechat=%s, last_api=%s",
            uptime_min, msgs, db_status, wechat_status, last_api_str,
        )

        self._write_status_json()

    def _check_db(self) -> str:
        """Check database connection is alive."""
        try:
            self._conn.execute("SELECT 1")
            return "OK"
        except Exception as e:
            return f"ERR:{e}"

    def _check_wechat_hwnd(self) -> str:
        """Check WeChat window HWND."""
        try:
            health_status = getattr(self._backend, "health_status", None)
            if callable(health_status):
                return str(health_status())

            wc = getattr(self._backend, "_window", None)
            if wc is None:
                return f"{self._config.wechat_backend}_ok"
            hwnd = wc._cached_hwnd
            if hwnd is not None:
                if wc._validate_hwnd(hwnd):
                    return f"HWND_{hwnd}"
            return "no_hwnd"
        except Exception as e:
            return f"ERR:{e}"

    def _check_wechat_online(self) -> bool:
        """Check if iLink push channel is connected (bound + usable).

        Replaces the old WeChat process detection — "微信"状态 now reflects
        whether the push channel is available, not whether the WeChat window
        is open.
        """
        try:
            from src.wechat.ilink_push import get_ilink_push
            ilink = get_ilink_push()
            return ilink.is_healthy()
        except Exception:
            return False

    def _check_ai_ok(self) -> bool:
        """Check if AI API has ever been called successfully."""
        last = self._summarizer.last_api_call_time
        if last <= 0:
            return False  # never called
        # Once connected successfully, stay ok until restart
        return True  # ever called = ok

    def _get_model_name(self) -> str:
        """Get the current AI model name."""
        try:
            return self._config.ai_provider_model or ""
        except Exception:
            return ""

    def _get_group_count(self) -> int:
        """Get number of monitored groups."""
        try:
            # Monitor all groups by default
            sessions = getattr(self._backend, "get_sessions", None)
            if callable(sessions):
                slist = sessions()
                return sum(1 for s in slist if s.get("username", "").endswith("@chatroom"))
            return -1  # unknown count
        except Exception:
            return 0

    def _last_api_ago(self) -> str:
        """Human-readable 'time since last successful API call'."""
        last = self._summarizer.last_api_call_time
        if last <= 0:
            return "never"
        ago = int(time.time() - last)
        if ago < 60:
            return f"{ago}s_ago"
        elif ago < 3600:
            return f"{ago // 60}m_ago"
        else:
            return f"{ago // 3600}h_ago"

    def _write_status_json(self) -> None:
        """Write a lightweight status file for external watchdogs."""
        status = {
            "uptime_sec": int(time.time() - self._start_time),
            "messages_processed": self._router.messages_processed,
            "db_ok": self._check_db() == "OK",
            "wechat_backend": self._config.wechat_backend,
            "last_api_call_sec_ago": (
                int(time.time() - self._summarizer.last_api_call_time)
                if self._summarizer.last_api_call_time > 0
                else -1
            ),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        out_dir = PROJECT_ROOT / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "bot_status.json"
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(status, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)  # atomic write
        except Exception:
            logger.exception("Failed to write status JSON")


class Bot:
    """Orchestrates the WeChat summarizer bot.

    Usage:
        config = load_config()
        bot = Bot(config)
        bot.run()
    """

    def __init__(self, config: BotConfig):
        self._config = config
        self._conn = None
        self._backend = None
        self._health: HealthMonitor | None = None

    def run(self) -> None:
        """Initialize all components and start the bot. Blocks until stopped."""
        config = self._config

        # ── 1. Logging ──────────────────────────────────────────
        setup_logging(level=config.log_level, log_file=config.log_file)
        self._log_banner()
        op_log("BOOT", "Bot 启动: backend=%s model=%s",
               config.wechat_backend, config.ai_provider_model or "(未配置)")

        # ── 2. Database ─────────────────────────────────────────
        self._conn = initialize_db(config.db_path)
        store = MessageStore(self._conn)

        # Notify Web UI early: database is ready
        try:
            from .web.server import update_status as _us
            _us(db_ok=True)
        except Exception:
            pass

        # ── 3. Components ───────────────────────────────────────
        summarizer = create_summarizer(config)

        router = MessageRouter(
            store=store,
            summarizer=summarizer,
            config=config,
        )

        # ── 4. Web UI status ────────────────────────────────────
        # (web server already started by desktop.py)
        try:
            from .web.server import update_status
            update_status(
                wechat_backend=config.wechat_backend,
                model_name=config.ai_provider_model or "",
                restricted_features_enabled=config.enable_restricted_features,
            )
            self._update_status = update_status
        except Exception as e:
            logger.debug("Web UI status: %s", e)
            self._update_status = lambda **kw: None

        # ── 5. WeChat backend ───────────────────────────────────
        backend = self._create_wechat_backend(store)
        self._backend = backend
        self.backend = backend   # public ref for lifecycle control

        # Register backend with web server for stop/restart (explicit
        # API — no monkey-patching needed).
        try:
            from .web.server import _register_backend
            _register_backend(backend)
        except Exception:
            pass

        # ── 5b. Cleanup restricted triggers if disabled ──────────────
        # Triggers persist in WCDB across restarts; if the config switch
        # is off, we must actively uninstall them so they stop taking effect.
        if not config.enable_restricted_features:
            try:
                from .web.api_handlers import cleanup_restricted_triggers
                cleanup_restricted_triggers()
            except Exception as e:
                logger.debug("Restricted trigger cleanup skipped: %s", e)

        # ── 6. Health monitor ───────────────────────────────────
        self._health = HealthMonitor(
            summarizer=summarizer,
            router=router,
            conn=self._conn,
            backend=backend,
            config=config,
            on_tick=self._update_status,
        )
        self._health.start()

        # ── 6. Signal handling ──────────────────────────────────
        def shutdown(signum, frame):
            logger.info("Received signal %d. Shutting down...", signum)
            backend.stop()
            if self._health:
                self._health.stop()

        try:
            signal.signal(signal.SIGINT, shutdown)
            signal.signal(signal.SIGTERM, shutdown)
        except ValueError:
            # Running in a thread — signals not available
            pass

        # ── 7. Start listening (blocks) ─────────────────────────
        #
        # DESIGN NOTE — fire-and-forget callback execution:
        #   WcdbBackend uses a ThreadPoolExecutor (max_workers=4) to
        #   offload AI-triggering callbacks from the poll loop.  The poll
        #   thread submits each message to the pool and returns immediately,
        #   so a slow summarization in one group never blocks polling of
        #   other groups.  Reply sending + WCDB confirmation happen inside
        #   the worker, serialized through a client_lock to keep ctypes
        #   safe.  On shutdown the pool drains with a 30 s timeout.
        #
        # Legacy design (pre-2026-06):
        #   The old single-threaded loop caused head-of-line blocking:
        #   one slow AI call delayed ALL groups' message polling.
        #   The old comment is archived in AUDIT.md §C1.

        # ── 7a. Initialize Assistant (keyword alerts + OA monitor + digest scheduler) ─
        assistant_alert = None
        assistant_scheduler = None
        oa_monitor = None
        content_cache = None  # 由下面 try 块赋值，外层确保引用安全
        try:
            from src.assistant.config import load_assistant_config
            asst_cfg = load_assistant_config()
            from src.assistant.outbox import Outbox
            from src.assistant.alert import AlertEngine

            outbox = Outbox()

            # ── Task Center (task lifecycle tracking) ──
            task_center = None
            try:
                from src.assistant.task_center import TaskCenter
                task_center = TaskCenter()
                task_center.cleanup_expired()
            except Exception as e:
                logger.warning("TaskCenter init failed (continuing without): %s", e)

            # ── Content Cache (本地持久化缓存，OA/SNS/Fav 读取加速) ──
            content_cache = None
            try:
                from src.db.content_cache import ContentCache
                content_cache = ContentCache()
                from src.web.server import register_content_cache
                register_content_cache(content_cache)
                logger.info("[CACHE] ContentCache 已创建并注册")
            except Exception as e:
                logger.warning("[CACHE] ContentCache init failed (continuing without): %s", e)

            # Always create all engines unconditionally so they can be hot-enabled
            # via the API even if assistant_enabled was false at boot.
            # Each engine's check()/tick() no-ops when assistant_enabled=False
            # or when no groups are configured.

            # ── Digest scheduler (thread loop, no-ops when disabled) ──
            from src.assistant.scheduler import DigestScheduler
            assistant_scheduler = DigestScheduler(
                asst_cfg, outbox, summarizer, store,
                wcdb_client=getattr(self, '_wcdb_client', None),
                task_center=task_center,
            )
            assistant_scheduler.start()
            try:
                from src.web.server import register_assistant_scheduler
                register_assistant_scheduler(assistant_scheduler)
            except Exception:
                pass

            # Register TaskCenter with server
            if task_center:
                try:
                    from src.web.server import register_task_center
                    register_task_center(task_center)
                except Exception:
                    pass

            # ── Alert engine (message-triggered, no thread needed) ──
            assistant_alert = AlertEngine(asst_cfg, outbox)
            try:
                from src.web.server import register_assistant_alert
                register_assistant_alert(assistant_alert)
            except Exception:
                pass

            # ── OA Monitor (thread loop, no-ops when no groups) ──
            from src.assistant.oa_monitor import OAMonitorEngine
            oa_monitor = OAMonitorEngine(asst_cfg, outbox,
                                         content_cache=content_cache)
            oa_monitor.start()
            try:
                from src.web.server import register_oa_monitor
                register_oa_monitor(oa_monitor)
            except Exception:
                pass

            logger.info("Assistant: alert engine + OA monitor + digest scheduler initialized")
        except Exception as e:
            logger.warning("Assistant init failed (continuing without): %s", e)

        # ── 7b. AI Agent ─────────────────────────────────────────────
        agent_engine = None
        try:
            from .agent import ToolExecutor, AgentEngine
            from .web.server import register_agent_engine, get_status_snapshot

            tool_executor = ToolExecutor(
                store=store,
                summarizer=summarizer,
                status_fn=get_status_snapshot,
                task_center=task_center,
                scheduler=assistant_scheduler,
            )
            agent_engine = AgentEngine(
                summarizer=summarizer,
                tool_executor=tool_executor,
            )
            register_agent_engine(agent_engine)
            logger.info("Agent engine created")

            # ── RAG Engine (optional, zero impact on failure) ──
            rag_engine = None
            try:
                import importlib
                _rag_mod = importlib.import_module('src.assistant.rag')
                RAGEngine = _rag_mod.RAGEngine
                FastEmbedder = _rag_mod.FastEmbedder
                ChromaStore = _rag_mod.ChromaStore
                SlidingWindowChunker = _rag_mod.SlidingWindowChunker

                embedder = FastEmbedder()
                vec_store = ChromaStore(path="data/chroma")
                chunker = SlidingWindowChunker()
                rag_engine = RAGEngine(store=vec_store, embedder=embedder, chunker=chunker)
                rag_engine.warmup()

                # Inject into Router（用于实时索引）和 ToolExecutor（用于搜索工具）
                router.set_rag(rag_engine)
                tool_executor.set_rag(rag_engine)

                # Register global (for OA Monitor to trigger re-index)
                try:
                    from src.web.server import register_rag_engine
                    register_rag_engine(rag_engine)
                except Exception:
                    pass

                logger.info("[RAG] RAGEngine initialized and injected")

                # ── Cold start (background thread) ──
                def _cold_start_task():
                    try:
                        db_conn = self._conn
                        rag_engine.cold_start(db_conn, tracked_groups=None)
                    except Exception:
                        pass

                threading.Thread(target=_cold_start_task, daemon=True,
                                 name="rag-cold-start").start()

            except Exception as rag_e:
                logger.warning("[RAG] RAGEngine init failed (continuing without): %s", rag_e)
                rag_engine = None

            # ── Cache sync: 后台全量同步 + 定时增量循环（独立于 RAG）──
            if content_cache:
                def _cache_init_task():
                    """初始化缓存：全量同步 + RAG 索引 + 定时增量循环。"""
                    import time as _t
                    _t.sleep(5)  # 等 WCDB client 就绪
                    # 获取 WCDB client
                    _client = None
                    try:
                        from src.web.api_handlers import get_wcdb_client
                        _client = get_wcdb_client()
                    except Exception:
                        pass
                    if not _client:
                        logger.warning("[CACHE] WCDB 不可用，全量同步跳过")
                        return

                    # 全量同步（三源）— 已有缓存则跳过，避免重启重复拉取
                    try:
                        _cnt = content_cache.query("SELECT COUNT(*) as c FROM oa_cache")[0]["c"]
                        if _cnt == 0:
                            content_cache.sync_oa_all(_client, task_center)
                    except Exception:
                        content_cache.sync_oa_all(_client, task_center)
                    try:
                        _cnt = content_cache.query("SELECT COUNT(*) as c FROM sns_cache")[0]["c"]
                        if _cnt == 0:
                            content_cache.sync_sns_all(_client, task_center)
                    except Exception:
                        content_cache.sync_sns_all(_client, task_center)
                    try:
                        _cnt = content_cache.query("SELECT COUNT(*) as c FROM fav_cache")[0]["c"]
                        if _cnt == 0:
                            content_cache.sync_fav_all(_client, task_center)
                    except Exception:
                        content_cache.sync_fav_all(_client, task_center)

                    # RAG 索引缓存数据（仅在 RAG 可用时）
                    if rag_engine:
                        for _src in ("oa", "sns", "fav"):
                            try:
                                content_cache.index_to_rag(rag_engine, _src)
                            except Exception:
                                pass
                    logger.info("[CACHE] 全量同步完成")

                threading.Thread(target=_cache_init_task, daemon=True,
                                 name="cache-init").start()

                # 启动 OA 全文抓取队列（每秒 1 篇），传入 task_center 追踪
                content_cache.start_oa_content_fetcher(task_center=task_center)

                # 定时增量同步（SNS 5min / Fav 10min）
                def _sns_timer():
                    import time as _t
                    while True:
                        _t.sleep(300)
                        try:
                            from src.web.api_handlers import get_wcdb_client
                            _c = get_wcdb_client()
                            if _c:
                                _n = content_cache.sync_sns_incremental(_c, task_center)
                                if _n > 0 and rag_engine:
                                    content_cache.index_to_rag(rag_engine, "sns")
                        except Exception:
                            pass
                threading.Thread(target=_sns_timer, daemon=True,
                                 name="sns-sync-timer").start()

                def _fav_timer():
                    import time as _t
                    while True:
                        _t.sleep(600)
                        try:
                            from src.web.api_handlers import get_wcdb_client
                            _c = get_wcdb_client()
                            if _c:
                                _n = content_cache.sync_fav_incremental(_c, task_center)
                                if _n > 0 and rag_engine:
                                    content_cache.index_to_rag(rag_engine, "fav")
                        except Exception:
                            pass
                threading.Thread(target=_fav_timer, daemon=True,
                                 name="fav-sync-timer").start()

                # ── OA 60s 增量定时器（TaskCenter 追踪） ──
                def _oa_timer():
                    import time as _t
                    while True:
                        _t.sleep(60)
                        try:
                            from src.web.api_handlers import get_wcdb_client
                            _c = get_wcdb_client()
                            if _c:
                                _n = content_cache.sync_oa_incremental(_c, task_center)
                                if _n > 0 and rag_engine:
                                    content_cache.index_to_rag(rag_engine, "oa")
                        except Exception:
                            pass
                threading.Thread(target=_oa_timer, daemon=True,
                                 name="oa-sync-timer").start()

                # ── OA 账号 30min 刷新定时器 ──
                def _oa_accounts_timer():
                    import time as _t
                    while True:
                        _t.sleep(1800)
                        try:
                            from src.web.api_handlers import get_wcdb_client
                            _c = get_wcdb_client()
                            if _c:
                                content_cache.sync_oa_accounts(_c, task_center)
                        except Exception:
                            pass
                threading.Thread(target=_oa_accounts_timer, daemon=True,
                                 name="oa-accounts-timer").start()

            # ── Start MCP Server (daemon thread) ───────────────────
            try:
                from .agent.mcp_server import start_mcp_server
                start_mcp_server(tool_executor.registry)
            except Exception as mcp_e:
                logger.warning("MCP server not started: %s", mcp_e)
        except Exception as e:
            logger.warning("Failed to create agent engine: %s", e)

        # ── 7c. Inject agent_engine into router ─────────────────────
        try:
            router.set_agent_engine(agent_engine)
        except Exception:
            pass

        # ── 7d. Register router.handle as iLink callback ────────────
        try:
            from .web.server import register_ilink_callback
            register_ilink_callback(router.handle)
            logger.info("iLink callback registered (router.handle)")
        except Exception as e:
            logger.debug("iLink callback not registered: %s", e)

        # ── 7e. Auto-start iLink receiver if account was bound ──────
        try:
            from src.wechat.ilink_push import get_ilink_push
            ilink = get_ilink_push()
            if ilink.is_available():
                from .web.server import _start_ilink_receiver
                _start_ilink_receiver()
                logger.info("iLink receiver auto-started (bound account found)")
        except Exception as e:
            logger.debug("iLink auto-start skipped: %s", e)

        # Wrap callback to include assistant alert checking.
        # IMPORTANT: alert.check() must always run even if router.handle()
        # throws an exception — otherwise keyword alerts silently fail when
        # the message store or AI backend has transient errors.
        def _wrapped_callback(msg):
            reply = None
            try:
                reply = router.handle(msg)
            except Exception as e:
                logger.warning("router.handle() failed for msg in %s: %s",
                               msg.get("group_name", msg.get("chat_id", "?"))[:20], e)
            if assistant_alert is not None:
                try:
                    assistant_alert.check(msg)
                except Exception as e:
                    logger.warning("assistant_alert.check() failed: %s", e)
            return reply

        try:
            logger.info("Bot is running. Press Ctrl+C to stop.")
            backend.start(_wrapped_callback)
        except KeyboardInterrupt:
            pass
        finally:
            if oa_monitor is not None:
                try:
                    oa_monitor.stop()
                    try:
                        from src.web.server import register_oa_monitor
                        register_oa_monitor(None)
                    except Exception:
                        pass
                except Exception:
                    pass
            if assistant_scheduler is not None:
                try:
                    assistant_scheduler.stop()
                    # Unregister from server module
                    try:
                        from src.web.server import register_assistant_scheduler
                        register_assistant_scheduler(None)
                    except Exception:
                        pass
                except Exception:
                    pass
            if self._health:
                self._health.stop()
            try:
                from .web.server import _stop_ilink_receiver
                _stop_ilink_receiver()
            except Exception:
                pass
            if self._conn is not None:
                self._conn.close()
            try:
                self._update_status(running=False)
            except Exception:
                pass
            logger.info("Bot shut down gracefully.")

    # ── Helpers ──────────────────────────────────────────────────

    def _log_banner(self) -> None:
        """Log the startup banner with configuration details."""
        config = self._config
        logger.info("=" * 50)
        logger.info("wx-assist starting...")
        logger.info("WeChat backend: %s", config.wechat_backend)
        logger.info("AI model: %s", config.ai_provider_model or "(未配置)")
        logger.info("DB path: %s", config.db_path)
        logger.info("=" * 50)

    def _create_wechat_backend(self, store=None):
        """Create the appropriate WeChat backend based on config.

        Returns an AbstractWeChatBackend instance.
        """
        config = self._config
        groups = ["*"]  # monitor all groups by default

        if config.wechat_backend == "wcdb":
            from .wechat.wcdb_backend import WcdbBackend
            return WcdbBackend(
                groups=groups,
                poll_sec=config.poll_interval_sec,
                store=store,
            )

        if config.wechat_backend == "mac_ui":
            from .wechat.mac_ui_backend import MacUIBackend
            return MacUIBackend(
                groups=groups,
                poll_sec=config.poll_interval_sec,
                store=store,
            )

        if config.wechat_backend == "mac_hybrid":
            from .wechat.mac_hybrid_backend import MacHybridBackend
            return MacHybridBackend(
                groups=groups,
                poll_sec=config.poll_interval_sec,
                store=store,
            )

        else:
            raise ValueError(
                f"Unknown WECHAT_BACKEND: '{config.wechat_backend}'. "
                f"Supported: wcdb, mac_ui, mac_hybrid."
            )
