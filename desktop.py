"""
Desktop application entry point.

Uses Edge WebView2 (built into Windows 10/11) for a native window.
Falls back to browser if WebView2 is unavailable.

Usage:
    python desktop.py
    wx-assist.exe  (packaged version)
"""
import atexit
import logging
import os
import signal
import sys
import threading
import time
import webbrowser
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent

# ── Process identity for hard-kill protection ────────────────────
# Record our PID so atexit can kill us if graceful shutdown stalls.
_OUR_PID = os.getpid()
logger = logging.getLogger(__name__)


def _write_crash_log(exc_info: str) -> None:
    """Write crash details to a file for windowed-mode debugging."""
    try:
        crash_dir = PROJECT_ROOT / "data"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_path = crash_dir / "crash.log"
        with open(crash_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Crash at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(exc_info)
            f.write(f"\n{'='*60}\n\n")
    except Exception:
        pass  # last resort — can't even write crash log


def start_bot():
    """Start bot in background thread (signal-safe)."""
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.web.server import (
        start_web_server, update_status,
        register_bot, _bot_exited,
    )
    web_thread = start_web_server()

    try:
        from src.config import load_config
        config = load_config()
        update_status(
            wechat_backend=config.wechat_backend,
        )
        from src.bot import Bot
        bot = Bot(config)
        # Bot.run() calls _register_backend() during init — no patch needed
        register_bot(thread=threading.current_thread(), backend=None)
        bot.run()
        # Bot exited normally (e.g., no groups found)
        update_status(running=False)
    except SystemExit:
        update_status(running=False)
    except Exception as e:
        update_status(running=False, error=str(e))
        exc_info = traceback.format_exc()
        _write_crash_log(exc_info)
    finally:
        # Always reset bot control state so the user can restart
        # via the web UI (or auto-restart will work next launch)
        _bot_exited()


def _graceful_shutdown():
    """Stop bot cleanly within the timeout window.

    Called via atexit when the Python process is exiting (window closed,
    SIGTERM, SIGINT, or sys.exit()).  We do our best to stop the bot
    and close the database gracefully, but if anything stalls we hard-kill our
    own process after 5 seconds so the user never ends up with a zombie
    background process.
    """
    import logging
    log = logging.getLogger("desktop.shutdown")
    log.info("Graceful shutdown initiated (PID=%d)", _OUR_PID)

    # 1. Try to stop the bot cleanly
    try:
        from src.web.server import _bot_control
        _bot_control.stop()
        log.info("Bot stopped successfully")
    except Exception as e:
        log.warning("Bot stop failed: %s", e)

    # 2. DLL calls are now serialized by _dll_lock (not an executor),
    #    so no executor shutdown needed. The lock will be released
    #    naturally when the process exits.

    # 3. Hard-kill safeguard — if the process is still alive in 5s,
    #    something is stuck (daemon thread, hanging DLL call, etc.).
    #    Schedule a hard kill so the user never has orphan processes.
    import subprocess
    try:
        subprocess.Popen(
            [
                sys.executable if not getattr(sys, "frozen", False) else "cmd",
                "-c" if not getattr(sys, "frozen", False) else "/c",
                f"timeout /t 5 /nobreak >nul & taskkill /pid {_OUR_PID} /f"
                if not getattr(sys, "frozen", False)
                else f"timeout /t 5 /nobreak >nul & taskkill /pid {_OUR_PID} /f",
            ],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
        )
    except Exception:
        pass  # Best effort — if this fails, the process will still exit
              # when all non-daemon threads finish.


def _signal_handler(signum, frame):
    """SIGTERM/SIGINT handler — trigger graceful shutdown then exit."""
    sys.exit(0)


def main():
    # ── Set CWD to app home directory ──────────────────────────────
    # Regardless of how the app is launched (double-click EXE / CLI /
    # shortcut), fix the current working directory to the application
    # directory so all relative paths (data/, .env, etc.) resolve
    # correctly and data survives across sessions.
    if getattr(sys, "frozen", False):
        os.chdir(str(Path(sys.executable).resolve().parent))
    else:
        os.chdir(str(PROJECT_ROOT))

    # ── Register graceful shutdown ─────────────────────────────────
    # atexit fires on sys.exit() and normal interpreter shutdown.
    # signal handlers fire on SIGTERM (kill) and SIGINT (Ctrl+C).
    # Together they ensure: closing the window → atexit → clean stop
    # → 5s hard-kill safeguard.  No zombie processes left behind.
    atexit.register(_graceful_shutdown)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # ── Setup file logging early ───────────────────────────────────
    # This ensures all log output (including web server and OA digest)
    # is written to data/bot.log, not just console.
    from src.utils.logging_config import setup_logging
    log_level = os.getenv("LOG_LEVEL", "INFO").strip()
    setup_logging(level=log_level, log_file="data/bot.log")

    # Check if onboarding is needed
    from src.config import is_onboarding_done
    onboarding_needed = not is_onboarding_done()

    # Always start web server (needed for both onboarding and dashboard)
    from src.web.server import start_web_server
    web_thread = start_web_server()

    # Wait for web server (raw TCP — bypasses Windows system proxy)
    import socket as _socket
    ready = False
    for _ in range(30):
        try:
            s = _socket.create_connection(("127.0.0.1", 17327), timeout=1)
            s.close()
            ready = True
            break
        except (OSError, _socket.timeout):
            time.sleep(0.5)

    if not ready:
        _write_crash_log("Web server startup timeout (30 attempts)")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Web 服务器启动超时，请检查端口 17327 是否被占用。\n\n"
                "详情见 data/crash.log",
                "微信助手 — 启动失败",
                0x10,
            )
        except Exception:
            pass
        return

    # ── Auto-start bot when prerequisites are met ──────────────────
    if not onboarding_needed:
        # Check 1: WeChat process must be running (WCDB key depends on it)
        import subprocess as _sp
        wechat_ok = False
        try:
            r = _sp.run(
                ["tasklist", "/FI", "IMAGENAME eq WeChat.exe", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            wechat_ok = "WeChat.exe" in r.stdout
        except Exception:
            pass

        if wechat_ok:
            _t = threading.Thread(target=start_bot, daemon=True, name="bot-auto")
            _t.start()
            logger.info("Bot auto-started (onboarding done + WeChat running)")
        else:
            logger.info("Bot auto-start skipped: WeChat process not found")

    title = "微信助手 — 初始设置" if onboarding_needed else "微信助手 — Dashboard"

    # Try native WebView2, fall back to browser
    try:
        import webview
        window = webview.create_window(
            title=title,
            url="http://127.0.0.1:17327",
            width=1200,
            height=800,
            min_size=(900, 600),
        )
        webview.start(gui="edgechromium")
    except Exception as e:
        logger_available = False
        try:
            from src.web.server import logger
            logger.warning("WebView2 不可用，正在使用浏览器: %s", e)
            logger_available = True
        except Exception:
            pass
        if not logger_available:
            _write_crash_log(f"WebView2 unavailable: {e}\nFalling back to browser.")
        webbrowser.open("http://127.0.0.1:17327")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
