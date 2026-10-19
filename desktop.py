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
        start_web_server, update_status, _bot_exited, _bot_control,
    )
    web_thread = start_web_server()

    owner = _bot_control.reserve_start()
    if owner is None:
        logger.info("Bot auto-start skipped: another Bot is running or stopping")
        return
    if not _bot_control.register_running_thread(threading.current_thread(), owner):
        logger.info("Bot auto-start reservation expired")
        _bot_exited(owner)
        return

    try:
        from src.config import load_config
        config = load_config()
        update_status(
            wechat_backend=config.wechat_backend,
        )
        from src.bot import Bot
        bot = Bot(config)
        if not _bot_control.register_running_thread(threading.current_thread(), owner):
            logger.info("Bot auto-start cancelled before Bot.run")
            return
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
        _bot_exited(owner)


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


# ── 关窗行为 / 托盘 ────────────────────────────────────────────────────
# 点窗口关闭按钮时做什么，由 .env 的 CLOSE_ACTION 决定，可在
# 「系统配置 › 通用」里随时改。每次都现读，改完立即生效。

_TRAY_AVAILABLE = True
try:
    import pystray
    from PIL import Image as _PILImage
except Exception:  # 托盘组件缺失时降级为普通最小化，不影响其它功能
    pystray = None
    _PILImage = None
    _TRAY_AVAILABLE = False

_window_ref = None        # 当前窗口对象
_exit_requested = False   # True = 放行关闭，真的退出
_tray_icon = None         # pystray 图标实例
_tray_hint_shown = False  # 托盘气泡只提示一次
_ask_lock = threading.Lock()
_ask_pending = False      # 前端关窗弹窗是否还在等用户选择
_ASK_DIALOG_TIMEOUT = 3.0  # 等前端弹窗的秒数，超时退到系统弹窗


def _read_close_action() -> str:
    """现读 .env 的 CLOSE_ACTION，保证设置改完立即生效。"""
    try:
        from src.web.server import read_env_key
        value = (read_env_key("CLOSE_ACTION") or "ask").strip().lower()
    except Exception as exc:
        logger.debug("读取 CLOSE_ACTION 失败: %s", exc)
        value = "ask"
    return value if value in ("ask", "tray", "quit") else "ask"


def _tray_image():
    """托盘图标：优先用打包进来的 favicon.ico，取不到就画一个绿点兜底。"""
    if getattr(sys, "frozen", False):
        path = Path(getattr(sys, "_MEIPASS", "")) / "favicon.ico"
    else:
        path = PROJECT_ROOT / "favicon.ico"
    try:
        if _PILImage is not None and path.exists():
            return _PILImage.open(path)
    except Exception as exc:
        logger.debug("加载托盘图标失败: %s", exc)
    if _PILImage is not None:
        image = _PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = _PILImage.ImageDraw.Draw(image)
        draw.ellipse((6, 6, 58, 58), fill=(13, 140, 92, 255))
        return image
    return None


def _show_window(*_args):
    """从托盘把窗口显示出来。"""
    window = _window_ref
    if window is None:
        return
    try:
        window.show()
        window.restore()
        logger.info("窗口已从托盘恢复显示")
    except Exception as exc:
        logger.warning("恢复窗口失败: %s", exc)


def _stop_tray():
    """关掉托盘图标。"""
    global _tray_icon
    icon, _tray_icon = _tray_icon, None
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass


def _quit_application(*_args):
    """真正退出：先放行关闭，再销毁窗口，走正常的退出流程。"""
    global _exit_requested
    _exit_requested = True
    _stop_tray()
    window = _window_ref
    if window is None:
        os._exit(0)
        return
    try:
        window.destroy()
    except Exception as exc:
        logger.warning("销毁窗口失败，直接退出: %s", exc)
        os._exit(0)


def _ensure_tray() -> bool:
    """确保托盘图标存在；返回是否成功（失败时调用方负责降级）。"""
    global _tray_icon
    if not _TRAY_AVAILABLE:
        return False
    if _tray_icon is not None:
        return True

    image = _tray_image()
    if image is None:
        return False

    try:
        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", _show_window, default=True),
            pystray.MenuItem("摘星正在后台运行", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("立即退出", _quit_application),
        )
        _tray_icon = pystray.Icon("wx-assist", image, "摘星 · 微信助手", menu)
        # pywebview 占着主线程，所以用 detached 模式在自己的线程里跑消息循环
        _tray_icon.run_detached()
        return True
    except Exception as exc:
        logger.warning("创建托盘图标失败: %s", exc)
        _tray_icon = None
        return False


def _hide_to_tray(window) -> bool:
    """隐藏窗口到托盘；托盘不可用时退化为普通最小化。"""
    global _tray_hint_shown
    if not _ensure_tray():
        logger.info("托盘不可用，退化为最小化窗口")
        try:
            window.minimize()
        except Exception as exc:
            logger.debug("最小化窗口失败: %s", exc)
        return False

    try:
        window.hide()
    except Exception as exc:
        logger.warning("隐藏窗口失败: %s", exc)
        return False

    if not _tray_hint_shown:
        _tray_hint_shown = True
        try:
            _tray_icon.notify(
                "程序仍在后台运行，关键词提醒和定时任务会继续执行。",
                "已最小化到托盘",
            )
        except Exception:
            pass
    return True


def _native_close_dialog(window):
    """前端弹窗没响应时的兜底：用系统弹窗问一次。

    这一步是为了"窗口绝不会关不掉" —— 万一前端白屏或卡死，
    用户至少还能通过它做出选择，而不是只能去任务管理器杀进程。
    """
    try:
        import ctypes
        result = ctypes.windll.user32.MessageBoxW(
            0,
            "关闭窗口后程序仍在后台运行，关键词提醒和定时任务会继续执行。\n\n"
            "「是」最小化到托盘    「否」退出程序    「取消」什么都不做",
            "摘星 · 微信助手",
            0x3 | 0x20 | 0x40000,  # YESNOCANCEL | ICONQUESTION | TOPMOST
        )
    except Exception as exc:
        logger.warning("系统兜底弹窗失败: %s", exc)
        return

    if result == 6:      # 是 → 最小化到托盘
        _hide_to_tray(window)
    elif result == 7:    # 否 → 退出程序
        _quit_application()


def _ask_close_choice(window):
    """取消关闭，让 WebUI 弹窗询问；前端不响应就退到系统弹窗。"""
    global _ask_pending
    with _ask_lock:
        if _ask_pending:
            return  # 上一次询问还没结束，不叠加
        _ask_pending = True

    def _worker():
        global _ask_pending
        try:
            # 必须在后台线程里发 JS：这个回调跑在窗口自己的 GUI 线程上，
            # 直接调 evaluate_js 会"自己等自己"卡死。
            window.evaluate_js(
                "window.__wxShowCloseDialog && window.__wxShowCloseDialog()"
            )
        except Exception as exc:
            logger.debug("调用前端关窗弹窗失败: %s", exc)

        deadline = time.time() + _ASK_DIALOG_TIMEOUT
        while time.time() < deadline:
            with _ask_lock:
                if not _ask_pending:
                    return  # 前端已处理（POST /api/app/close-intent）
            time.sleep(0.2)

        with _ask_lock:
            if not _ask_pending:
                return
            _ask_pending = False
        logger.info("前端关窗弹窗无响应，改用系统弹窗")
        _native_close_dialog(window)

    threading.Thread(target=_worker, name="close-choose", daemon=True).start()


def _resolve_close_intent(action: str):
    """前端关窗弹窗的选择（由 /api/app/close-intent 触发）。"""
    global _ask_pending
    with _ask_lock:
        _ask_pending = False
    window = _window_ref
    if window is None:
        return
    if action == "tray":
        _hide_to_tray(window)
    else:
        _quit_application()


def _on_window_closing(window=None):
    """关窗拦截。

    pywebview 的约定：返回 False 表示取消这次关闭。
    注意这个回调是同步跑在 GUI 线程上的，所以里面绝不能阻塞或直接操作
    前端 —— 需要做的事一律丢到后台线程。
    """
    global _exit_requested
    if _exit_requested:
        return True

    target = window or _window_ref
    if target is None:
        _exit_requested = True
        return True

    action = _read_close_action()
    logger.info("窗口关闭请求：CLOSE_ACTION=%s", action)

    if action == "quit":
        _exit_requested = True
        _stop_tray()
        return True   # 放行，走原来的退出流程
    if action == "tray":
        _hide_to_tray(target)
        return False
    _ask_close_choice(target)
    return False


def _notify_existing_instance() -> bool:
    """让已经在运行的实例把窗口显示出来（它可能被最小化到托盘了）。

    这里必须绕过系统代理直连本机 —— 否则 127.0.0.1 的请求会被丢给
    代理软件，然后以各种奇怪的方式失败（实测踩过这个坑）。
    """
    try:
        import urllib.request
        request = urllib.request.Request(
            "http://127.0.0.1:17327/api/app/show",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            ok = response.status == 200
        logger.info("已唤起正在运行的实例: %s", ok)
        return ok
    except Exception as exc:
        logger.info("唤起已有实例失败：%s", exc)
        return False


def _signal_handler(signum, frame):
    """SIGTERM/SIGINT handler — trigger graceful shutdown then exit."""
    sys.exit(0)


def main():
    # ── Set CWD to app home directory ──────────────────────────────
    if getattr(sys, "frozen", False):
        os.chdir(str(Path(sys.executable).resolve().parent))
    else:
        os.chdir(str(PROJECT_ROOT))

    # ── Skill script execution mode (EXE builds only) ──────────────
    # EXE 模式 skill 脚本在子进程跑: [wx-assist.exe, --run-script, weather.py, args...]
    # desktop.py 检测到 --run-script 后直接 exec 脚本并退出,
    # 不走互斥锁 / 全量启动, 避免启动第二个 wx-assist 实例弹框。
    # 注意: CWD 已在上面设置, 所以脚本内的相对路径 data/ 等能正确解析。
    if getattr(sys, "frozen", False) and len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
        script_path = sys.argv[2]
        script_args = sys.argv[3:]
        sys.argv = [script_path] + script_args
        # Reconfigure stdout/stderr to UTF-8 (EXE inherits GBK from Windows)
        import io
        if sys.stdout and hasattr(sys.stdout, "buffer") and sys.stdout.buffer:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "buffer") and sys.stderr.buffer:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        with open(script_path, encoding="utf-8") as _f:
            _code = compile(_f.read(), script_path, "exec")
        exec(_code, {"__name__": "__main__", "__file__": str(script_path)})
        sys.exit(0)

    # ── Single-instance mutex (EXE only, source mode allows multi) ─
    # Creates a Windows named mutex so double-clicking the EXE twice
    # doesn't start two instances that fight over the same port, DB,
    # and iLink session.  The mutex lives as long as this handle is
    # open; when the process exits, Windows auto-releases it.
    if getattr(sys, "frozen", False):
        import ctypes as _ctypes
        _MUTEX_NAME = "Global\\wx-assist-17327"
        _mutex_h = _ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not _mutex_h:
            pass  # CreateMutex failed — proceed anyway
        elif _ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            _ctypes.windll.kernel32.CloseHandle(_mutex_h)
            # 已有实例在跑：先试着把它的窗口唤起来（它可能被最小化到托盘了，
            # 用户正是因为找不到窗口才又点了一次图标）。
            # 唤不起来才提示"已在运行"，避免出现"打不开新的、旧窗口也找不到"。
            if _notify_existing_instance():
                sys.exit(0)
            _ctypes.windll.user32.MessageBoxW(
                0,
                "wx-assist 已在运行，无需重复启动。\n\n"
                "如果无法正常使用，请先关闭已有程序再试。",
                "微信助手 — 提示",
                0x40,  # MB_ICONINFORMATION
            )
            sys.exit(0)

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

        # 把窗口交给 /api/app/* 使用，并挂上关窗拦截。
        # 注册回调而不是让 server 直接碰窗口：窗口只能在它自己的线程里操作。
        global _window_ref
        _window_ref = window
        from src.web.server import register_app_control
        register_app_control(
            close=_resolve_close_intent,
            show=_show_window,
            exit=_quit_application,
        )
        window.events.closing += _on_window_closing

        webview.start(gui="edgechromium")
    except Exception as e:
        logger_available = False
        try:
            from src.web.server import logger as _srv_logger
            _srv_logger.warning("WebView2 不可用，正在使用浏览器: %s", e)
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
    # PyInstaller worker processes must enter multiprocessing before starting UI.
    import multiprocessing
    multiprocessing.freeze_support()
    main()
