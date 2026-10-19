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

# ── exe 版本资源 ───────────────────────────────────────────────────────
# 版本号从 src/version.py 读取（纯文本正则提取，不 import src 包），
# 在 build/ 下生成 PyInstaller 版本资源文件，让 exe 的右键属性里
# 显示出正确版本，避免再出现"程序里写着 1.0.1、Release 上却是 v1.6.0"。
import re as _re


def _read_app_version() -> str:
    src = (PROJECT_ROOT / 'src' / 'version.py').read_text(encoding='utf-8')
    match = _re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, _re.M)
    if not match:
        raise RuntimeError('src/version.py 中找不到 __version__')
    return match.group(1)


APP_VERSION = _read_app_version()
_ver_tuple = tuple(int(x) for x in APP_VERSION.split('.'))
_ver_tuple = (_ver_tuple + (0, 0, 0, 0))[:4]

_version_file = PROJECT_ROOT / 'build' / 'version_info.txt'
_version_file.parent.mkdir(parents=True, exist_ok=True)
_version_file.write_text(
    "VSVersionInfo(\n"
    "  ffi=FixedFileInfo(\n"
    f"    filevers={_ver_tuple},\n"
    f"    prodvers={_ver_tuple},\n"
    "    mask=0x3f,\n"
    "    flags=0x0,\n"
    "    OS=0x40004,\n"
    "    fileType=0x1,\n"
    "    subtype=0x0,\n"
    "    date=(0, 0)\n"
    "  ),\n"
    "  kids=[\n"
    "    StringFileInfo([\n"
    "      StringTable('040904B0', [\n"
    "        StringStruct('CompanyName', 'MaleleStudySpace'),\n"
    "        StringStruct('FileDescription', 'wx-assist'),\n"
    f"        StringStruct('FileVersion', '{APP_VERSION}'),\n"
    "        StringStruct('InternalName', 'wx-assist'),\n"
    "        StringStruct('LegalCopyright', 'MIT License'),\n"
    "        StringStruct('OriginalFilename', 'wx-assist.exe'),\n"
    "        StringStruct('ProductName', 'wx-assist'),\n"
    f"        StringStruct('ProductVersion', '{APP_VERSION}'),\n"
    "      ])\n"
    "    ]),\n"
    "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
    "  ]\n"
    ")\n",
    encoding='utf-8',
)

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
        # 注意：不再打包 wcdb_api.dll / WCDB.dll / SDL2.dll。
        # 数据库读取已改为纯 Python（PBKDF2 + AES-CBC 分页解密 + sqlite3），
        # 见 src/wechat/db_crypto.py 与 src/wechat/db_reader.py。
        (str(PROJECT_ROOT / 'lib' / 'wx_key.dll'), 'lib'),  # 密钥提取仍需要
    ],
    datas=[
        ('ui/dist', 'ui/dist'),
        ('lib/wasm', 'lib/wasm'),
        ('.env.example', '.'),
        ('favicon.ico', '.'),  # 最小化到托盘时要用它做图标
        # RAG embedding model (~182MB, needed for semantic search)
        (str(PROJECT_ROOT / 'models'), 'models'),
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
        'src.assistant.rag', 'src.assistant.rag.engine',
        'src.assistant.rag.embedder', 'src.assistant.rag.vector_store',
        'src.assistant.rag.chunking', 'src.assistant.rag.models',
        'src.assistant.rag.reranker',
        'src.agent', 'src.agent.engine', 'src.agent.tools',
        'src.agent.registry', 'src.agent.mcp_server',
        'src.summarize', 'src.summarize.base', 'src.summarize.claude_backend',
        'src.summarize.deepseek_backend', 'src.summarize.models', 'src.summarize.prompts',
        'src.memory', 'src.memory.consolidator',
        'src.wechat', 'src.wechat.base', 'src.wechat.wcdb_backend',
        'src.wechat.wcdb_client', 'src.wechat.mac_hybrid_backend',
        'src.wechat.mac_weflow_client',
        'src.wechat.content_codec',  # wcdb_backend 函数内 import，静态分析检测不到，必须显式加入
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
        'src.scheduler', 'src.scheduler.cron_scheduler',
        'src.skill', 'src.skill.engine',
        'dotenv', 'anthropic', 'openai', 'pydantic',
        'ddgs', 'duckduckgo_search',
        'psutil', 'pyperclip',
        'uiautomation',
        'webview', 'webview.platforms', 'webview.platforms.edgechromium',
        'PIL', 'PIL.Image', 'PIL.ImageDraw',
        'pystray', 'pystray._win32',
        'requests', 'urllib3',
        'APScheduler', 'apscheduler.schedulers.background', 'apscheduler.triggers.cron',
        'zstandard', 'Crypto',
        # RAG dependencies
        'numpy', 'chromadb', 'fastembed',
        # ChromaDB submodules not statically imported (loaded via Settings
        # dynamic import) — bundle explicitly to avoid No module named errors
        # in the frozen EXE.
        'chromadb.telemetry',
        'chromadb.telemetry.product',
        'chromadb.telemetry.product.posthog',
        'chromadb.telemetry.product.events',
        'chromadb.telemetry.opentelemetry',
        'overrides',
        # ChromaDB native Rust bindings (compiled .pyd, not auto-detected)
        'chromadb_rust_bindings',
        'chromadb.api.rust',
    'src.mcp',
    'src.mcp.client',
    'src.mcp.manager',
    'src.mcp.tool_registry',
    'src.mcp.config_schema',
    # ── IM 多平台推送 ──
    'src.im', 'src.im.base', 'src.im.message', 'src.im.delivery',
    'src.im.progress_router', 'src.im.push_channel', 'src.im.registry',
    'src.im.targets', 'src.im.config_schema',
    'src.im.plugins',
    'src.im.plugins.feishu', 'src.im.plugins.feishu.adapter',
    'src.im.plugins.feishu.events', 'src.im.plugins.feishu.onboarding',
    'src.im.plugins.feishu.openapi_client', 'src.im.plugins.feishu.push',
    'src.im.plugins.qqbot', 'src.im.plugins.qqbot.adapter',
    'src.im.plugins.qqbot.onboarding', 'src.im.plugins.qqbot.openapi_client',
    'src.im.plugins.qqbot.push',
    'src.im.plugins.wechat', 'src.im.plugins.wechat.adapter',
    'src.im.plugins.wechat.push',
    # ── 其他缺失模块 ──
    'src.assistant.rag_types',
    'src.db.content_cache',
    'src.summarize.errors', 'src.summarize.provider_detector',
    'src.summarize.stub_backend',
    'src.wechat.fav_reader', 'src.wechat.wcdb_fav_reader',
    'src.wechat.sns_client', 'src.wechat.wcdb_sns_reader',
    'src.utils.web_search', 'src.utils.op_logger', 'src.utils.cron',
    'src.trigger', 'src.router',
    # ── 第三方动态导入 ──
    'lark_oapi', 'lark_oapi.ws', 'lark_oapi.event',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'jedi', 'IPython'],
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
    name='wx-assist',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # Windows 不依赖外部 strip 工具，保留 DLL 调试信息。
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
    version=str(_version_file),
)
