# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for wx-assist Desktop.

Build: pyinstaller build.spec
Output: dist/wx-assist.exe
"""
import sys
import site
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)

# ── Resolve webview runtime DLLs dynamically ───────────────────────────
def _find_webview_runtime_dir():
    """Find the webview package's runtime directory in site-packages."""
    for sp in site.getsitepackages():
        candidate = Path(sp) / "webview" / "lib"
        if candidate.exists():
            return candidate
    # Fallback: try user site-packages
    user_sp = site.getusersitepackages()
    if user_sp:
        candidate = Path(user_sp) / "webview" / "lib"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "webview package not found. Install with: pip install pywebview"
    )

_webview_dir = _find_webview_runtime_dir()
_webview_runtime = _webview_dir / "runtimes" / "win-x64" / "native" / "WebView2Loader.dll"
_webview_interop = _webview_dir / "WebBrowserInterop.x64.dll"

if not _webview_runtime.exists():
    raise FileNotFoundError(f"WebView2Loader.dll not found at {_webview_runtime}")
if not _webview_interop.exists():
    raise FileNotFoundError(f"WebBrowserInterop.x64.dll not found at {_webview_interop}")

a = Analysis(
    ['desktop.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[
        (str(_webview_runtime), './runtimes/win-x64/native'),
        (str(_webview_interop), './lib'),
        (str(PROJECT_ROOT / 'lib' / 'wcdb_api.dll'), 'lib'),
        (str(PROJECT_ROOT / 'lib' / 'WCDB.dll'), 'lib'),
        (str(PROJECT_ROOT / 'lib' / 'MSVCP140.dll'), 'lib'),
        (str(PROJECT_ROOT / 'lib' / 'VCRUNTIME140.dll'), 'lib'),
        (str(PROJECT_ROOT / 'lib' / 'VCRUNTIME140_1.dll'), 'lib'),
        (str(PROJECT_ROOT / 'lib' / 'wx_key.dll'), 'lib'),
    ],
    datas=[
        ('ui/dist', 'ui/dist'),
        ('lib/wasm', 'lib/wasm'),
        ('.env.example', '.'),
        # data/ is runtime-generated — do NOT bundle into read-only _MEIPASS
    ],
    hiddenimports=[
        'src', 'src.bot', 'src.config', 'src.main',
        'src.db', 'src.db.schema', 'src.db.store',
        'src.assistant', 'src.assistant.config', 'src.assistant.alert',
        'src.assistant.digest', 'src.assistant.scheduler',
        'src.assistant.outbox', 'src.assistant.oa_monitor',
        'src.assistant.oa_digest', 'src.assistant.oa_groups',
        'src.assistant.oa_parser', 'src.assistant.oa_reader',
        'src.assistant.task_center',
        'src.agent', 'src.agent.engine', 'src.agent.tools',
        'src.agent.registry', 'src.agent.mcp_server',
        'src.summarize', 'src.summarize.base', 'src.summarize.claude_backend',
        'src.summarize.deepseek_backend', 'src.summarize.models', 'src.summarize.prompts',
        'src.memory', 'src.memory.consolidator',
        'src.wechat', 'src.wechat.base', 'src.wechat.wcdb_backend',
        'src.wechat.wcdb_client', 'src.wechat.mac_hybrid_backend',
        'src.wechat.mac_weflow_client',
        'src.wechat.mac_ui_backend', 'src.wechat.window_controller',
        'src.wechat.keyboard', 'src.wechat.helpers', 'src.wechat.extract_key',
        'src.wechat.native', 'src.wechat.native.injector',
        'src.wechat.image_decrypt', 'src.wechat.v2_cache_decrypt',
        'src.wechat.voice_decode', 'src.wechat.ilink_push',
        'src.wechat.ilink_receiver',
        'src.web', 'src.web.server', 'src.web.api_handlers', 'src.web.ai_chat',
        'src.nickname', 'src.admin',
        'src.utils', 'src.utils.logging_config', 'src.utils.llm_logger',
        'src.guard', 'src.guard.content_filter',
        'src.scheduler', 'src.scheduler.task_scheduler',
        'dotenv', 'anthropic', 'openai', 'pydantic',
        'ddgs', 'duckduckgo_search',
        'psutil', 'pyperclip',
        'uiautomation',
        'webview', 'webview.platforms', 'webview.platforms.edgechromium',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'requests', 'urllib3',
        'APScheduler', 'apscheduler.schedulers.background', 'apscheduler.triggers.cron',
        'zstandard', 'Crypto',
    'src.mcp',
    'src.mcp.client',
    'src.mcp.manager',
    'src.mcp.tool_registry',
    'src.mcp.config_schema',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'jedi', 'IPython',
              'numpy', 'chromadb', 'fastembed', 'onnxruntime'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='wx-assist_no_rag',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / 'favicon.ico'),
)
