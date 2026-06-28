"""
Zero-dependency web UI server for the bot dashboard.

Uses only Python stdlib (http.server for HTTP + WebSocket).
Serves the React UI from ui/dist/ and provides bot status via WebSocket.

Runs in a daemon thread — no impact on the main bot loop.
"""
import json
import logging
import os
import struct
import threading
import time
from hashlib import sha1
from base64 import b64encode
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

# Used in _handle_request() conditionals; must be imported at module level
# to avoid UnboundLocalError in branches that reference it.
from src.config import _decode_wechat_groups

logger = logging.getLogger(__name__)

import sys as _sys
if getattr(_sys, "frozen", False):
    UI_DIR = (Path(_sys._MEIPASS) / "ui" / "dist").resolve()
else:
    UI_DIR = (Path(__file__).resolve().parent.parent.parent / "ui" / "dist").resolve()
WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _messages_table_exists(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'"
    ).fetchone()
    return row is not None


def _find_or_create_env() -> Path:
    """Find .env file, using the canonical search order from config.py.

    If .env is not found but .env.example is, copy it to create a new .env.
    """
    import sys

    # 1. Use the canonical search from config.py (consistent across the app)
    from src.config import find_env_file
    existing = find_env_file()
    if existing:
        return existing

    # 2. Not found — try to create from .env.example
    # .env.example is bundled into _MEIPASS in frozen mode.
    if getattr(sys, "frozen", False):
        env_example = Path(sys._MEIPASS) / ".env.example"
    else:
        env_example = Path(__file__).resolve().parent.parent.parent / ".env.example"

    if env_example.exists():
        # Create .env in app home (CWD already set to EXE dir / project root)
        env_path = Path(".env")
        env_path.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Created .env from .env.example at %s", env_path.resolve())
        return env_path

    # 3. Last resort: create minimal .env in app home
    env_path = Path(".env")
    env_path.write_text(
        "AI_BACKEND=deepseek\n"
        "DEEPSEEK_API_KEY=\n"
        "WECHAT_BACKEND=wcdb\n",
        encoding="utf-8",
    )
    logger.info("Created minimal .env at %s", env_path.resolve())
    return env_path


def _detect_default_data_dir() -> str:
    """Auto-detect the default WeChat data directory (parent of wxid_*).

    Returns the base directory path string, or empty string if not found.
    Used by the UI to show what auto-detection would use.
    """
    import os as _os
    candidates = [
        Path(_os.environ.get("USERPROFILE", "")) / "Documents" / "xwechat_files",
        Path(_os.environ.get("USERPROFILE", "")) / "Documents" / "WeChat Files",
    ]
    for base in candidates:
        if not base.exists():
            continue
        try:
            wxid_dirs = [d for d in base.iterdir() if d.is_dir() and d.name.startswith("wxid_")]
            for wxid_dir in wxid_dirs:
                session_db = wxid_dir / "db_storage" / "session" / "session.db"
                if session_db.exists():
                    return str(base)
        except PermissionError:
            continue
    return ""


def _detect_wxid_and_db_path():
    """Auto-detect WeChat wxid and database path from common locations.

    Respects WECHAT_DATA_DIR env var as a custom base dir (scanned first).
    """
    import os as _os

    candidates: list[Path] = []

    # 1. Custom path from env (highest priority)
    custom_dir = _os.environ.get("WECHAT_DATA_DIR", "").strip()
    if custom_dir:
        custom = Path(custom_dir)
        if custom.exists() and custom.is_dir():
            candidates.append(custom)

    # 2. Default locations
    candidates += [
        Path(_os.environ.get("USERPROFILE", "")) / "Documents" / "xwechat_files",
        Path(_os.environ.get("USERPROFILE", "")) / "Documents" / "WeChat Files",
    ]
    for base in candidates:
        if not base.exists():
            continue
        wxid_dirs = sorted(
            [d for d in base.iterdir() if d.is_dir() and d.name.startswith("wxid_")],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for wxid_dir in wxid_dirs:
            session_db = wxid_dir / "db_storage" / "session" / "session.db"
            if session_db.exists():
                return wxid_dir.name, str(session_db)
            # Older WeChat versions
            msg_dir = wxid_dir / "Msg"
            if msg_dir.exists():
                db_files = sorted(msg_dir.glob("MSG*.db"), key=lambda f: f.stat().st_mtime, reverse=True)
                if db_files:
                    return wxid_dir.name, str(db_files[0])
    return None, None


def _set_env_key(env_path: Path, key: str, value: str) -> None:
    """Set or update one key=value in a .env file atomically (file-lock protected)."""
    from src.config import write_env_atomic
    write_env_atomic(env_path, {key: value})


def _write_onboarding_to_env(env_path):
    """Write accumulated onboarding data to .env file atomically (file-lock protected)."""
    from src.config import write_env_atomic
    env_map = {
        "AI_PROVIDER_BASE_URL": _onboarding_data.get("ai_provider_base_url", ""),
        "AI_PROVIDER_API_KEY": _onboarding_data.get("ai_provider_api_key", ""),
        "AI_PROVIDER_TYPE": _onboarding_data.get("ai_provider_type", "auto"),
        "AI_PROVIDER_MODEL": _onboarding_data.get("ai_provider_model", ""),
        "WECHAT_BACKEND": _onboarding_data.get("wechat_backend", "wcdb"),
        "MEMORY_CONSOLIDATION_ENABLED": str(_onboarding_data.get("memory_consolidation_enabled", False)).lower(),
        "WCDB_KEY": _onboarding_data.get("key", ""),
        "ONBOARDING_DONE": "true",
    }
    # Filter out None values
    updates = {k: v for k, v in env_map.items() if v is not None}
    write_env_atomic(env_path, updates)
    logger.info("Onboarding complete — wrote .env")


def _run_step1_extraction():
    """Background thread: wait for WeChat exit → restart → hook → capture.

    Uses extract_wcdb_key's on_progress callback to push real-time phase
    updates to the frontend so the user sees exactly what's happening.
    """
    from src.wechat.extract_key import extract_wcdb_key

    def _on_progress(phase, message):
        """Push progress updates to the frontend via _step1_state."""
        with _step1_lock:
            _step1_state["phase"] = phase
            _step1_state["message"] = message

    try:
        # extract_wcdb_key(require_restart=True) handles the full flow.
        # on_progress pushes phase changes so the frontend can display
        # real-time instructions (hooking → waiting_exit → waiting_login
        # → hooking_restart).
        key = extract_wcdb_key(require_restart=True,
                               on_progress=_on_progress)

        if key:
            wxid, db_path = _detect_wxid_and_db_path()
            with _onboarding_lock:
                _onboarding_data["step1_done"] = True
                _onboarding_data["key"] = key
                _onboarding_data["wxid"] = wxid or ""
                _onboarding_data["db_path"] = db_path or ""

            # Persist the key to .env immediately so the bot can use it
            # on restart without needing to complete the full onboarding flow.
            env_path = _find_or_create_env()
            _set_env_key(env_path, "WCDB_KEY", key)
            # Also set in the current process for load_dotenv in this session
            import os as _os
            _os.environ["WCDB_KEY"] = key
            # Clear the KEY_MISSING error so it doesn't reappear on page refresh
            update_status(error="")

            with _step1_lock:
                _step1_state["phase"] = "done"
                _step1_state["message"] = "密钥获取成功"
                _step1_state["result"] = {"key": key, "wxid": wxid or "", "db_path": db_path or ""}
                _step1_state["running"] = False
        else:
            with _step1_lock:
                _step1_state["phase"] = "timeout"
                _step1_state["message"] = "密钥提取超时，请确保微信已登录并重试"
                _step1_state["running"] = False

    except Exception as e:
        logger.exception("Step1 extraction failed")
        with _step1_lock:
            _step1_state["phase"] = "error"
            _step1_state["message"] = str(e)
            _step1_state["running"] = False


def _list_dir_entries(target: Path) -> list[dict]:
    """List directory entries for the filesystem browser API.

    Returns only directories (the user is browsing for parent dir of wxid_*).
    Sorted: directories first, then alphabetically.
    """
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".") or child.name.startswith("$"):
                continue  # skip hidden/system entries
            entries.append({
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir(),
            })
    except PermissionError:
        pass
    return entries


def _read_recent_logs():
    """Read the last 500 lines from the bot log file. Returns JSON-serializable list.

    Log format: ``YYYY-MM-DD HH:MM:SS [LEVEL] module: message``
    (configured in src/utils/logging_config.py).

    Uses seek-from-end to avoid reading the entire log file into memory
    (bot.log can grow to 100+ MB over days of operation).
    """
    import re
    # CWD is set to app home by desktop.py; relative path works for both
    # frozen (EXE dir) and dev (project root) modes.
    log_path = Path("data/bot.log")
    if not log_path.exists():
        return {"ok": True, "logs": [], "message": "日志文件尚未创建"}
    try:
        # Read only the tail of the file (last ~256KB) instead of the whole thing.
        # 256KB is enough for ~500 lines of typical log entries (~500 bytes each).
        TAIL_BYTES = 256 * 1024
        file_size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            if file_size > TAIL_BYTES:
                f.seek(file_size - TAIL_BYTES)
                # Discard the first (partial) line since we likely landed mid-line
                f.readline()
            raw = f.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()
        # Return last 500 lines
        recent = lines[-500:]
        # Regex: timestamp [LEVEL] module: message
        pattern = re.compile(
            r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+'
            r'\[(DEBUG|INFO|WARNING|ERROR)\]\s+'
            r'([^:]+):\s+'
            r'(.*)$'
        )
        entries = []
        for line in recent:
            entry = {"raw": line}
            m = pattern.match(line.strip())
            if m:
                entry["ts"] = m.group(1)
                entry["level"] = m.group(2)
                entry["module"] = m.group(3)
                entry["msg"] = m.group(4)
            else:
                # Fallback for lines that don't match (tracebacks, multi-line, etc.)
                entry["ts"] = ""
                entry["level"] = "INFO"
                entry["module"] = ""
                entry["msg"] = line
            entries.append(entry)
        return {"ok": True, "logs": entries}
    except Exception as e:
        return {"ok": False, "logs": [], "error": str(e)}


def _read_recent_llm_logs():
    """Read the last 200 LLM log entries from data/llm.log.

    Each [LLM-DETAIL] line is a JSON object with full request/response.
    Returns JSON-serializable list of parsed entries.
    """
    import json as _json
    log_path = Path("data/llm.log")
    if not log_path.exists():
        return {"ok": True, "logs": [], "message": "LLM 日志文件尚未创建"}
    try:
        TAIL_BYTES = 512 * 1024  # 512KB — LLM detail lines are larger
        file_size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            if file_size > TAIL_BYTES:
                f.seek(file_size - TAIL_BYTES)
                f.readline()  # skip partial line
            raw = f.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()

        entries = []
        for line in lines[-500:]:
            # Only parse [LLM-DETAIL] lines (JSON)
            idx = line.find("[LLM-DETAIL] ")
            if idx < 0:
                continue
            json_str = line[idx + len("[LLM-DETAIL] "):]
            try:
                entry = _json.loads(json_str)
                entries.append(entry)
            except _json.JSONDecodeError:
                # Skip malformed lines
                continue

        # Return last 200 entries (newest last → reverse for display)
        return {"ok": True, "logs": entries[-200:]}
    except Exception as e:
        return {"ok": False, "logs": [], "error": str(e)}


def _can_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def _platform_dependency_report(system_name=None, import_checker=None, command_checker=None):
    """Return platform-aware dependency diagnostics for onboarding."""
    import platform
    import shutil

    system = system_name or platform.system()
    import_checker = import_checker or _can_import
    command_checker = command_checker or shutil.which

    req_mapping = {
        "dotenv": "python-dotenv",
        "anthropic": "anthropic",
        "openai": "openai",
        "pydantic": "pydantic",
        "webview": "pywebview",
        "PIL": "Pillow",
        "psutil": "psutil",
        "pyperclip": "pyperclip",
    }
    if system == "Windows":
        req_mapping.update({
            "uiautomation": "uiautomation",
            "win32api": "pywin32",
            "comtypes": "comtypes",
        })

    missing_reqs = []
    ddgs_ok = import_checker("ddgs") or import_checker("duckduckgo_search")
    if not ddgs_ok:
        missing_reqs.append("ddgs")

    for mod, pkg in req_mapping.items():
        if not import_checker(mod):
            missing_reqs.append(pkg)

    if system == "Darwin":
        for command in ("osascript", "pbcopy"):
            if not command_checker(command):
                missing_reqs.append(command)

    ok = len(missing_reqs) == 0
    value = "所有依赖已安装" if ok else f"缺少依赖: {', '.join(missing_reqs)}"
    return {"ok": ok, "value": value, "missing": missing_reqs}


def _platform_wechat_report(system_name=None):
    """Return a platform-aware WeChat process status."""
    import os as _os
    import platform
    import subprocess

    system = system_name or platform.system()
    if system == "Darwin":
        app_name = _os.getenv("MAC_WECHAT_APP_NAME", "WeChat")
        try:
            result = subprocess.run(
                ["pgrep", "-x", app_name],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                pid = result.stdout.strip().splitlines()[0]
                return {"ok": True, "value": f"微信运行中 (PID {pid})", "error": None}
            return {"ok": False, "value": "微信未运行", "error": "请启动 macOS 微信并授权辅助功能权限"}
        except Exception as e:
            return {"ok": False, "value": f"微信检测出错: {e}", "error": str(e)}

    try:
        from src.wechat.native.injector import _find_wechat_pid
        wx_pid, wx_name = _find_wechat_pid()
        wx_ok = wx_pid is not None
        wx_val = f"微信运行中 (PID {wx_pid})" if wx_ok else "微信未运行"
        return {"ok": wx_ok, "value": wx_val, "error": None if wx_ok else "请登录微信电脑端"}
    except Exception as e:
        return {"ok": False, "value": f"微信检测出错: {e}", "error": str(e)}


def _macos_wechat_diagnostics(system_name=None, automation=None):
    """Run macOS WeChat permission diagnostics from this process identity."""
    import platform

    system = system_name or platform.system()
    if system != "Darwin":
        return {
            "ok": False,
            "skipped": True,
            "error": "macOS diagnostics are only available on Darwin",
        }

    try:
        if automation is None:
            from src.wechat.mac_ui_backend import MacUIAutomation

            automation = MacUIAutomation()
        return automation.diagnose_access()
    except Exception as exc:
        logger.exception("macOS WeChat diagnostics failed")
        return {
            "ok": False,
            "skipped": False,
            "error": str(exc),
        }


# ── Thread-safe server state classes ────────────────────────────────────


class _ServerStatus:
    """Bot status with WebSocket broadcast.

    Write operations (update) are serialized through an internal lock.
    Read operations (snapshot) are lock-free — in CPython, reads of
    bool/int/str/float attributes are atomic under the GIL, so a
    snapshot may observe a mix of old and new values during a concurrent
    update, but will never crash or see garbage.  This is acceptable
    for a dashboard that refreshes every 30 seconds.
    """

    _FIELDS = (
        "running", "uptime_sec", "messages_processed",
        "wechat_backend", "db_ok",
        "wechat_online", "ai_ok", "ai_verified", "model_name", "group_count",
        "last_api_call_sec_ago", "last_api_call_time",
        "timestamp", "error", "avatar_url", "wx_name",
        "restricted_features_enabled",
    )

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.uptime_sec = 0
        self.messages_processed = 0
        self.wechat_backend = ""
        self.db_ok = False
        self.wechat_online = False
        self.ai_ok = False
        self.ai_verified = False
        self.model_name = ""
        self.group_count = 0
        self.last_api_call_sec_ago = -1
        self.last_api_call_time = 0.0
        self.timestamp = ""
        self.error = ""
        self.avatar_url = ""
        self.wx_name = ""
        self.restricted_features_enabled = False
        self._clients: list = []
        self._clients_lock = threading.Lock()

    def update(self, **kwargs):
        """Update status fields and broadcast to all WebSocket clients."""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)
            # Merge ai_ok: verified via config detection OR successful AI call OR bot reports ok
            if 'ai_verified' in kwargs or 'last_api_call_time' in kwargs or 'ai_ok' in kwargs:
                self.ai_ok = self.ai_verified or (self.last_api_call_time > 0) or kwargs.get('ai_ok', False)
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        # Snapshot lock-free: all fields are atomic types under GIL
        self._broadcast(self.snapshot())

    def snapshot(self):
        """Return a dict snapshot (lock-free, GIL-safe for atomic types)."""
        return {k: getattr(self, k) for k in self._FIELDS}

    def add_client(self, sock):
        with self._clients_lock:
            self._clients.append(sock)

    def remove_client(self, sock):
        with self._clients_lock:
            if sock in self._clients:
                self._clients.remove(sock)

    def _broadcast(self, snapshot):
        """Push snapshot to all connected WebSocket clients.

        Takes a snapshot of the client list under lock, then sends to each
        client *without* holding the lock — avoids blocking other handlers
        during slow network sends.
        """
        payload = json.dumps(snapshot, ensure_ascii=False)
        with self._clients_lock:
            clients = list(self._clients)
        dead = []
        for sock in clients:
            try:
                _send_ws_frame(sock, payload)
            except Exception:
                dead.append(sock)
        if dead:
            with self._clients_lock:
                for s in dead:
                    if s in self._clients:
                        self._clients.remove(s)


class _BotControl:
    """Bot lifecycle control.

    Write operations are serialized through an internal lock.
    is_running() is lock-free — reading a bool is atomic under CPython GIL.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.thread = None
        self.backend = None
        self.running = False

    def register(self, thread=None, backend=None):
        with self._lock:
            if thread is not None:
                self.thread = thread
            if backend is not None:
                self.backend = backend
            self.running = True

    def register_backend(self, backend):
        """Called by Bot.run() during initialization."""
        with self._lock:
            self.backend = backend

    def stop(self):
        """Stop the bot backend and wait for the thread to exit."""
        # Read refs under lock, then call stop + join outside the lock
        # to avoid deadlock if stop() needs the lock.
        with self._lock:
            backend = self.backend
            thread = self.thread

        if backend is not None and hasattr(backend, "stop"):
            backend.stop()

        if thread is not None and thread.is_alive():
            thread.join(timeout=30)

        with self._lock:
            self.running = False
            self.backend = None
            self.thread = None
        return backend is not None

    def is_running(self):
        """Lock-free: reading a bool is atomic under CPython GIL."""
        return self.running

    def set_running(self):
        with self._lock:
            self.running = True

    def mark_stopped(self):
        """Reset running state when the bot thread exits on its own.

        Does NOT stop the backend or join the thread — use stop() for
        external shutdown requests.  This is called from within the bot
        thread's ``finally`` block so the next /api/start can proceed.
        """
        with self._lock:
            self.running = False
            self.backend = None
            self.thread = None

    def set_thread(self, thread):
        with self._lock:
            self.thread = thread


class _ServerStartGuard:
    """Thread-safe idempotent server start guard."""

    def __init__(self):
        self._lock = threading.Lock()
        self._started = False

    def try_start(self):
        """Return True if server should start, False if already started."""
        with self._lock:
            if self._started:
                return False
            self._started = True
            return True


# ── Module-level instances ────────────────────────────────────────────

_status = _ServerStatus()
_bot_control = _BotControl()
_server_guard = _ServerStartGuard()
_shutdown_event = threading.Event()
_image_proxy_cache: dict[str, tuple[bytes, str]] = {}  # MD5(url) → (decrypted_data, content_type)
_image_proxy_lock = threading.Lock()  # protects _image_proxy_cache across handler threads
_assistant_scheduler = None  # DigestScheduler — registered by bot.py for hot-reload
_assistant_alert = None  # AlertEngine — registered by bot.py for hot-reload
_oa_monitor = None  # OAMonitorEngine — registered by bot.py for hot-reload


def signal_shutdown():
    """Signal all components to stop (called on app exit)."""
    _shutdown_event.set()


def register_assistant_scheduler(scheduler):
    """Register the DigestScheduler so the API can hot-reload its config."""
    global _assistant_scheduler
    _assistant_scheduler = scheduler


def register_assistant_alert(alert_engine):
    """Register the AlertEngine so the API can hot-reload its config."""
    global _assistant_alert
    _assistant_alert = alert_engine


def register_oa_monitor(monitor):
    """Register the OAMonitorEngine so the API can hot-reload its config."""
    global _oa_monitor
    _oa_monitor = monitor


def is_shutting_down():
    """Check if shutdown has been signaled."""
    return _shutdown_event.is_set()

# ── Onboarding state ──────────────────────────────────────────────────

_onboarding_data = {
    "step1_done": False, "step2_done": False, "step3_done": False, "step4_done": False,
    "key": "", "wxid": "", "db_path": "",
    "wechat_backend": "wcdb",
    "ai_provider_base_url": "", "ai_provider_api_key": "", "ai_provider_type": "auto", "ai_provider_model": "",
    "memory_consolidation_enabled": False,
}
_onboarding_lock = threading.Lock()

# Async step1 state
_step1_state = {
    "running": False,
    "phase": "idle",   # idle | waiting_exit | waiting_login | hooking | done | error
    "message": "",
    "result": None,    # {"key": ..., "wxid": ..., "db_path": ...}
}
_step1_thread = None
_step1_lock = threading.Lock()


# ── Public API wrappers (delegate to thread-safe classes) ─────────────


def update_status(**kwargs):
    """Push status update to all WebSocket clients (thread-safe)."""
    _status.update(**kwargs)


def register_bot(thread=None, backend=None):
    """Register bot thread/backend so the web API can control it."""
    _bot_control.register(thread=thread, backend=backend)
    update_status(running=True)


def _bot_exited():
    """Notify that the bot thread has exited (any path — normal/error).

    Resets the control lock so the next /api/start can proceed.
    Called from desktop.py's start_bot() and _start_bot_in_thread().
    """
    _bot_control.mark_stopped()


def _register_backend(backend):
    """Register backend from Bot.run() — explicit API, no monkey-patching."""
    _bot_control.register_backend(backend)


def _stop_bot():
    """Stop the running bot backend. Returns True if anything was stopped."""
    stopped = _bot_control.stop()
    # Clear WCDB client cache — the backend's client is closed on shutdown,
    # and stale references cause DLL calls to fail (ret=-2) on restart.
    from src.web.api_handlers import reset_wcdb_client
    reset_wcdb_client()
    update_status(running=False)
    if stopped:
        logger.info("Bot stopped via web API")
    return stopped


def _start_bot_in_thread():
    """Start the bot in a new daemon thread. Call from API handler."""
    if _bot_control.is_running():
        return {"ok": False, "error": "Bot is already running"}

    import sys
    from src.config import PROJECT_ROOT

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    def _run():
        try:
            from src.config import load_config
            config = load_config()
            update_status(
                wechat_backend=config.wechat_backend,
                model_name=config.ai_provider_model or "",
                error="",
            )
            from src.bot import Bot
            bot = Bot(config)
            # Bot.run() calls _register_backend() during init — no patch needed
            bot.run()
        except SystemExit:
            update_status(running=False)
        except Exception as e:
            update_status(running=False, error=str(e))
            logger.exception("Bot crashed during startup")
        finally:
            # Always clear the running flag so the user can restart
            # (bot.run() exits gracefully on errors like KEY_MISSING)
            _bot_control.mark_stopped()

    thread = threading.Thread(target=_run, daemon=True, name="bot-main")
    thread.start()
    _bot_control.set_thread(thread)
    _bot_control.set_running()
    update_status(running=True)

    # Auto-detect AI connectivity on startup (background, non-blocking)
    def _auto_ai_check():
        time.sleep(6)  # Wait for bot to initialize
        try:
            from src.config import load_config
            cfg = load_config()
            base_url = cfg.ai_provider_base_url
            api_key = cfg.ai_provider_api_key
            if base_url and api_key:
                from src.summarize.provider_detector import detect_provider
                info = detect_provider(base_url, api_key)
                update_status(ai_verified=(info.error == ""))
                logger.info("Auto AI check: %s", "ok" if info.error == "" else f"failed: {info.error}")
            else:
                logger.info("Auto AI check: skipped (no URL/Key)")
        except Exception as e:
            logger.warning("Auto AI check failed: %s", e)

    threading.Thread(target=_auto_ai_check, daemon=True, name="ai-auto-check").start()

    return {"ok": True}


def _recv_exactly(sock, n):
    """Receive exactly n bytes from a socket (handles TCP fragmentation)."""
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _send_ws_frame(sock, text):
    """Send a WebSocket text frame."""
    data = text.encode("utf-8")
    frame = bytearray()
    frame.append(0x81)  # FIN + text opcode
    if len(data) < 126:
        frame.append(len(data))
    elif len(data) < 65536:
        frame.append(126)
        frame.extend(struct.pack(">H", len(data)))
    else:
        frame.append(127)
        frame.extend(struct.pack(">Q", len(data)))
    frame.extend(data)
    sock.sendall(bytes(frame))


def _read_ws_frame(sock):
    """Read a WebSocket frame (handles TCP fragmentation)."""
    header = _recv_exactly(sock, 2)
    if header is None:
        return None
    opcode = header[0] & 0x0F
    if opcode == 0x8:  # close
        return None
    if opcode == 0x9:  # ping
        # Send pong
        pong = bytearray([0x8A, 0x00])  # FIN + pong opcode, no payload
        sock.sendall(bytes(pong))
        return b""  # return empty to keep reading
    length = header[1] & 0x7F
    if length == 126:
        ext = _recv_exactly(sock, 2)
        if ext is None:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exactly(sock, 8)
        if ext is None:
            return None
        length = struct.unpack(">Q", ext)[0]
    mask = _recv_exactly(sock, 4)
    if mask is None:
        return None
    payload = _recv_exactly(sock, length)
    if payload is None:
        return None
    payload = bytearray(payload)
    for i in range(len(payload)):
        payload[i] ^= mask[i % 4]
    return bytes(payload)


def _handle_ws_upgrade(headers, conn):
    """Perform WebSocket handshake using already-parsed headers.

    Uses the ``http.client.HTTPMessage`` object directly — avoids re-parsing
    raw bytes, which broke on Python 3.13 where ``headers.as_bytes()`` no
    longer round-trips faithfully.
    """
    key = headers.get("Sec-WebSocket-Key", "")
    if not key:
        logger.warning("WS upgrade rejected: missing Sec-WebSocket-Key")
        return False

    accept = b64encode(sha1((key + WEBSOCKET_GUID.decode()).encode()).digest()).decode()

    conn.sendall(
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
    )
    logger.info("WS upgrade accepted")
    return True


class _UIHandler(SimpleHTTPRequestHandler):
    """HTTP handler: static files + WebSocket upgrade + API."""

    MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB — prevents OOM from malicious Content-Length

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_PUT(self):
        put_path = self.path.split("?")[0] if "?" in self.path else self.path
        if self.path == "/api/assistant/config" or (
            put_path.startswith("/api/oa/groups/") and len(put_path.split("/")) == 5
        ):
            self.do_GET()
        else:
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Method not allowed"}).encode())

    def do_DELETE(self):
        """Handle DELETE requests for OA groups and similar REST endpoints."""
        delete_path = self.path.split("?")[0] if "?" in self.path else self.path

        if (delete_path.startswith("/api/oa/groups/") and len(delete_path.split("/")) == 5):
            self.do_GET()
        else:
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Method not allowed"}).encode())

    def do_POST(self):
        # Only delegate specific API paths; return 405 for unknown POST paths
        # Extract path without query params
        post_path = self.path.split("?")[0] if "?" in self.path else self.path

        if post_path in ("/api/config", "/api/config/import", "/api/start", "/api/stop",
                         "/api/nicknames",
                         "/api/onboarding/reset",
                         "/api/onboarding/step1", "/api/onboarding/step2",
                         "/api/onboarding/step3", "/api/onboarding/step4",
                         "/api/sandbox/test",
                         "/api/assistant/config",
                         "/api/assistant/digest/run",
                         "/api/assistant/ai/detect",
                         "/api/assistant/notifications/test",
                         "/api/assistant/notifications/pending",
                         "/api/fav/export",
                         "/api/sns/protect/install",
                         "/api/sns/protect/uninstall",
                         "/api/chat/anti-revoke/install",
                         "/api/chat/anti-revoke/uninstall",
                         "/api/sns/export",
                         "/api/chat/export",
                         "/api/export/open-folder",
                         "/api/ai/chat/start",
                         "/api/ai/chat/message",
                         "/api/ai/chat/compress",
                         "/api/ai/chat/destroy",
                         "/api/sns/ai/summarize",
                         "/api/oa/groups/create",
                         "/api/oa/digest/run",
                         "/api/ilink/bind",
                         "/api/ilink/unbind",
                         "/api/ilink/test-push",
                         "/api/wechat-data-dir/detect") or (
                             self.path.startswith("/api/assistant/notifications/")
                             and (self.path.endswith("/ack") or self.path.endswith("/ignore"))
                         ) or (
                             self.path.startswith("/api/oa/groups/") and len(self.path.split("/")) == 5
                         ) or (
                             self.path.startswith("/api/oa/digest/run/")
                         ) or (
                             post_path == "/api/scheduler/tasks"
                         ) or (
                             self.path.startswith("/api/scheduler/tasks/") and len(self.path.split("/")) == 5
                         ):
            self.do_GET()
        else:
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Method not allowed"}).encode())

    def do_GET(self):
        self._handle_request()

    def _handle_request(self):
        req_t0 = time.monotonic()
        if self.path.startswith("/api/chat/") or self.path.startswith("/api/fav/"):
            logger.info("[REQ-TRACE] start %s %s thread=%s", self.command, self.path, threading.current_thread().name)
        # ── WebSocket upgrade ─────────────────────────────────────────
        if self.path == "/ws":
            connection_header = self.headers.get("Connection", "").lower()
            upgrade_header = self.headers.get("Upgrade", "").lower()
            if "upgrade" in connection_header and upgrade_header == "websocket":
                if _handle_ws_upgrade(self.headers, self.request):
                    _status.add_client(self.request)
                    # Send initial status
                    try:
                        _send_ws_frame(
                            self.request,
                            json.dumps(_status.snapshot(), ensure_ascii=False),
                        )
                    except Exception:
                        _status.remove_client(self.request)
                        return
                    # Read loop (ping/pong handled in _read_ws_frame)
                    while True:
                        try:
                            frame = _read_ws_frame(self.request)
                            if frame is None:
                                break
                        except Exception:
                            break
                    _status.remove_client(self.request)
                    return
                else:
                    self.send_response(400)
                    self.end_headers()
                    return

        # ── API: Start bot ────────────────────────────────────────────
        if self.path == "/api/start":
            if _bot_control.is_running():
                self.send_json({"ok": True, "already_running": True})
            else:
                result = _start_bot_in_thread()
                self.send_json(result)
            return

        # ── API: Stop bot ─────────────────────────────────────────────
        if self.path == "/api/stop":
            _stop_bot()
            self.send_json({"ok": True})
            return

        # ── API: Load config ───────────────────────────────────────────
        if self.path == "/api/load-config":
            env_path = _find_or_create_env()
            raw = {}
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        raw[k.strip()] = v.strip()
            self.send_json({
                "ok": True,
                "config": {
                    "ai_provider_base_url": raw.get("AI_PROVIDER_BASE_URL", ""),
                    "ai_provider_api_key": raw.get("AI_PROVIDER_API_KEY", ""),
                    "ai_provider_type": raw.get("AI_PROVIDER_TYPE", "auto"),
                    "ai_provider_model": raw.get("AI_PROVIDER_MODEL", ""),
                    "ai_provider_extra_body": raw.get("AI_PROVIDER_EXTRA_BODY", ""),
                    "wechat_backend": raw.get("WECHAT_BACKEND", "wcdb"),
                    "memory_consolidation_enabled": raw.get("MEMORY_CONSOLIDATION_ENABLED", "false").lower() == "true",
                    "trigger_keywords": [
                        kw.strip() for kw in raw.get("TRIGGER_KEYWORDS", "").split(",")
                        if kw.strip()
                    ],
                    "log_level": raw.get("LOG_LEVEL", "INFO"),
                    "wechat_data_dir": raw.get("WECHAT_DATA_DIR", ""),
                },
                "detected_data_dir": _detect_default_data_dir(),
            })
            return

        # ── API: Export config ───────────────────────────────────────
        if self.path == "/api/config/export":
            from datetime import date as _dt_date
            try:
                env_path = _find_or_create_env()
                raw = {}
                if env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            raw[k.strip()] = v.strip()
                export_data = {
                    "ai_provider_base_url": raw.get("AI_PROVIDER_BASE_URL", ""),
                    "ai_provider_api_key": raw.get("AI_PROVIDER_API_KEY", ""),
                    "ai_provider_type": raw.get("AI_PROVIDER_TYPE", "auto"),
                    "ai_provider_model": raw.get("AI_PROVIDER_MODEL", ""),
                    "wechat_backend": raw.get("WECHAT_BACKEND", "wcdb"),
                    "memory_consolidation_enabled": raw.get("MEMORY_CONSOLIDATION_ENABLED", "false").lower() == "true",
                    "trigger_keywords": [
                        kw.strip() for kw in raw.get("TRIGGER_KEYWORDS", "").split(",")
                        if kw.strip()
                    ],
                    "log_level": raw.get("LOG_LEVEL", "INFO"),
                    "wechat_data_dir": raw.get("WECHAT_DATA_DIR", ""),
                }
                filename = f"wx-assist-config-{_dt_date.today().isoformat()}.json"
                body = json.dumps(export_data, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                logger.exception("Failed to export config")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Save config ──────────────────────────────────────────
        if self.path == "/api/config":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                config = json.loads(body)
                env_path = _find_or_create_env()
                if env_path.exists():
                    lines = env_path.read_text(encoding="utf-8").splitlines()
                    new_lines = []
                    updates = {
                        "AI_PROVIDER_BASE_URL": config.get("ai_provider_base_url"),
                        "AI_PROVIDER_API_KEY": config.get("ai_provider_api_key"),
                        "AI_PROVIDER_TYPE": config.get("ai_provider_type"),
                        "AI_PROVIDER_MODEL": config.get("ai_provider_model"),
                        "AI_PROVIDER_EXTRA_BODY": config.get("ai_provider_extra_body"),
                        "WECHAT_BACKEND": config.get("wechat_backend"),
                        "MEMORY_CONSOLIDATION_ENABLED": str(config.get("memory_consolidation_enabled", False)).lower(),
                        "TRIGGER_KEYWORDS": ",".join(config.get("trigger_keywords", [])) if config.get("trigger_keywords") else None,
                        "LOG_LEVEL": config.get("log_level"),
                        "WECHAT_DATA_DIR": config.get("wechat_data_dir"),
                    }
                    seen = set()
                    for line in lines:
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#") and "=" in stripped:
                            key = stripped.split("=", 1)[0].strip()
                            if key in updates and updates[key] is not None:
                                new_lines.append(f"{key}={updates[key]}")
                                seen.add(key)
                                continue
                        new_lines.append(line)
                    for key, val in updates.items():
                        if key not in seen and val is not None:
                            new_lines.append(f"{key}={val}")
                    # Atomic write: temp file then os.replace
                    tmp_path = env_path.with_suffix(".tmp")
                    tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                    os.replace(tmp_path, env_path)
                    for key, val in updates.items():
                        if val is not None:
                            os.environ[key] = str(val)
                    self.send_json({
                        "ok": True,
                        "saved": list(seen),
                        "requires_restart": True,
                    })
            except Exception as e:
                logger.exception("Failed to save config")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Import config ─────────────────────────────────────────
        if self.path == "/api/config/import":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                config = json.loads(body)
                # Basic validation: must look like a wx-assist config export
                expected_keys = ['ai_provider_base_url', 'wechat_backend']
                has_keys = any(k in config for k in expected_keys)
                if not has_keys:
                    raise ValueError("无效的配置文件格式：缺少必需字段")
                env_path = _find_or_create_env()
                if env_path.exists():
                    lines = env_path.read_text(encoding="utf-8").splitlines()
                else:
                    lines = []
                updates = {
                    "AI_PROVIDER_BASE_URL": config.get("ai_provider_base_url"),
                    "AI_PROVIDER_API_KEY": config.get("ai_provider_api_key"),
                    "AI_PROVIDER_TYPE": config.get("ai_provider_type"),
                    "AI_PROVIDER_MODEL": config.get("ai_provider_model"),
                    "WECHAT_BACKEND": config.get("wechat_backend"),
                    "TRIGGER_KEYWORDS": ",".join(config.get("trigger_keywords", [])) if config.get("trigger_keywords") else None,
                    "LOG_LEVEL": config.get("log_level"),
                    "WECHAT_DATA_DIR": config.get("wechat_data_dir"),
                }
                new_lines = []
                seen = set()
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "=" in stripped:
                        key = stripped.split("=", 1)[0].strip()
                        if key in updates and updates[key] is not None:
                            new_lines.append(f"{key}={updates[key]}")
                            seen.add(key)
                            continue
                    new_lines.append(line)
                for key, val in updates.items():
                    if key not in seen and val is not None:
                        new_lines.append(f"{key}={val}")
                # Atomic write
                tmp_path = env_path.with_suffix(".tmp")
                tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                os.replace(tmp_path, env_path)
                # Update in-process environment
                for key, val in updates.items():
                    if val is not None:
                        os.environ[key] = str(val)
                self.send_json({
                    "ok": True,
                    "imported": list(seen),
                    "requires_restart": True,
                })
            except ValueError as e:
                self.send_json({"ok": False, "error": str(e)})
            except Exception as e:
                logger.exception("Failed to import config")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Get nickname groups ─────────────────────────────────────
        if self.path == "/api/nicknames/groups":
            try:
                from src.config import find_env_file, _decode_wechat_groups
                env_path = find_env_file()
                import sqlite3

                # Resolve group names same way as wcdb_backend: read env, match sessions
                groups_raw = "*"
                if env_path and env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("WECHAT_GROUPS="):
                            groups_raw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                            break
                groups_raw = _decode_wechat_groups(groups_raw)

                db_path = "data/messages.db"
                if env_path and env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("DB_PATH="):
                            db_path = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                            break

                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row

                # ── Load persisted chat_id -> group display names ────────
                # Written by WcdbBackend._save_group_names() when the bot
                # resolves group names from WeChat's session DB via WCDB DLL.
                group_names_path = Path("data/group_names.json")
                group_names: dict[str, str] = {}
                if group_names_path.exists():
                    try:
                        group_names = json.loads(
                            group_names_path.read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, OSError):
                        pass
                # ──────────────────────────────────────────────────────────

                groups = []
                if not _messages_table_exists(conn):
                    conn.close()
                    self.send_json({"ok": True, "groups": groups})
                    return
                if groups_raw == "*" or not groups_raw:
                    # All groups: use one GROUP BY query instead of N per-group COUNT queries.
                    # 性能优化原因：群助手首屏不需要精确实时成员数；这里统计的是
                    # messages.db 历史消息中的 sender_id 去重数，不调用 WCDB DLL，避免慢查询。
                    rows = conn.execute(
                        """
                        SELECT chat_id, COUNT(DISTINCT sender_id) as cnt
                        FROM messages
                        WHERE chat_id LIKE '%@chatroom%'
                        GROUP BY chat_id
                        ORDER BY chat_id
                        """
                    ).fetchall()
                    for row in rows:
                        chat_id = row["chat_id"]
                        groups.append({
                            "chat_id": chat_id,
                            "group_name": group_names.get(chat_id, chat_id),
                            "member_count": row["cnt"] if row else 0,
                        })
                else:
                    # Specific group names — match against known chat_ids
                    wanted = [g.strip() for g in groups_raw.split(",") if g.strip()]
                    # Get all chatroom IDs from messages with member counts in one query
                    all_chats = conn.execute(
                        """
                        SELECT chat_id, COUNT(DISTINCT sender_id) as cnt
                        FROM messages
                        WHERE chat_id LIKE '%@chatroom%'
                        GROUP BY chat_id
                        """
                    ).fetchall()
                    all_ids = {r["chat_id"]: r["cnt"] for r in all_chats}
                    for name in wanted:
                        # Try exact match first, then substring
                        chat_id = name
                        for cid in all_ids:
                            if name.lower() in cid.lower():
                                chat_id = cid
                                break
                        # Resolve display name from persisted mapping, fallback to configured name
                        display_name = group_names.get(chat_id) or name
                        groups.append({
                            "chat_id": chat_id,
                            "group_name": display_name,
                            "member_count": all_ids.get(chat_id, 0),
                        })

                conn.close()
                self.send_json({"ok": True, "groups": groups})
            except Exception as e:
                logger.exception("Failed to list nickname groups")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Get nicknames for a group ────────────────────────────────
        if self.path.startswith("/api/nicknames") and self.command == "GET":
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if not parsed.path.startswith("/api/nicknames") or parsed.path != "/api/nicknames":
                self.send_json({"ok": False, "error": "not found"})
                return
            try:
                chat_id = params.get("chat_id", [""])[0]
                if not chat_id:
                    self.send_json({"ok": False, "error": "missing chat_id"})
                    return

                from src.nickname import NicknameService
                nicks = NicknameService()
                overrides = nicks.load()

                import sqlite3
                from src.config import load_config
                config = load_config()
                conn = sqlite3.connect(config.db_path)
                try:
                    conn.row_factory = sqlite3.Row
                    if not _messages_table_exists(conn):
                        self.send_json({"ok": True, "members": []})
                        return
                    rows = conn.execute(
                        "SELECT DISTINCT sender_id, sender_name FROM messages WHERE chat_id=? ORDER BY sender_name",
                        (chat_id,),
                    ).fetchall()
                finally:
                    conn.close()

                members = []
                for row in rows:
                    wxid = row["sender_id"]
                    display = row["sender_name"] or wxid
                    members.append({
                        "wxid": wxid,
                        "display_name": display,
                        "nickname": overrides.get(wxid, ""),
                    })

                self.send_json({"ok": True, "members": members})
            except Exception as e:
                logger.exception("Failed to get nicknames")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Save nickname ────────────────────────────────────────────
        if self.path == "/api/nicknames" and self.command != "GET":
            try:
                content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
                body = self.rfile.read(content_len) if content_len else b"{}"
                data = json.loads(body)
                wxid = (data.get("wxid") or "").strip()
                nickname = (data.get("nickname") or "").strip()

                if not wxid:
                    self.send_json({"ok": False, "error": "missing wxid"})
                    return

                from src.nickname import NicknameService
                nicks = NicknameService()
                if nickname:
                    nicks.update(wxid, nickname)
                else:
                    nicks.remove(wxid)

                self.send_json({"ok": True})
            except Exception as e:
                logger.exception("Failed to save nickname")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Test AI prompt sandbox ──────────────────────────────
        if self.path == "/api/sandbox/test":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                body_text = body.decode("utf-8") if isinstance(body, bytes) else body
            except UnicodeDecodeError:
                body_text = body.decode("latin-1") if isinstance(body, bytes) else "{}"
            try:
                data = json.loads(body_text)
                message = data.get("message", "").strip()
                sender_name = data.get("sender_name", "张三").strip()
                group_name = data.get("group_name", "技术交流群").strip()
                group_memory = data.get("group_memory", "").strip()
                context_messages = data.get("context_messages", [])

                # Load config — prime os.environ from .env so load_config()
                # picks up the latest saved values (the server may have started
                # without load_dotenv, so os.environ can be stale).
                from src.config import find_env_file, load_config
                from src.summarize import create_summarizer

                env_path = find_env_file()
                if env_path and env_path.exists():
                    try:
                        for line in env_path.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip()
                                if v and k not in os.environ:
                                    os.environ[k] = v
                    except UnicodeDecodeError:
                        # Binary keys in .env — skip, env is already loaded
                        pass

                config = load_config()

                # Allow frontend sandbox inputs to override saved config
                if data.get("ai_provider_base_url"):
                    config.ai_provider_base_url = data["ai_provider_base_url"]
                if data.get("ai_provider_api_key"):
                    config.ai_provider_api_key = data["ai_provider_api_key"]
                if data.get("ai_provider_type"):
                    config.ai_provider_type = data["ai_provider_type"]
                if data.get("ai_provider_model"):
                    config.ai_provider_model = data["ai_provider_model"]
                if data.get("ai_provider_extra_body") is not None:
                    config.ai_provider_extra_body = data["ai_provider_extra_body"]

                # Create summarizer
                summarizer = create_summarizer(config)

                # Call chat
                reply = summarizer.chat(
                    message=message,
                    context_messages=context_messages,
                    requester_name=sender_name,
                    bot_name= "群聊小助手",
                    group_name=group_name,
                    group_memory=group_memory,
                )

                self.send_json({
                    "ok": True,
                    "reply": reply,
                })
            except Exception as e:
                logger.exception("Failed to run sandbox test")
                update_status(ai_ok=False, ai_verified=False)
                self.send_json({
                    "ok": False,
                    "error": str(e),
                })
            return

        # ── API: Get status ───────────────────────────────────────────
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(_status.snapshot(), ensure_ascii=False).encode())
            return

        # ── API: Get logs ────────────────────────────────────────────
        if self.path == "/api/logs":
            self.send_json(_read_recent_logs())
            return

        # ── API: Get LLM logs ──────────────────────────────────────────
        if self.path == "/api/llm-logs":
            self.send_json(_read_recent_llm_logs())
            return

        # ── API: macOS WeChat automation diagnostics ─────────────────
        if self.path == "/api/macos/diagnose":
            self.send_json({
                "ok": True,
                "diagnostics": _macos_wechat_diagnostics(),
            })
            return

        # ── API: Onboarding status ────────────────────────────────────
        if self.path == "/api/onboarding/status":
            from src.config import is_onboarding_done
            done = is_onboarding_done()
            with _onboarding_lock:
                steps = {
                    "step1": _onboarding_data["step1_done"],
                    "step2": _onboarding_data["step2_done"],
                    "step3": _onboarding_data["step3_done"],
                    "step4": _onboarding_data["step4_done"],
                }
            self.send_json({"ok": True, "onboarding_done": done, "steps": steps})
            return

        # ── API: Onboarding diagnostics check ─────────────────────────
        if self.path == "/api/onboarding/diagnose":
            import sys

            # 1. Python check
            python_ok = sys.version_info >= (3, 10)
            python_val = f"Python {sys.version.split()[0]}"

            # 2. Requirements check
            req_report = _platform_dependency_report()

            # 3. WeChat PID check
            wx_report = _platform_wechat_report()

            # 4. .env check
            # In frozen mode, __file__ is inside the read-only _MEIPASS
            # extraction directory. Use PROJECT_ROOT from config.py which
            # correctly resolves to the EXE directory when frozen.
            from src.config import PROJECT_ROOT, find_env_file
            project_root = PROJECT_ROOT
            env_path = find_env_file() or (project_root / ".env")
            env_ok = env_path.exists()
            env_val = "配置文件已存在" if env_ok else "配置文件尚未创建"

            # 5. DB permissions check
            data_dir = project_root / "data"
            db_perm_ok = True
            db_perm_err = None
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                test_file = data_dir / ".write_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
            except Exception as e:
                db_perm_ok = False
                db_perm_err = str(e)

            # Check read permission to WeChat db path if it's set/detected
            db_path = None
            with _onboarding_lock:
                db_path = _onboarding_data.get("db_path")
            if not db_path:
                _, detected_db = _detect_wxid_and_db_path()
                if detected_db:
                    db_path = detected_db

            if db_path:
                db_path_obj = Path(db_path)
                if db_path_obj.exists():
                    try:
                        with open(db_path_obj, "rb") as f:
                            f.read(100)
                    except Exception as e:
                        db_perm_ok = False
                        db_perm_err = f"微信数据库读取失败: {e}"

            db_perm_val = "数据库读写权限正常" if db_perm_ok else f"数据库权限错误: {db_perm_err}"

            # Build labelled diagnostics with detail on failure
            diag = {
                "python": {"ok": python_ok, "value": python_val, "error": None,
                           "label": "Python 版本", "detail": python_val if not python_ok else None},
                "requirements": {**req_report, "label": "依赖包",
                                 "detail": "缺少: " + ", ".join(req_report.get("missing", [])) if not req_report.get("ok") else None},
                "wechat": {**wx_report, "label": "微信进程",
                           "detail": wx_report.get("error") if not wx_report.get("ok") else None},
                "env": {"ok": env_ok, "value": env_val, "error": None,
                        "label": "配置文件", "detail": env_val if not env_ok else None},
                "db": {"ok": db_perm_ok, "value": db_perm_val, "error": db_perm_err,
                       "label": "数据库权限", "detail": db_perm_err},
            }
            self.send_json({"ok": True, "diagnostics": diag})
            return

        # ── API: Onboarding step 1 - start extraction (async) ─────────
        if self.path == "/api/onboarding/step1":
            with _step1_lock:
                if _step1_state["running"]:
                    self.send_json({"ok": False, "phase": "busy", "message": "正在提取中..."})
                    return
                _step1_state["running"] = True
                _step1_state["phase"] = "idle"
                _step1_state["message"] = ""
                _step1_state["result"] = None

            # Start background thread
            t = threading.Thread(target=_run_step1_extraction, daemon=True)
            t.start()
            with _step1_lock:
                global _step1_thread
                _step1_thread = t

            self.send_json({"ok": True, "phase": "started", "message": "提取已启动"})
            return

        # ── API: Onboarding step 1 - poll status ──────────────────────
        if self.path == "/api/onboarding/step1-status":
            with _step1_lock:
                s = dict(_step1_state)
            self.send_json(s)
            return

        # ── API: Onboarding step 2 - WeChat identity ──────────────────
        if self.path == "/api/onboarding/step2":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(body)
                with _onboarding_lock:
                    _onboarding_data["step2_done"] = True
                    _onboarding_data["wechat_backend"] = data.get("wechat_backend", "wcdb")
                    if data.get("wxid"):
                        _onboarding_data["wxid"] = data["wxid"]
                    if data.get("db_path"):
                        _onboarding_data["db_path"] = data["db_path"]
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Onboarding step 3 - AI backend ───────────────────────
        if self.path == "/api/onboarding/step3":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(body)
                with _onboarding_lock:
                    _onboarding_data["step3_done"] = True
                    _onboarding_data["ai_provider_base_url"] = data.get("ai_provider_base_url", "")
                    _onboarding_data["ai_provider_api_key"] = data.get("ai_provider_api_key", "")
                    _onboarding_data["ai_provider_type"] = data.get("ai_provider_type", "auto")
                    _onboarding_data["ai_provider_model"] = data.get("ai_provider_model", "")
                self.send_json({"ok": True})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Onboarding step 4 - features + write .env ────────────
        if self.path == "/api/onboarding/step4":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(body)
                with _onboarding_lock:
                    _onboarding_data["step4_done"] = True

                # Write all accumulated data to .env
                env_path = _find_or_create_env()
                _write_onboarding_to_env(env_path)
                self.send_json({"ok": True})
            except Exception as e:
                logger.exception("Onboarding step4 failed")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Reset onboarding → allow re-extraction ─────────────
        if self.path == "/api/onboarding/reset":
            # 1. Reset file-based state
            env_path = _find_or_create_env()
            _set_env_key(env_path, "ONBOARDING_DONE", "false")
            _set_env_key(env_path, "WCDB_KEY", "")
            # 2. Reset in-memory state so a fresh extraction can start
            with _onboarding_lock:
                for k in _onboarding_data:
                    if isinstance(_onboarding_data[k], bool):
                        _onboarding_data[k] = False
                    elif isinstance(_onboarding_data[k], str):
                        _onboarding_data[k] = ""
            with _step1_lock:
                _step1_state["running"] = False
                _step1_state["phase"] = "idle"
                _step1_state["message"] = ""
                _step1_state["result"] = None
            self.send_json({"ok": True, "message": "请退出微信，然后点击「重新获取密钥」"})
            return

        # ── API: Browse filesystem directories ──────────────────────
        if self.path.startswith("/api/browse"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            dir_path = params.get("path", [""])[0].strip()
            if not dir_path:
                # No path given — list drives on Windows, home on others
                import platform
                if platform.system() == "Windows":
                    import string
                    drives = []
                    for letter in string.ascii_uppercase:
                        p = Path(f"{letter}:\\")
                        if p.exists():
                            drives.append({"name": f"{letter}:", "path": f"{letter}:\\", "is_dir": True})
                    self.send_json({"ok": True, "entries": drives, "current_path": ""})
                else:
                    home = Path.home()
                    entries = _list_dir_entries(home)
                    self.send_json({"ok": True, "entries": entries, "current_path": str(home)})
                return

            target = Path(dir_path)
            if not target.exists():
                self.send_json({"ok": False, "error": f"路径不存在: {dir_path}"})
                return
            if not target.is_dir():
                self.send_json({"ok": False, "error": "请选择一个目录"})
                return

            try:
                entries = _list_dir_entries(target)
                self.send_json({"ok": True, "entries": entries, "current_path": str(target)})
            except PermissionError:
                self.send_json({"ok": False, "error": "没有权限访问该目录"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Detect WeChat data in a custom directory ──────────
        if self.path == "/api/wechat-data-dir/detect":
            content_len = min(int(self.headers.get("Content-Length", 0)), self.MAX_BODY_SIZE)
            body = self.rfile.read(content_len) if content_len else b"{}"
            try:
                data = json.loads(body)
                dir_path = (data.get("path") or "").strip()
                if not dir_path:
                    self.send_json({"ok": False, "error": "请提供目录路径"})
                    return
                target = Path(dir_path)
                if not target.exists() or not target.is_dir():
                    self.send_json({"ok": False, "error": f"目录不存在: {dir_path}"})
                    return

                # Scan for wxid_* directories
                wxid_dirs = sorted(
                    [d for d in target.iterdir() if d.is_dir() and d.name.startswith("wxid_")],
                    key=lambda d: d.stat().st_mtime, reverse=True,
                )
                accounts = []
                for wxid_dir in wxid_dirs:
                    session_db = wxid_dir / "db_storage" / "session" / "session.db"
                    accounts.append({
                        "wxid": wxid_dir.name,
                        "has_session_db": session_db.exists(),
                        "db_path": str(session_db) if session_db.exists() else "",
                    })

                if accounts:
                    self.send_json({
                        "ok": True,
                        "found": True,
                        "accounts": accounts,
                        "message": f"找到 {len(accounts)} 个微信账号",
                    })
                else:
                    self.send_json({
                        "ok": True,
                        "found": False,
                        "accounts": [],
                        "message": f"在 {dir_path} 中未找到 wxid_* 目录。请确认路径正确。",
                    })
            except Exception as e:
                logger.exception("Failed to detect WeChat data dir")
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: Assistant — AI provider detection ─────────────────────
        if self.path == "/api/assistant/ai/detect":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
            except Exception:
                body = {}
            base_url = body.get("base_url", "")
            api_key = body.get("api_key", "")
            provider_type = body.get("provider_type", "openai")
            from src.summarize.provider_detector import detect_provider
            info = detect_provider(base_url, api_key, provider_type=provider_type)
            ok = info.error == ""
            update_status(ai_verified=ok)
            self.send_json({
                "ok": ok,
                "provider_type": info.provider_type,
                "available_models": info.available_models,
                "error": info.error,
            })
            return

        # ── API: Assistant — get config ─────────────────────────────────
        if self.path == "/api/assistant/config":
            if self.command == "PUT" or self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length)) if length > 0 else {}
                except Exception:
                    body = {}
                from src.assistant.config import _dict_to_config, save_assistant_config, load_assistant_config
                try:
                    existing = load_assistant_config()
                    # Merge: update fields from body
                    if "assistant_enabled" in body:
                        existing.assistant_enabled = bool(body["assistant_enabled"])
                    if "allow_wechat_send" in body:
                        existing.allow_wechat_send = bool(body["allow_wechat_send"])
                    if "alert_groups" in body:
                        existing.alert_groups = _dict_to_config({"alert_groups": body["alert_groups"]}).alert_groups
                    if "oa_monitor_groups" in body:
                        existing.oa_monitor_groups = _dict_to_config({"oa_monitor_groups": body["oa_monitor_groups"]}).oa_monitor_groups
                    if "digest_groups" in body:
                        existing.digest_groups = _dict_to_config({"digest_groups": body["digest_groups"]}).digest_groups
                    if "notify_channels" in body:
                        existing.notification_queue.enabled = any(
                            ch.get("enabled", True) for ch in body["notify_channels"]
                        )
                    if "notification_queue" in body:
                        q = body.get("notification_queue") or {}
                        if "enabled" in q:
                            existing.notification_queue.enabled = bool(q["enabled"])
                        if "retention_hours" in q:
                            existing.notification_queue.retention_hours = int(q["retention_hours"])
                    if "outbox_retention_hours" in body:
                        existing.notification_queue.retention_hours = int(body["outbox_retention_hours"])
                    save_assistant_config(existing)
                    # Hot-reload the running scheduler with the new config
                    if _assistant_scheduler is not None:
                        try:
                            _assistant_scheduler.update_config(existing)
                        except Exception as e:
                            logger.warning("Failed to hot-reload scheduler config: %s", e)
                    # Hot-reload the alert engine with the new config
                    if _assistant_alert is not None:
                        try:
                            _assistant_alert.update_config(existing)
                        except Exception as e:
                            logger.warning("Failed to hot-reload alert config: %s", e)
                    # Hot-reload the OA monitor with the new config
                    if _oa_monitor is not None:
                        try:
                            _oa_monitor.update_config(existing)
                        except Exception as e:
                            logger.warning("Failed to hot-reload OA monitor config: %s", e)
                    self.send_json({"ok": True})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)})
            else:
                from src.assistant.config import load_assistant_config, _config_to_dict
                cfg = load_assistant_config()
                self.send_json({"ok": True, "config": _config_to_dict(cfg)})
            return

        # ── API: Assistant — notifications list ─────────────────────────
        if self.path.startswith("/api/assistant/notifications?") or self.path == "/api/assistant/notifications":
            from urllib.parse import urlparse, parse_qs
            from src.assistant.outbox import Outbox
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["50"])[0] or 50)
            outbox = Outbox()
            notifications = outbox.list_notifications(
                chat_id=(qs.get("chat_id") or [""])[0],
                group_name=(qs.get("group_name") or [""])[0],
                notif_type=(qs.get("type") or [""])[0],
                status=(qs.get("status") or [""])[0],
                limit=limit,
            )
            self.send_json({"ok": True, "notifications": notifications})
            return

        # ── API: Assistant — pending notifications ──────────────────────
        if self.path == "/api/assistant/notifications/pending":
            from src.assistant.outbox import Outbox
            outbox = Outbox()
            pending = outbox.get_pending(limit=20)
            self.send_json({"ok": True, "notifications": pending})
            return

        # ── API: Assistant — test notification ──────────────────────────
        if self.path == "/api/assistant/notifications/test":
            from src.assistant.outbox import Outbox
            outbox = Outbox()
            nid = outbox.add(
                notif_type="keyword_alert",
                chat_id="",
                group_name="通知中心测试",
                title="测试通知",
                content="这是一条由 Dashboard 写入的测试通知，用于验证通知投递队列。",
                priority="normal",
            )
            self.send_json({"ok": True, "id": nid})
            return

        # ── API: Assistant — ack/ignore notification ────────────────────
        if self.path.startswith("/api/assistant/notifications/") and (
            self.path.endswith("/ack") or self.path.endswith("/ignore")
        ):
            import re
            m = re.match(r"/api/assistant/notifications/(\d+)/(ack|ignore)", self.path)
            if m:
                nid = int(m.group(1))
                action = m.group(2)
                from src.assistant.outbox import Outbox
                outbox = Outbox()
                if action == "ack":
                    ok = outbox.ack(nid)
                else:
                    ok = outbox.ignore(nid)
                self.send_json({"ok": ok})
                return

        # ── API: iLink — status ────────────────────────────────────────
        if self.path == "/api/ilink/status":
            try:
                from src.wechat.ilink_push import get_ilink_push
                ilink = get_ilink_push()
                status = ilink.get_status()
                self.send_json({"ok": True, **status})
            except Exception as e:
                self.send_json({"ok": True, "bound": False, "error": str(e)})
            return

        # ── API: iLink — QR code ────────────────────────────────────────
        if self.path == "/api/ilink/qrcode":
            try:
                from src.wechat.ilink_push import get_ilink_push
                ilink = get_ilink_push()
                result = ilink.get_qrcode()
                self.send_json(result)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: iLink — QR code status ─────────────────────────────────
        if self.path.startswith("/api/ilink/qrcode-status"):
            try:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                qrcode_id = params.get("qrcode", [""])[0]
                if not qrcode_id:
                    self.send_json({"status": "error", "error": "Missing qrcode parameter"})
                    return
                from src.wechat.ilink_push import get_ilink_push
                ilink = get_ilink_push()
                result = ilink.check_qrcode_status(qrcode_id)
                self.send_json(result)
            except Exception as e:
                self.send_json({"status": "error", "error": str(e)})
            return

        # ── API: iLink — bind ───────────────────────────────────────────
        if self.path == "/api/ilink/bind":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
                bot_token = body.get("bot_token", "")
                account_id = body.get("account_id", "")
                base_url = body.get("base_url", "")
                user_id = body.get("user_id", "")
                if not bot_token or not account_id or not user_id:
                    self.send_json({"ok": False, "error": "Missing required fields"})
                    return
                from src.wechat.ilink_push import get_ilink_push, reset_ilink_push
                ilink = get_ilink_push()
                ilink.bind(bot_token, account_id, base_url, user_id)
                # 重置单例，下次调用 get_ilink_push() 会重新从磁盘加载新账号
                reset_ilink_push()
                self.send_json({"ok": True, "message": "iLink bound successfully"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: iLink — unbind ─────────────────────────────────────────
        if self.path == "/api/ilink/unbind":
            try:
                from src.wechat.ilink_push import get_ilink_push, reset_ilink_push
                ilink = get_ilink_push()
                ilink.unbind()
                reset_ilink_push()  # Reset singleton so next call gets fresh state
                self.send_json({"ok": True, "message": "iLink unbound"})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
            return

        # ── API: iLink — test push ──────────────────────────────────────
        if self.path == "/api/ilink/test-push":
            # SSE stream: report each retry attempt in real time
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def _send_sse(event: str, data: dict):
                try:
                    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

            try:
                from src.wechat.ilink_push import get_ilink_push
                # 每次 test-push 前先 reload 单例，确保用磁盘上的最新账号
                ilink = get_ilink_push()
                ilink.reload()
                if not ilink.is_available():
                    _send_sse("error", {"error": "iLink not bound"})
                    return

                def on_retry(attempt, max_retries, delay, error):
                    _send_sse("retry", {
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "delay": delay,
                        "message": f"推送请求未响应，{delay:.0f}秒后第{attempt}次重试",
                        "raw": error,
                    })

                result = ilink.send_message(
                    "✅ wx-assist 推送测试成功！\n如果你看到这条消息，说明微信推送通道已正常工作。",
                    progress_callback=on_retry,
                )

                if result.get("success"):
                    update_status(error="")
                    _send_sse("success", {"message": "测试消息发送成功，请检查微信"})
                else:
                    raw_error = result.get("error", "")
                    friendly = "推送失败，请尝试先给助手主动发送一条消息激活；如果仍失败，请重新扫码绑定。"
                    if "session_expired" in raw_error or "errcode=-14" in raw_error:
                        friendly = "推送会话已失效（同一微信号在其他设备登录导致令牌失效）。请先给助手发送一条消息激活；如果仍失败，请重新扫码绑定。"
                    elif "rate-limited" in raw_error:
                        friendly = "推送请求被限流，3 次重试后仍失败。请稍后再试，或先给助手主动发送一条消息激活。"
                    update_status(ai_ok=False, ai_verified=False, error=friendly)
                    _send_sse("error", {
                        "error": friendly,
                        "detail": raw_error,
                    })
            except Exception as e:
                friendly = f"推送异常：{e}。请尝试先给助手主动发送一条消息；如果仍失败，请重新扫码绑定。"
                update_status(ai_ok=False, ai_verified=False, error=friendly)
                _send_sse("error", {"error": friendly})
            return

        # ── API: Assistant — trigger digest ─────────────────────────────
        if self.path == "/api/assistant/digest/run":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
            except Exception:
                body = {}
            group_name = body.get("group_name", "")
            self.send_json({"ok": True, "message": f"已触发 {group_name} 摘要生成"})
            return

        # ── API: Image/Video proxy (download + decrypt from CDN) ─────────────────
        if self.path.startswith("/api/image/proxy"):
            try:
                from src.wechat.image_decrypt import download_and_decrypt
                from urllib.parse import parse_qs, unquote
                import hashlib as _hashlib

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                url = params.get("url", [""])[0]
                key = params.get("key", [""])[0]
                token = params.get("token", [""])[0]

                if not url:
                    self.send_json({"ok": False, "error": "Missing url parameter"})
                    return

                # Detect video URLs (same logic as WeFlow)
                is_video = ("snsvideodownload" in url.lower() or
                            ".mp4" in url.lower() or
                            ("video" in url.lower() and "vweixinthumb" not in url.lower()))

                # Build full URL with token if provided
                import re as _re
                full_url = url.replace("http://", "https://")
                # For images: replace size suffix (/150, /200, /480) with /0 for full size
                # For videos: keep the URL as-is (video URLs already have correct path)
                if not is_video:
                    full_url = _re.sub(r"/(150|200|480)($|\?)", r"/0\2", full_url)
                    # For images: append token if provided and not already in URL
                    if token and "token=" not in full_url:
                        sep = "&" if "?" in full_url else "?"
                        full_url = f"{full_url}{sep}token={token}&idx=1"
                else:
                    # For videos: URL already has token+idx from DLL, just ensure https
                    # If token param is provided separately, append it
                    if token and "token=" not in full_url:
                        sep = "&" if "?" in full_url else "?"
                        full_url = f"{full_url}{sep}token={token}&idx=1"

                # Check in-memory cache first (images only, videos too large)
                cache_key = _hashlib.md5(full_url.encode()).hexdigest()
                if not is_video:
                    with _image_proxy_lock:
                        cached = _image_proxy_cache.get(cache_key)
                    if cached:
                        data, content_type = cached
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.send_header("X-Cache", "HIT")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(data)
                        return

                # Convert key to appropriate type
                key_val = None
                if key and key != "0":
                    try:
                        key_val = int(key)
                    except ValueError:
                        key_val = key

                # For video: use longer timeout
                timeout = 60 if is_video else 15
                data = download_and_decrypt(full_url, key_val, timeout=timeout)
                if data:
                    # Detect content type from magic bytes
                    content_type = "image/jpeg"
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        content_type = "image/png"
                    elif data[:4] == b"GIF8":
                        content_type = "image/gif"
                    elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
                        content_type = "image/webp"
                    elif data[4:8] == b"ftyp" or data[:4] == b"\x00\x00\x00\x1c":
                        content_type = "video/mp4"
                    elif data[:3] == b"\x1a\x45\xdf\xa3"[:3]:
                        content_type = "video/webm"

                    # Cache images only (videos too large for in-memory cache)
                    if not is_video:
                        with _image_proxy_lock:
                            _image_proxy_cache[cache_key] = (data, content_type)
                            # Limit cache size (keep last 200 images)
                            while len(_image_proxy_cache) > 200:
                                _image_proxy_cache.pop(next(iter(_image_proxy_cache)), None)

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("X-Cache", "MISS" if not is_video else "VIDEO")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                else:
                    self.send_json({"ok": False, "error": "Failed to download/decrypt"})
                    return
            except Exception as e:
                logger.error(f"Image proxy error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: Favorite image (decrypt from local V2 cache) ──────────────
        if self.path.startswith("/api/fav/image"):
            try:
                from src.wechat.v2_cache_decrypt import V2CacheManager
                from urllib.parse import parse_qs

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                local_id_str = params.get("id", [""])[0] or params.get("local_id", [""])[0]
                size = params.get("size", ["original"])[0]
                wxid = params.get("wxid", [""])[0]
                fullmd5 = params.get("fullmd5", [""])[0]
                fullsize_str = params.get("fullsize", [""])[0]
                fullsize = int(fullsize_str) if fullsize_str and fullsize_str.isdigit() else None
                dataid = params.get("dataid", [""])[0]

                if not local_id_str:
                    self.send_json({"ok": False, "error": "Missing id parameter"})
                    return

                try:
                    local_id = int(local_id_str)
                except ValueError:
                    self.send_json({"ok": False, "error": "Invalid id"})
                    return

                # Auto-detect wxid if not provided (use the active account)
                import os as _os
                data_dir = _os.getenv("WECHAT_DATA_DIR", "")
                if not wxid:
                    if data_dir:
                        from pathlib import Path as _Path
                        wxid_dirs = sorted(
                            [d for d in _Path(data_dir).iterdir()
                             if d.is_dir() and d.name.startswith("wxid_")],
                            key=lambda d: d.stat().st_mtime,
                            reverse=True,
                        )
                        if wxid_dirs:
                            wxid = wxid_dirs[0].name

                if not wxid:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"wxid not found")
                    return

                manager = V2CacheManager.get_instance(data_dir)
                data = manager.decrypt_fav_image(local_id, wxid, size=size,
                                                  fullmd5=fullmd5 if fullmd5 else None,
                                                  fullsize=fullsize)

                if data:
                    # Detect content type from magic bytes
                    content_type = "image/jpeg"
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        content_type = "image/png"
                    elif data[:4] == b"GIF8":
                        content_type = "image/gif"
                    elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
                        content_type = "image/webp"
                    elif len(data) >= 8 and data[4:8] == b"ftyp":
                        content_type = "video/mp4"

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                else:
                    # File locked (WeChat running) or not found
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(b"image not found or locked")
                    return
            except Exception as e:
                logger.error(f"Fav image error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: Voice message (chat voice) ─────────────────────────────────
        if self.path.startswith("/api/voice"):
            try:
                from urllib.parse import parse_qs
                from src.wechat.voice_decode import silk_to_wav

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                session_id = params.get("session_id", [""])[0] or params.get("sessionId", [""])[0]
                create_time = params.get("create_time", [""])[0] or params.get("createTime", [""])[0]
                local_id = params.get("local_id", [""])[0] or params.get("localId", [""])[0]
                svr_id = params.get("svr_id", [""])[0] or params.get("svrId", [""])[0]
                candidates_str = params.get("candidates", [""])[0]

                if not session_id or not create_time or not local_id:
                    self.send_json({"ok": False, "error": "Missing required parameters: session_id, create_time, local_id"})
                    return

                # Parse candidates
                candidates = []
                if candidates_str:
                    try:
                        candidates = json.loads(candidates_str)
                    except:
                        candidates = [candidates_str]

                # Get wcdb_client from api_handlers singleton
                from src.web.api_handlers import get_wcdb_client
                wcdb_client = get_wcdb_client()
                if not wcdb_client:
                    self.send_json({"ok": False, "error": "WCDB client not initialized"})
                    return

                # Call DLL to get voice data
                result = wcdb_client.get_voice_data(
                    session_id=session_id,
                    create_time=int(create_time),
                    local_id=int(local_id),
                    svr_id=int(svr_id) if svr_id else 0,
                    candidates=candidates
                )

                if not result.get("success"):
                    self.send_json({"ok": False, "error": result.get("error", "Unknown error")})
                    return

                hex_data = result.get("hex", "")
                if not hex_data:
                    self.send_json({"ok": False, "error": "Empty voice data"})
                    return

                # Convert SILK to WAV
                wav_data = silk_to_wav(hex_data)
                if not wav_data:
                    self.send_json({"ok": False, "error": "Failed to decode SILK voice data"})
                    return

                # Return WAV file
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(wav_data)
                return

            except Exception as e:
                logger.error(f"Voice API error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: Favorite voice in chat record ──────────────────────────────
        if self.path.startswith("/api/fav/voice/record"):
            try:
                from urllib.parse import parse_qs
                from src.wechat.voice_decode import silk_to_wav
                from src.web.api_handlers import _get_wcdb_fav_reader, get_wcdb_client

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                fav_id_str = params.get("fav_id", [""])[0] or params.get("id", [""])[0]
                dataid = params.get("dataid", [""])[0]

                if not fav_id_str or not dataid:
                    self.send_json({"ok": False, "error": "Missing fav_id and dataid parameters"})
                    return

                fav_id = int(fav_id_str)

                # Get fav item and find voice metadata by dataid
                fav_reader = _get_wcdb_fav_reader()
                if not fav_reader:
                    self.send_json({"ok": False, "error": "WCDB fav reader not initialized"})
                    return

                favs = fav_reader.get_items(limit=5000, offset=0)
                fav_item = None
                for f in favs:
                    if int(f.get("local_id") or 0) == fav_id:
                        fav_item = f
                        break

                if not fav_item:
                    self.send_json({"ok": False, "error": "Favorite item not found"})
                    return

                # Parse XML to find the dataitem with matching dataid
                import re
                content = fav_item.get("content", "") or fav_item.get("content_raw", "")
                if not content:
                    self.send_json({"ok": False, "error": "No content in favorite item"})
                    return

                # Find the dataitem with matching dataid
                # Pattern: <dataitem ... dataid="TARGET" ...> ... </dataitem>
                pattern = r'<dataitem[^>]*dataid="' + re.escape(dataid) + r'"[^>]*>(.*?)</dataitem>'
                match = re.search(pattern, content, re.DOTALL)
                if not match:
                    self.send_json({"ok": False, "error": f"Voice dataitem {dataid} not found in XML"})
                    return

                dataitem_xml = match.group(0)

                # Extract voice metadata from the dataitem
                src_msg_ct_match = re.search(r'<srcMsgCreateTime>(\d+)</srcMsgCreateTime>', dataitem_xml)
                from_msg_id_match = re.search(r'<fromnewmsgid>(\d+)</fromnewmsgid>', dataitem_xml)
                if not from_msg_id_match:
                    from_msg_id_match = re.search(r'<datasourceid>(\d+)</datasourceid>', dataitem_xml)

                if not src_msg_ct_match or not from_msg_id_match:
                    self.send_json({"ok": False, "error": "Missing srcMsgCreateTime or fromnewmsgid in voice dataitem"})
                    return

                create_time = int(src_msg_ct_match.group(1))
                msg_id = int(from_msg_id_match.group(1))

                # Get fromusr from the source tag
                fromusr_match = re.search(r'<fromusr>([^<]+)</fromusr>', content)
                # Get tousr - try to find it from the chat context
                tousr_match = re.search(r'<tousr>([^<]+)</tousr>', content)

                fromusr = fromusr_match.group(1) if fromusr_match else ""
                tousr = tousr_match.group(1) if tousr_match else ""

                # Build candidates list
                candidates = [t for t in [tousr, fromusr] if t]
                if not candidates:
                    self.send_json({"ok": False, "error": "Cannot determine chat participants"})
                    return

                # Call DLL to get voice data
                client = get_wcdb_client()
                if not client:
                    self.send_json({"ok": False, "error": "WCDB client not initialized"})
                    return

                # Try each candidate as session_id
                result = None
                for candidate in candidates:
                    r = client.get_voice_data(
                        session_id=candidate,
                        create_time=create_time,
                        local_id=0,
                        svr_id=msg_id,
                        candidates=candidates
                    )
                    if r and r.get("success") and r.get("hex"):
                        result = r
                        break

                if not result or not result.get("success"):
                    self.send_json({"ok": False, "error": result.get("error", "Failed to get voice data") if result else "No result"})
                    return

                hex_data = result.get("hex", "")
                if not hex_data:
                    self.send_json({"ok": False, "error": "Empty voice data"})
                    return

                # Convert SILK to WAV
                wav_data = silk_to_wav(hex_data)
                if not wav_data:
                    self.send_json({"ok": False, "error": "Failed to decode SILK voice data"})
                    return

                # Return WAV file
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(wav_data)
                return

            except Exception as e:
                logger.error(f"Favorite voice record API error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: Favorite voice download ────────────────────────────────────
        if self.path.startswith("/api/fav/voice/download"):
            try:
                from urllib.parse import parse_qs
                from src.wechat.voice_decode import silk_to_wav
                from src.web.api_handlers import _get_wcdb_fav_reader, get_wcdb_client

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                fav_id_str = params.get("id", [""])[0]
                fmt = params.get("format", ["wav"])[0].lower()  # wav or mp3

                if not fav_id_str:
                    self.send_json({"ok": False, "error": "Missing id parameter"})
                    return

                fav_id = int(fav_id_str)

                # Reuse the working favorites reader
                fav_reader = _get_wcdb_fav_reader()
                if not fav_reader:
                    self.send_json({"ok": False, "error": "WCDB fav reader not initialized"})
                    return

                favs = fav_reader.get_items(limit=5000, offset=0)
                fav_item = None
                for f in favs:
                    if int(f.get("local_id") or 0) == fav_id:
                        fav_item = f
                        break

                if not fav_item:
                    self.send_json({"ok": False, "error": "Favorite item not found"})
                    return

                import re
                content = fav_item.get("content", "") or fav_item.get("content_raw", "")
                if not content:
                    self.send_json({"ok": False, "error": "No content in favorite item"})
                    return

                fromusr_match = re.search(r'<fromusr>([^<]+)</fromusr>', content)
                tousr_match = re.search(r'<tousr>([^<]+)</tousr>', content)
                createtime_match = re.search(r'<createtime>(\d+)</createtime>', content)
                msgid_match = re.search(r'<msgid>(\d+)</msgid>', content)

                if not all([fromusr_match, tousr_match, createtime_match, msgid_match]):
                    self.send_json({"ok": False, "error": "Failed to parse voice metadata from XML"})
                    return

                fromusr = fromusr_match.group(1)
                tousr = tousr_match.group(1)
                createtime = int(createtime_match.group(1))
                msgid = int(msgid_match.group(1))
                candidates = [tousr, fromusr]

                client = get_wcdb_client()
                if not client:
                    self.send_json({"ok": False, "error": "WCDB client not initialized for voice"})
                    return

                result = client.get_voice_data(
                    session_id=tousr,
                    create_time=createtime,
                    local_id=0,
                    svr_id=msgid,
                    candidates=candidates
                )

                if not result.get("success"):
                    self.send_json({"ok": False, "error": result.get("error", "Unknown error")})
                    return

                hex_data = result.get("hex", "")
                if not hex_data:
                    self.send_json({"ok": False, "error": "Empty voice data"})
                    return

                # Convert based on requested format
                if fmt == "mp3":
                    from src.wechat.voice_decode import silk_to_mp3
                    audio_data = silk_to_mp3(hex_data)
                    content_type = "audio/mpeg"
                    ext = "mp3"
                else:
                    audio_data = silk_to_wav(hex_data)
                    content_type = "audio/wav"
                    ext = "wav"

                if not audio_data:
                    self.send_json({"ok": False, "error": f"Failed to decode SILK voice data to {ext}"})
                    return

                filename = f"voice_{fav_id}.{ext}"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(audio_data)))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(audio_data)
                return

            except Exception as e:
                logger.error(f"Favorite voice download API error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: Favorite voice (streaming playback) ────────────────────────
        if self.path.startswith("/api/fav/voice"):
            try:
                from urllib.parse import parse_qs
                from src.wechat.voice_decode import silk_to_wav
                from src.web.api_handlers import _get_wcdb_fav_reader, get_wcdb_client

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                fav_id_str = params.get("id", [""])[0] or params.get("fav_id", [""])[0]
                if not fav_id_str:
                    self.send_json({"ok": False, "error": "Missing id parameter"})
                    return

                fav_id = int(fav_id_str)

                # Reuse the working favorites reader (powers the favorites list page)
                fav_reader = _get_wcdb_fav_reader()
                if not fav_reader:
                    self.send_json({"ok": False, "error": "WCDB fav reader not initialized"})
                    return

                # Get favorite item via the working reader
                favs = fav_reader.get_items(limit=5000, offset=0)
                fav_item = None
                for f in favs:
                    if int(f.get("local_id") or 0) == fav_id:
                        fav_item = f
                        break

                if not fav_item:
                    self.send_json({"ok": False, "error": "Favorite item not found"})
                    return

                # Parse XML content to extract voice metadata
                import re
                content = fav_item.get("content", "") or fav_item.get("content_raw", "")
                if not content:
                    self.send_json({"ok": False, "error": "No content in favorite item"})
                    return

                # Extract fromusr, tousr, createtime, msgid from XML
                fromusr_match = re.search(r'<fromusr>([^<]+)</fromusr>', content)
                tousr_match = re.search(r'<tousr>([^<]+)</tousr>', content)
                createtime_match = re.search(r'<createtime>(\d+)</createtime>', content)
                msgid_match = re.search(r'<msgid>(\d+)</msgid>', content)

                if not all([fromusr_match, tousr_match, createtime_match, msgid_match]):
                    self.send_json({"ok": False, "error": "Failed to parse voice metadata from XML"})
                    return

                fromusr = fromusr_match.group(1)  # My wxid
                tousr = tousr_match.group(1)      # Other person's wxid
                createtime = int(createtime_match.group(1))
                msgid = int(msgid_match.group(1))

                # Build candidates list (sender + receiver)
                candidates = [tousr, fromusr]

                # Get wcdb_client for voice data retrieval
                client = get_wcdb_client()
                if not client:
                    self.send_json({"ok": False, "error": "WCDB client not initialized for voice"})
                    return

                # Call DLL to get voice data
                # Note: session_id should be the other person's wxid (for 1-on-1 chat)
                result = client.get_voice_data(
                    session_id=tousr,
                    create_time=createtime,
                    local_id=0,  # local_id from fav is not the message's local_id
                    svr_id=msgid,
                    candidates=candidates
                )

                if not result.get("success"):
                    self.send_json({"ok": False, "error": result.get("error", "Unknown error")})
                    return

                hex_data = result.get("hex", "")
                if not hex_data:
                    self.send_json({"ok": False, "error": "Empty voice data"})
                    return

                # Convert SILK to WAV
                wav_data = silk_to_wav(hex_data)
                if not wav_data:
                    self.send_json({"ok": False, "error": "Failed to decode SILK voice data"})
                    return

                # Return WAV file
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(wav_data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(wav_data)
                return

            except Exception as e:
                logger.error(f"Favorite voice API error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return


        # ── API: SNS video download ──────────────────────────────────────────
        if self.path.startswith("/api/sns/video/download"):
            try:
                from urllib.parse import parse_qs
                from src.wechat.image_decrypt import download_and_decrypt
                from src.web.api_handlers import _get_wcdb_sns_reader

                parsed = self.path.split("?", 1)
                params = parse_qs(parsed[1]) if len(parsed) > 1 else {}

                post_id = params.get("post_id", [""])[0] or params.get("id", [""])[0]
                media_idx_str = params.get("idx", ["0"])[0]
                media_idx = int(media_idx_str) if media_idx_str.isdigit() else 0

                if not post_id:
                    self.send_json({"ok": False, "error": "Missing post_id parameter"})
                    return

                # Load timeline to find the post
                reader = _get_wcdb_sns_reader()
                if not reader:
                    self.send_json({"ok": False, "error": "WCDB SNS reader not initialized"})
                    return

                posts = reader.get_timeline(limit=5000, offset=0)
                post = None
                for p in posts:
                    if str(p.get("id", "")) == post_id:
                        post = p
                        break

                if not post:
                    self.send_json({"ok": False, "error": "Post not found"})
                    return

                media_list = post.get("media", [])
                if media_idx >= len(media_list):
                    self.send_json({"ok": False, "error": "Media index out of range"})
                    return

                m = media_list[media_idx]
                url = m.get("url", "")
                if not url:
                    self.send_json({"ok": False, "error": "No URL for this media"})
                    return

                # Detect if video
                is_video = ("snsvideodownload" in url.lower() or
                            ".mp4" in url.lower() or
                            ("video" in url.lower() and "vweixinthumb" not in url.lower()))

                if not is_video:
                    self.send_json({"ok": False, "error": "This media is not a video"})
                    return

                # Build full URL
                import re as _re
                full_url = url.replace("http://", "https://")
                token = m.get("token", "")
                if token and "token=" not in full_url:
                    sep = "&" if "?" in full_url else "?"
                    full_url = f"{full_url}{sep}token={token}&idx=1"

                # Get key — prefer enc key from rawXml if media key is 0
                key = m.get("key", 0)
                if not key or str(key) == "0":
                    raw_xml = post.get("rawXml", "")
                    if raw_xml and "<enc" in raw_xml:
                        enc_match = _re.search(r'<enc\s+key="(\d+)"', raw_xml)
                        if enc_match:
                            key = int(enc_match.group(1))

                key_val = key if key else None
                data = download_and_decrypt(full_url, key_val, timeout=60)

                if not data:
                    self.send_json({"ok": False, "error": "Failed to download/decrypt video"})
                    return

                # Detect format
                ext = "mp4"
                if data[4:8] == b"ftyp":
                    ext = "mp4"
                elif data[:3] == b"\x1a\x45\xdf":
                    ext = "webm"

                filename = f"sns_video_{post_id}.{ext}"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
                return

            except Exception as e:
                logger.error(f"SNS video download API error: {e}")
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: AI Chat SSE streaming endpoint ────────────────────────
        if self.path == "/api/ai/chat/message" and self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
            except Exception:
                body = {}

            # Send SSE headers (text/event-stream)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            from src.web.ai_chat import handle_ai_chat_message_stream
            handle_ai_chat_message_stream(body, self.wfile)
            return

        # ── API: SNS AI Summarize (SSE stream) ──────────────────────
        if self.path == "/api/sns/ai/summarize" and self.command == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length)) if length > 0 else {}
            except Exception:
                body = {}

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            from src.web.ai_chat import handle_sns_ai_summarize_stream
            handle_sns_ai_summarize_stream(body, self.wfile)
            return

        # ── API: AI Chat non-streaming endpoints ──────────────────────
        if self.path.startswith("/api/ai/chat/"):
            try:
                from src.web.ai_chat import (
                    handle_ai_chat_start,
                    handle_ai_chat_compress,
                    handle_ai_chat_history,
                    handle_ai_chat_destroy,
                )

                parsed_path = self.path.split("?", 1)
                path_only = parsed_path[0]
                params = {}
                if len(parsed_path) > 1:
                    from urllib.parse import parse_qs
                    params = parse_qs(parsed_path[1])

                body = {}
                if self.command in ("POST", "PUT", "DELETE"):
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        body = json.loads(self.rfile.read(length)) if length > 0 else {}
                    except Exception:
                        body = {}

                if path_only == "/api/ai/chat/start":
                    result = handle_ai_chat_start(body)
                elif path_only == "/api/ai/chat/compress":
                    result = handle_ai_chat_compress(body)
                elif path_only == "/api/ai/chat/history":
                    result = handle_ai_chat_history(params)
                elif path_only == "/api/ai/chat/destroy":
                    result = handle_ai_chat_destroy(body)
                else:
                    result = {"ok": False, "error": "Unknown AI chat endpoint"}

                if result is not None:
                    self.send_json(result)
                    return
            except Exception as e:
                logger.error(f"AI Chat API error: {e}")
                update_status(ai_ok=False, ai_verified=False)
                self.send_json({"ok": False, "error": str(e)})
                return

        # ── API: 收藏/朋友圈/公众号/会话管理/调度器/导出/推送记录 (wechat-data-hub) ──────────
        if (self.path.startswith("/api/fav/") or self.path.startswith("/api/sns/") or
            self.path.startswith("/api/oa/") or self.path.startswith("/api/chat/") or
            self.path.startswith("/api/scheduler/") or self.path.startswith("/api/export/") or
            self.path.startswith("/api/push/") or self.path.startswith("/api/groups/") or
            self.path == "/api/scheduled-tasks"):
            try:
                    logger.info("[REQ-TRACE] entering api_handlers for %s thread=%s", self.path, threading.current_thread().name)
                    from src.web.api_handlers import handle_api_request
                    from src.assistant.config import load_assistant_config as _load_cfg

                    # Parse query params
                    parsed_path = self.path.split("?", 1)
                    path_only = parsed_path[0]
                    params = {}
                    if len(parsed_path) > 1:
                        from urllib.parse import parse_qs
                        params = parse_qs(parsed_path[1])

                    # Parse body for POST/PUT/DELETE
                    body = {}
                    if self.command in ("POST", "PUT", "DELETE"):
                        try:
                            length = int(self.headers.get("Content-Length", "0"))
                            body = json.loads(self.rfile.read(length)) if length > 0 else {}
                        except Exception:
                            body = {}

                    cfg = _load_cfg()
                    # Pass HTTP method for PUT/DELETE disambiguation
                    params["_method"] = self.command
                    result = handle_api_request(path_only, params, cfg, body)
                    if result is not None:
                        if isinstance(result, dict) and result.get('_binary'):
                            data = result.get('data', b'')
                            ct = result.get('content_type', 'application/octet-stream')
                            self.send_response(200)
                            self.send_header('Content-Type', ct)
                            self.send_header('Content-Length', str(len(data)))
                            self.send_header('Cache-Control', 'public, max-age=86400')
                            self.send_header('Access-Control-Allow-Origin', '*')
                            self.end_headers()
                            self.wfile.write(data)
                            return
                        self.send_json(result)
                        return
            except Exception as e:
                    logger.error(f"API handler error for {self.path}: {e}")
                    self.send_json({"ok": False, "error": str(e)})
                    return

        # ── SPA fallback: serve index.html for unknown paths ──────────
        if self.command != "GET" and self.command != "HEAD":
            self.send_response(405)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "Method not allowed"}).encode())
            return

        path = self.translate_path(self.path)
        if not Path(path).exists():
            self.path = "/index.html"

        super().do_GET()

    def log_message(self, format, *args):
        """Log HTTP errors but suppress normal access logs."""
        if args and any(
            code in str(args).lower()
            for code in ["error", "exception", "400", "401", "403", "404", "405", "500"]
        ):
            logger.warning("HTTP %s", format % args)

    def send_json(self, obj, status_code=200):
        """Send a JSON response with configurable HTTP status code.

        Args:
            obj: JSON-serializable object.
            status_code: HTTP status code (default 200). Use 400 for
                client errors, 500 for server errors.
        """
        req_elapsed = (time.monotonic() - req_t0) * 1000 if 'req_t0' in dir() else 0
        if req_elapsed > 200:
            logger.info("[REQ-TRACE] %s %s → %d (%.0fms)", self.command, self.path, status_code, req_elapsed)
        body = json.dumps(obj, ensure_ascii=False, default=str)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


def _run_server(host, port):
    """Run the HTTP server (blocking, called in daemon thread)."""
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), _UIHandler)
    server.daemon_threads = True  # WebSocket handlers won't block exit
    logger.info("Web UI: http://%s:%s", host, port)
    server.serve_forever()


def start_web_server(host="127.0.0.1", port=17327):
    """Start the web UI in a daemon thread (idempotent)."""
    if not _server_guard.try_start():
        logger.debug("Web server already running, skipping duplicate start")
        return None

    if not UI_DIR.exists():
        logger.warning("UI not built. Run: cd ui && npm run build")
        return None

    thread = threading.Thread(
        target=_run_server, args=(host, port),
        daemon=True, name="web-ui-server",
    )
    thread.start()
    return thread
