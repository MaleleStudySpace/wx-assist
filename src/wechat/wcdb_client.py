"""
Native database client — direct DLL calls, no external HTTP bridge.

Loads wcdb_api.dll via ctypes, applies one-byte compatibility patch, and provides
the same data access as a dedicated HTTP bridge but entirely in-process.
"""
import ctypes as ct
from ctypes import wintypes
import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level DLL singleton: wcdb_api.dll can only be initialized once per
# process.  A second wcdb_init() call causes an access violation crash.
# This flag tracks whether the DLL has been initialized so that a new
# WcdbNativeClient instance can safely reuse an already-loaded DLL.
_dll_initialized = False

# Serializing lock for all DLL calls — the DLL is not thread-safe, so only
# one ctypes call may run at a time.  Using a Lock instead of a
# ThreadPoolExecutor(max_workers=1) avoids queue starvation: with an executor,
# the poll thread submits 10+ tasks/sec and API requests get stuck at the
# back of the queue.  With a lock, an API request only waits for the
# *currently executing* DLL call to finish (typically <100ms), not for the
# entire queue to drain.
_dll_lock = threading.Lock()

# Default timeout (seconds) for acquiring the DLL lock.  Can be overridden per-call.
DLL_CALL_TIMEOUT = 15

PAGE_EXECUTE_READWRITE = 0x40

# ── DRM patch offset configuration ────────────────────────────────────
# Default values target WeChat 4.x.  When WeChat updates and the patch
# offset changes, you can override these via environment variables instead
# of modifying the source:
#
#   WCDB_PATCH_RVA   – hex RVA (relative virtual address) of the patch site
#   WCDB_PATCH_BYTE  – hex byte value expected at patch_site+1 before patching
#
# These are the bytes that the DRM check sets to signal "tampered":
#   mov eax, 2      (B8 02 00 00 00)   → normal / DRM active
#   mov eax, 0      (B8 00 00 00 00)   → patched / DRM bypassed
#
# Example for a future WeChat version:
#   set WCDB_PATCH_RVA=0x6f2a0
#   set WCDB_PATCH_BYTE=0x02

_DEFAULT_PATCH_RVA = 0x6e1f6
_DEFAULT_PATCH_BYTE = 0x02

_env_rva = os.environ.get("WCDB_PATCH_RVA", "").strip()
if _env_rva:
    PATCH_RVA = int(_env_rva, 16)
    logger.info("Using custom WCDB_PATCH_RVA from env: 0x%x", PATCH_RVA)
else:
    PATCH_RVA = _DEFAULT_PATCH_RVA
    logger.debug("Using default WCDB_PATCH_RVA: 0x%x", PATCH_RVA)

_env_byte = os.environ.get("WCDB_PATCH_BYTE", "").strip()
if _env_byte:
    EXPECTED_PATCH_BYTE = int(_env_byte, 16)
    logger.info("Using custom WCDB_PATCH_BYTE from env: 0x%x", EXPECTED_PATCH_BYTE)
else:
    EXPECTED_PATCH_BYTE = _DEFAULT_PATCH_BYTE
    logger.debug("Using default WCDB_PATCH_BYTE: 0x%x", EXPECTED_PATCH_BYTE)

# ── DLL loading ──────────────────────────────────────────────────────

_kernel32 = ct.WinDLL("kernel32", use_last_error=True)


def _apply_drm_patch(dll_handle, dll_path):
    """One-byte DRM patch: mov eax,2 -> mov eax,0 at the configured RVA.

    The patch offset and expected byte are controlled by PATCH_RVA and
    EXPECTED_PATCH_BYTE (see module-level config above).  Override them via
    WCDB_PATCH_RVA / WCDB_PATCH_BYTE environment variables when WeChat
    updates change the patch location.

    Also verifies the DLL hasn't been tampered with beyond our patch.
    """
    # Verify SHA256 baseline
    known_sha = None
    try:
        with open(dll_path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except Exception:
        sha = None

    # Apply the patch
    patch_addr = ct.c_void_p(dll_handle + PATCH_RVA)
    old_protect = wintypes.DWORD()
    _kernel32.VirtualProtect(
        patch_addr, 5, PAGE_EXECUTE_READWRITE, ct.byref(old_protect)
    )

    buf = (ct.c_ubyte * 5).from_address(patch_addr.value)
    if buf[1] == EXPECTED_PATCH_BYTE:
        buf[1] = 0x00
        logger.info("DRM patch applied: RVA 0x%x 02->00", PATCH_RVA)
    elif buf[1] == 0x00:
        logger.info("DRM patch already present")
    else:
        logger.warning(
            "Unexpected byte 0x%02x at patch point — DLL may be tampered",
            buf[1],
        )

    _kernel32.VirtualProtect(
        patch_addr, 5, old_protect, ct.byref(wintypes.DWORD())
    )


def _read_gbk_string(ptr):
    """Read null-terminated string from a raw pointer.

    The WCDB DLL may return GBK or UTF-8 depending on the data source.
    Since all DLL inputs are UTF-8, try UTF-8 first, then fall back to GBK.
    Validates with JSON parse to confirm the correct encoding was chosen.
    """
    if not ptr or ptr.value == 0:
        return ""
    raw = bytearray()
    addr = ptr.value
    for _ in range(500000):
        b = (ct.c_ubyte * 1).from_address(addr)[0]
        if b == 0:
            break
        raw.append(b)
        addr += 1
    # Try UTF-8 first (DLL inputs are always UTF-8), fall back to GBK.
    # GBK decode of UTF-8 bytes can produce valid JSON with garbled Chinese
    # (JSON structural chars are ASCII, identical in both encodings), so the
    # old GBK-first heuristic silently returned mojibake.
    import json as _json
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            _json.loads(text)
            return text
        except (UnicodeDecodeError, _json.JSONDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


# ── Filesystem auto-detection ─────────────────────────────────────────


def _find_dll():
    """Find the bundled wcdb_api.dll."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "lib" / "wcdb_api.dll",
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, Path(sys.executable).resolve().parent / "lib" / "wcdb_api.dll")
        candidates.insert(0, Path(sys._MEIPASS) / "lib" / "wcdb_api.dll")

    for c in candidates:
        if c.exists():
            logger.info("Found wcdb_api.dll at: %s", c)
            return str(c.parent), str(c)

    raise FileNotFoundError(
        "wcdb_api.dll not found. Please place it in the 'lib' folder next to the EXE."
    )


def _find_wxid_and_dbpath(custom_base_dir: str = ""):
    """Auto-detect WeChat wxid and database path from the filesystem.

    If custom_base_dir is provided, scans that directory first.
    Otherwise falls back to Documents\\xwechat_files\\ and Documents\\WeChat Files\\.
    """
    # Collect candidate base directories to scan
    candidates: list[Path] = []

    # 1. Custom directory (highest priority)
    if custom_base_dir:
        custom = Path(custom_base_dir)
        if custom.exists() and custom.is_dir():
            candidates.append(custom)
            logger.info("Scanning custom WECHAT_DATA_DIR: %s", custom)
        else:
            logger.warning(
                "WECHAT_DATA_DIR=%s does not exist or is not a directory — "
                "falling back to auto-detection",
                custom_base_dir,
            )

    # 2. Default auto-detection paths
    documents = Path.home() / "Documents"
    for default_base in (documents / "xwechat_files", documents / "WeChat Files"):
        if default_base not in candidates:
            candidates.append(default_base)

    # Scan candidates in order
    for base in candidates:
        if not base.exists():
            continue
        # Find wxid directories (e.g., wxid_zogepsik3fud12_b6ce)
        try:
            wxid_dirs = sorted(
                [d for d in base.iterdir() if d.is_dir() and d.name.startswith("wxid_")],
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
        except PermissionError:
            logger.warning("Permission denied reading %s — skipping", base)
            continue

        for wxid_dir in wxid_dirs:
            # Verify session.db exists
            session_db = wxid_dir / "db_storage" / "session" / "session.db"
            if session_db.exists():
                wxid = wxid_dir.name
                source = "custom" if base == candidates[0] and custom_base_dir else "auto"
                logger.info("%s-detected: wxid=%s db=%s", source, wxid, str(base))
                return wxid, str(base)

    raise FileNotFoundError(
        "Cannot find WeChat data directory. Make sure WeChat is installed "
        "and you have logged in at least once."
    )


# ── Public API ────────────────────────────────────────────────────────

import sys as _sys


class WcdbNativeClient:
    """Direct WCDB database reader via patched wcdb_api.dll.

    Auto-detects WeChat data paths from the filesystem.
    The DLL is bundled with the EXE in the lib/ directory.
    """

    def __init__(self, dll_dir=None, config_path=None):
        # Resolve DLL
        if dll_dir is not None:
            self._dll_dir = dll_dir
            self._dll_path = os.path.join(dll_dir, "wcdb_api.dll")
        else:
            self._dll_dir, self._dll_path = _find_dll()

        # Resolve config (wxid + dbPath)
        self._config_path = config_path  # may be None — auto-detected
        self._dll = None
        self._handle = 0
        self._config = None
        self._nicknames = {}  # wxid -> display name cache
        self._account_dir = ""     # resolved wxid directory
        self._favorite_db = ""     # favorite.db path
        self._sns_db = ""          # sns.db path

        self._load_config()

    # ── Account directory properties ─────────────────────────────────

    @property
    def account_dir(self) -> str:
        """Resolved wxid directory (e.g. D:/vxchat/xwechat_files/wxid_xxx)."""
        return self._account_dir

    @property
    def favorite_db_path(self) -> str:
        """Resolved favorite.db path."""
        return self._favorite_db

    @property
    def sns_db_path(self) -> str:
        """Resolved sns.db path."""
        return self._sns_db

    # ── Init ──────────────────────────────────────────────────────────

    def _load_config(self):
        # Read wechat_data_dir from config (custom path support)
        custom_dir = ""
        try:
            from src.config import load_config
            config = load_config()
            custom_dir = config.wechat_data_dir
        except Exception:
            # Config not yet available (e.g. during onboarding) — fall back
            # to auto-detection.  load_config may raise if required keys are
            # missing, but _find_wxid_and_dbpath still works without them.
            pass

        wxid, db_path = _find_wxid_and_dbpath(custom_dir)
        self._config = {
            "myWxid": wxid,
            "dbPath": db_path,
        }

    def init(self):
        """Load wcdb_api.dll, patch DRM, and initialize the WCDB engine."""
        os.add_dll_directory(self._dll_dir)
        dll_path = os.path.join(self._dll_dir, "wcdb_api.dll")
        self._dll = ct.CDLL(dll_path)

        # Apply DRM patch (only on first load — Windows caches the DLL)
        global _dll_initialized
        if not _dll_initialized:
            _apply_drm_patch(self._dll._handle, dll_path)
        else:
            logger.info("DRM patch already present")

        # Set up function signatures
        self._dll.InitProtection.argtypes = [ct.c_char_p]
        self._dll.InitProtection.restype = ct.c_int32

        self._dll.wcdb_init.argtypes = []
        self._dll.wcdb_init.restype = ct.c_int32

        self._dll.wcdb_open_account.argtypes = [
            ct.c_char_p, ct.c_char_p, ct.POINTER(ct.c_int64),
        ]
        self._dll.wcdb_open_account.restype = ct.c_int32

        self._dll.wcdb_get_sessions.argtypes = [
            ct.c_int64, ct.POINTER(ct.c_void_p),
        ]
        self._dll.wcdb_get_sessions.restype = ct.c_int32

        self._dll.wcdb_get_messages.argtypes = [
            ct.c_int64, ct.c_char_p, ct.c_int32, ct.c_int32,
            ct.POINTER(ct.c_void_p),
        ]
        self._dll.wcdb_get_messages.restype = ct.c_int32

        self._dll.wcdb_get_display_names.argtypes = [
            ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p),
        ]
        self._dll.wcdb_get_display_names.restype = ct.c_int32

        # Optional: wcdb_get_avatar_urls (may not exist in older DLLs)
        try:
            fn = getattr(self._dll, 'wcdb_get_avatar_urls', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_avatar_urls = fn
            else:
                self._dll.wcdb_get_avatar_urls = None
        except Exception:
            self._dll.wcdb_get_avatar_urls = None

        # Optional: wcdb_get_contacts_compact (may not exist in older DLLs)
        try:
            fn = getattr(self._dll, 'wcdb_get_contacts_compact', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_contacts_compact = fn
            else:
                self._dll.wcdb_get_contacts_compact = None
        except Exception:
            self._dll.wcdb_get_contacts_compact = None

        # Optional: wcdb_get_contact_status (folded/muted state from extra_buffer)
        try:
            fn = getattr(self._dll, 'wcdb_get_contact_status', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_contact_status = fn
            else:
                self._dll.wcdb_get_contact_status = None
        except Exception:
            self._dll.wcdb_get_contact_status = None

        # Optional: wcdb_get_group_members (chatroom member list)
        try:
            fn = getattr(self._dll, 'wcdb_get_group_members', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_group_members = fn
            else:
                self._dll.wcdb_get_group_members = None
        except Exception:
            self._dll.wcdb_get_group_members = None

        self._dll.wcdb_free_string.argtypes = [ct.c_void_p]
        self._dll.wcdb_free_string.restype = None

        # ── SNS interfaces ───────────────────────────────────────────────
        # Optional SNS symbols may be missing in older wcdb_api.dll builds.
        # Use getattr() before assigning self._dll.xxx = None; assigning first
        # would shadow ctypes' lazy function lookup and hide valid exports.
        try:
            fn = getattr(self._dll, 'wcdb_get_sns_timeline', None)
            if fn:
                fn.argtypes = [
                    ct.c_int64, ct.c_int32, ct.c_int32,
                    ct.c_char_p, ct.c_char_p, ct.c_int32, ct.c_int32,
                    ct.POINTER(ct.c_void_p)
                ]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_sns_timeline = fn
            else:
                self._dll.wcdb_get_sns_timeline = None
        except Exception as e:
            logger.debug("wcdb_get_sns_timeline not available: %s", e)
            self._dll.wcdb_get_sns_timeline = None

        try:
            fn = getattr(self._dll, 'wcdb_get_sns_usernames', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_sns_usernames = fn
            else:
                self._dll.wcdb_get_sns_usernames = None
        except Exception as e:
            logger.debug("wcdb_get_sns_usernames not available: %s", e)
            self._dll.wcdb_get_sns_usernames = None

        # SNS protection triggers
        try:
            fn = getattr(self._dll, 'wcdb_install_sns_block_delete_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_install_sns_block_delete_trigger = fn
            else:
                self._dll.wcdb_install_sns_block_delete_trigger = None
        except Exception as e:
            logger.debug("wcdb_install_sns_block_delete_trigger not available: %s", e)
            self._dll.wcdb_install_sns_block_delete_trigger = None

        try:
            fn = getattr(self._dll, 'wcdb_uninstall_sns_block_delete_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_uninstall_sns_block_delete_trigger = fn
            else:
                self._dll.wcdb_uninstall_sns_block_delete_trigger = None
        except Exception as e:
            logger.debug("wcdb_uninstall_sns_block_delete_trigger not available: %s", e)
            self._dll.wcdb_uninstall_sns_block_delete_trigger = None

        try:
            fn = getattr(self._dll, 'wcdb_check_sns_block_delete_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.POINTER(ct.c_int32)]
                fn.restype = ct.c_int32
                self._dll.wcdb_check_sns_block_delete_trigger = fn
            else:
                self._dll.wcdb_check_sns_block_delete_trigger = None
        except Exception as e:
            logger.debug("wcdb_check_sns_block_delete_trigger not available: %s", e)
            self._dll.wcdb_check_sns_block_delete_trigger = None

        # Message anti-revoke triggers (per-session, NOT global like SNS)
        try:
            fn = getattr(self._dll, 'wcdb_install_message_anti_revoke_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_install_message_anti_revoke_trigger = fn
            else:
                self._dll.wcdb_install_message_anti_revoke_trigger = None
        except Exception as e:
            logger.debug("wcdb_install_message_anti_revoke_trigger not available: %s", e)
            self._dll.wcdb_install_message_anti_revoke_trigger = None

        try:
            fn = getattr(self._dll, 'wcdb_uninstall_message_anti_revoke_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_uninstall_message_anti_revoke_trigger = fn
            else:
                self._dll.wcdb_uninstall_message_anti_revoke_trigger = None
        except Exception as e:
            logger.debug("wcdb_uninstall_message_anti_revoke_trigger not available: %s", e)
            self._dll.wcdb_uninstall_message_anti_revoke_trigger = None

        try:
            fn = getattr(self._dll, 'wcdb_check_message_anti_revoke_trigger', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_int32)]
                fn.restype = ct.c_int32
                self._dll.wcdb_check_message_anti_revoke_trigger = fn
            else:
                self._dll.wcdb_check_message_anti_revoke_trigger = None
        except Exception as e:
            logger.debug("wcdb_check_message_anti_revoke_trigger not available: %s", e)
            self._dll.wcdb_check_message_anti_revoke_trigger = None

        try:
            fn = getattr(self._dll, 'wcdb_delete_sns_post', None)
            if fn:
                fn.argtypes = [ct.c_int64, ct.c_char_p, ct.POINTER(ct.c_void_p)]
                fn.restype = ct.c_int32
                self._dll.wcdb_delete_sns_post = fn
            else:
                self._dll.wcdb_delete_sns_post = None
        except Exception as e:
            logger.debug("wcdb_delete_sns_post not available: %s", e)
            self._dll.wcdb_delete_sns_post = None

        # ── Generic SQL query (for fav.db, sns.db, contact.db) ────────────
        # NOTE: Must read the original function via the CDLL's _functions dict
        # BEFORE overwriting the attribute, because setting self._dll.xxx = None
        # shadows the real exported function.
        try:
            _exec_query_fn = None
            # ctypes CDLL stores function pointers in _functions or as raw attrs
            if hasattr(self._dll, '_functions') and 'wcdb_exec_query' in self._dll._functions:
                _exec_query_fn = self._dll._functions['wcdb_exec_query']
            else:
                # Fallback: try __getattr__ before we overwrite
                _exec_query_fn = self._dll.__getattr__('wcdb_exec_query')
        except AttributeError:
            _exec_query_fn = None

        self._dll.wcdb_exec_query = None
        if _exec_query_fn:
            try:
                _exec_query_fn.argtypes = [
                    ct.c_int64, ct.c_char_p, ct.c_char_p, ct.c_char_p,
                    ct.POINTER(ct.c_void_p)
                ]
                _exec_query_fn.restype = ct.c_int32
                self._dll.wcdb_exec_query = _exec_query_fn
                logger.info("wcdb_exec_query configured")
            except Exception as e:
                logger.debug("wcdb_exec_query argtypes setup failed: %s", e)
        else:
            logger.debug("wcdb_exec_query not available in DLL")

        # ── Voice data retrieval (for voice messages) ─────────────────────
        # Check if function exists before trying to configure it
        # Note: Parameter order matches WeFlow's definition:
        # handle, sessionId, createTime, localId, svrId, candidatesJson, &outHex
        try:
            fn = getattr(self._dll, 'wcdb_get_voice_data', None)
            if fn:
                fn.argtypes = [
                    ct.c_int64,        # handle
                    ct.c_char_p,       # sessionId
                    ct.c_int32,        # createTime
                    ct.c_int32,        # localId
                    ct.c_int64,        # svrId (server message ID)
                    ct.c_char_p,       # candidates JSON (array of wxids)
                    ct.POINTER(ct.c_void_p)  # outHex (hex-encoded SILK data)
                ]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_voice_data = fn
                logger.debug("wcdb_get_voice_data configured")
            else:
                logger.debug("wcdb_get_voice_data not available in DLL")
        except Exception as e:
            logger.debug("wcdb_get_voice_data not available: %s", e)

        # Check if batch function exists before trying to configure it
        try:
            fn = getattr(self._dll, 'wcdb_get_voice_data_batch', None)
            if fn:
                fn.argtypes = [
                    ct.c_int64,        # handle
                    ct.c_char_p,       # requests JSON (array of voice query objects)
                    ct.POINTER(ct.c_void_p)  # outJson (array of results)
                ]
                fn.restype = ct.c_int32
                self._dll.wcdb_get_voice_data_batch = fn
                logger.debug("wcdb_get_voice_data_batch configured")
            else:
                logger.debug("wcdb_get_voice_data_batch not available in DLL")
        except Exception as e:
            logger.debug("wcdb_get_voice_data_batch not available: %s", e)

        # Init protection
        resource_path = os.path.dirname(self._dll_dir)
        self._dll.InitProtection(resource_path.encode("utf-8"))

        # Init engine
        if _dll_initialized:
            logger.info("WCDB DLL already initialized — skipping wcdb_init (reuse)")
        else:
            ret = self._dll.wcdb_init()
            if ret != 0:
                raise RuntimeError(f"wcdb_init failed: {ret}")
            _dll_initialized = True
            logger.info("WCDB engine initialized (DRM patched)")

    def open(self):
        """Open the WeChat session.db for the configured account.

        Tries cached keys first.  If they produce 0 sessions (stale key),
        attempts live extraction from the running WeChat process.
        """
        my_wxid = self._config.get("myWxid", "")
        db_base = self._config.get("dbPath", "")
        wxid_base = "_".join(my_wxid.split("_")[:3])

        account_dir = None   # wxid directory (e.g. .../xwechat_files/wxid_xxx)
        session_db = None    # full path to session.db
        base = Path(db_base)
        for entry in base.iterdir():
            if entry.name.startswith(wxid_base):
                account_dir = str(entry)
                candidate = entry / "db_storage" / "session" / "session.db"
                if candidate.exists():
                    session_db = str(candidate)
                    break   # only stop when we actually found session.db

        if not account_dir or not session_db:
            raise RuntimeError(f"session.db not found in {db_base}")

        # wcdb_open_account expects the session.db file path.
        # Passing the account directory results in ret=-3.
        db_paths = [session_db]

        # ── Resolve key, try each source until one yields data ──────
        import os as _os

        for attempt, (key_candidate, source_label) in enumerate(self._key_candidates()):
            logger.info(
                "Trying WCDB key source #%d: %s (len=%d)",
                attempt + 1, source_label,
                len(key_candidate) if key_candidate else 0,
            )

            # Build key variants to try.  The DLL accepts 64-char hex strings
            # (ret=0) but explicitly rejects raw bytes (ret=-3).  Only try hex.
            key_variants = []
            if key_candidate and len(key_candidate) == 64 and all(
                c in "0123456789abcdefABCDEF" for c in key_candidate
            ):
                key_variants.append((key_candidate.encode("utf-8"), "hex"))
            elif key_candidate:
                key_variants.append((key_candidate.encode("utf-8"), "str"))
            # else: empty key → skip (ret=-2 means DLL requires a key)

            for key_bytes, key_fmt in key_variants:
                for db_path in db_paths:
                    path_label = "dir" if db_path == account_dir else "file"
                    handle = ct.c_int64(0)
                    ret = self._dll.wcdb_open_account(
                        db_path.encode("utf-8"),
                        key_bytes,
                        ct.byref(handle),
                    )
                    if ret != 0:
                        logger.info(
                            "wcdb_open_account FAIL (ret=%d) fmt=%s path=%s source=%s",
                            ret, key_fmt, path_label, source_label,
                        )
                        continue

                    self._handle = handle.value

                    # Store account directory and derived DB paths
                    self._account_dir = account_dir
                    self._favorite_db = str(Path(account_dir) / "db_storage" / "favorite" / "favorite.db")
                    self._sns_db = str(Path(account_dir) / "db_storage" / "sns" / "sns.db")

                    # Verify the key actually decrypts data
                    sessions = self.get_sessions()
                    if sessions:
                        session_count = (
                            len(sessions) if isinstance(sessions, list)
                            else len(sessions.get("sessions", sessions))
                        )
                        logger.info(
                            "Key WORKS (source=%s, fmt=%s, path=%s): %d sessions found",
                            source_label, key_fmt, path_label, session_count,
                        )
                        # Persist key for next cold start
                        _os.environ["WCDB_KEY"] = key_candidate
                        self._save_key_to_env(key_candidate)
                        logger.info("Database opened: %s", db_path)
                        try:
                            # Skip nickname cache on DLL reuse — the DLL's internal
                            # state may be stale, causing access violations in
                            # get_contacts().  Per-query resolution works fine.
                            if not _dll_initialized:
                                self._load_nickname_cache()
                            else:
                                logger.info("Skipping nickname cache (DLL reuse)")
                        except Exception:
                            # _load_nickname_cache may crash (access violation) on some
                            # environments, but the database handle is already valid.
                            # Nickname resolution will fall back to per-query lookups.
                            logger.warning("Nickname cache load failed; falling back to per-query resolution")
                        return True

                    # Key didn't work — close and try next variant
                    logger.info(
                        "Key from %s (fmt=%s, path=%s) → 0 sessions",
                        source_label, key_fmt, path_label,
                    )
                    self._close_handle()

            logger.warning(
                "Key from %s failed all formats — trying next source...",
                source_label,
            )

        raise RuntimeError(
            "KEY_MISSING: 密钥未配置。"
            "点击下方「重新获取密钥」按钮，按提示退出并重新登录微信即可。"
        )

    @staticmethod
    def _save_key_to_env(key: str):
        """Persist a working WCDB key to .env for next cold start (file-lock protected)."""
        from src.config import PROJECT_ROOT, write_env_atomic
        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            logger.debug("No .env found for key persistence — key in memory only")
            return

        try:
            write_env_atomic(env_path, {"WCDB_KEY": key})
            logger.debug("Persisted WCDB_KEY to %s", env_path)
        except Exception as e:
            logger.debug("Failed to persist WCDB_KEY: %s", e)

    def _key_candidates(self):
        """Generate (key, label) pairs in priority order.

        The key is captured ONCE during onboarding (WeChat restart flow)
        and persisted to .env / WCDB_KEY.  Live extraction from an
        already-running WeChat is unreliable (the key was loaded at
        startup and the hook may miss it), so we don't try it here.

        As a last resort, tries an empty key — some wcdb_api.dll builds
        can derive the key internally via InitProtection.
        """
        import os as _os

        # Environment variable — persists across runs after onboarding
        env_key = _os.environ.get("WCDB_KEY", "").strip()
        if env_key and len(env_key) == 64:
            yield env_key, "env"

        # Fallback: let the DLL try its own internal key discovery
        yield "", "builtin"

    def _load_nickname_cache(self):
        """Load wxid -> display name mappings from sessions and contacts."""
        sessions = self.get_sessions()
        for s in sessions:
            username = s.get("username", "")
            display = (s.get("displayName") or s.get("nickname") or "").strip()
            if username and display:
                self._nicknames[username] = display

        # Load contacts
        contacts = self.get_contacts()
        for c in contacts:
            username = c.get("userName") or c.get("username") or ""
            nick = (c.get("nickName") or c.get("remark") or c.get("displayName") or "").strip()
            if username and nick:
                self._nicknames[username] = nick

        # Manual overrides from nicknames.json
        nick_file = Path("data/nicknames.json")
        if nick_file.exists():
            try:
                manual = json.loads(nick_file.read_text(encoding="utf-8"))
                for wxid, name in manual.items():
                    if wxid.startswith("_"):
                        continue
                    if name and name.strip():
                        self._nicknames[wxid] = name.strip()
                logger.info("Loaded %d manual nickname overrides", len(manual))
            except Exception as e:
                logger.warning("Failed to load nicknames.json: %s", e)

    # ── Query methods ─────────────────────────────────────────────────

    def _call_with_timeout(self, fn, timeout=None):
        """Run a callable under _dll_lock with timeout protection.

        For DLL calls that don't fit _call_json's return-by-pointer pattern
        (e.g. SNS triggers, voice data).  Returns (result, timed_out) tuple
        where timed_out=True if the lock could not be acquired within timeout.

        The caller's thread is blocked while the DLL call runs, but it only
        waits for the *currently executing* call to finish — not for an
        entire queue of pending calls (as with the old ThreadPoolExecutor).
        """
        if timeout is None:
            timeout = DLL_CALL_TIMEOUT
        caller = threading.current_thread().name
        t0 = time.monotonic()
        acquired = _dll_lock.acquire(timeout=timeout)
        wait_ms = (time.monotonic() - t0) * 1000
        if not acquired:
            logger.error("[DLL-LOCK] %s FAILED to acquire lock after %.0fms — skipping call", caller, wait_ms)
            return None, True
        # DLL-LOCK debug logs commented out — too noisy (10K+/sec), fills disk.
        # Re-enable with LOG_LEVEL=DEBUG if lock contention needs diagnosis.
        # logger.debug("[DLL-LOCK] %s acquired lock (wait=%.0fms), executing...", caller, wait_ms)
        try:
            result = fn()
            exec_ms = (time.monotonic() - t0 - wait_ms / 1000) * 1000
            # logger.debug("[DLL-LOCK] %s call done in %.0fms", caller, exec_ms)
            return result, False
        finally:
            _dll_lock.release()

    def _call_json(self, func, *args, timeout=None):
        """Call a WCDB function that returns a JSON string pointer.

        Acquires _dll_lock to ensure no two DLL calls run simultaneously
        (the DLL is not thread-safe).  The lock timeout prevents a hung
        DLL call from blocking the calling thread forever.

        Unlike the old ThreadPoolExecutor approach, this does NOT queue
        calls — an API request only waits for the currently executing
        DLL call to finish (typically <100ms), not for an entire queue
        of poll-thread submissions to drain.
        """
        if timeout is None:
            timeout = DLL_CALL_TIMEOUT
        caller = threading.current_thread().name
        t0 = time.monotonic()
        acquired = _dll_lock.acquire(timeout=timeout)
        wait_ms = (time.monotonic() - t0) * 1000
        if not acquired:
            logger.error("[DLL-LOCK] %s FAILED to acquire lock for %s after %.0fms — returning None",
                         caller, func.__name__, wait_ms)
            return None
        # DLL-LOCK debug logs commented out — too noisy (10K+/sec), fills disk.
        # Re-enable with LOG_LEVEL=DEBUG if lock contention needs diagnosis.
        # logger.debug("[DLL-LOCK] %s acquired lock for %s (wait=%.0fms), executing...", caller, func.__name__, wait_ms)
        try:
            result = self._call_json_inner(func, *args)
            exec_ms = (time.monotonic() - t0 - wait_ms / 1000) * 1000
            # logger.debug("[DLL-LOCK] %s %s done: wait=%.0fms exec=%.0fms",
            #              caller, func.__name__, wait_ms, exec_ms)
            return result
        finally:
            _dll_lock.release()

    def _call_json_inner(self, func, *args):
        """Actual ctypes call — runs under _dll_lock."""
        out = ct.c_void_p()
        ret = func(*args, ct.byref(out))
        if ret != 0:
            logger.warning("WCDB call %s failed: ret=%d", func.__name__, ret)
            return None
        if not out.value:
            logger.debug("WCDB call %s returned null pointer", func.__name__)
            return {}
        try:
            data = _read_gbk_string(out)
            self._dll.wcdb_free_string(out)
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.debug("JSON parse error: %s", e)
            self._dll.wcdb_free_string(out)
            return {}
        except Exception as e:
            logger.warning("Unexpected error in _call_json for %s: %s",
                           func.__name__, e)
            self._dll.wcdb_free_string(out)
            return {}

    def get_sessions(self, limit=500):
        """Get all chat sessions with metadata."""
        result = self._call_json(self._dll.wcdb_get_sessions, self._handle)
        if result is None:
            logger.warning("wcdb_get_sessions returned None (DLL call failed)")
            return []
        if isinstance(result, list):
            logger.info("Got %d sessions (list)", len(result))
            return result
        if isinstance(result, dict):
            keys = list(result.keys())
            logger.info("Got sessions dict with keys: %s", keys)
            return result.get("sessions", result.get("data", []))
        logger.warning("wcdb_get_sessions returned unexpected type: %s", type(result))
        return []

    def get_messages(self, talker, limit=200, offset=0):
        """Get messages for a specific chat."""
        result = self._call_json(
            self._dll.wcdb_get_messages,
            self._handle,
            talker.encode("utf-8"),
            limit,
            offset,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("messages", result.get("data", []))
        return []

    def get_display_names(self, usernames):
        """Resolve wxids to display names."""
        if not self._handle or not usernames:
            return {}
        username_json = json.dumps(usernames, ensure_ascii=False).encode("utf-8")
        result = self._call_json(
            self._dll.wcdb_get_display_names,
            self._handle,
            username_json,
        )
        if isinstance(result, dict):
            return result.get("names", result)
        return {}

    def get_avatar_urls(self, usernames):
        """Resolve wxids to avatar URLs."""
        if not self._handle or not usernames:
            return {}
        if not self._dll.wcdb_get_avatar_urls:
            return {}
        username_json = json.dumps(usernames, ensure_ascii=False).encode("utf-8")
        result = self._call_json(
            self._dll.wcdb_get_avatar_urls,
            self._handle,
            username_json,
        )
        if isinstance(result, dict):
            return result.get("urls", result)
        return {}

    def get_contacts(self, keyword="", limit=1000):
        """Get contacts list."""
        if not self._dll.wcdb_get_contacts_compact:
            return []
        result = self._call_json(
            self._dll.wcdb_get_contacts_compact,
            self._handle,
            json.dumps([keyword], ensure_ascii=False).encode("utf-8"),
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("contacts", result.get("data", []))
        return []

    def get_contact_status(self, usernames: list[str]) -> dict:
        """Get isFolded/isMuted status for a list of usernames.

        Args:
            usernames: List of username strings (e.g., chatroom IDs)

        Returns:
            Dict mapping username -> {isFolded: bool, isMuted: bool}
        """
        if not self._handle or not usernames:
            return {}
        if not self._dll.wcdb_get_contact_status:
            return {}
        username_json = json.dumps(usernames, ensure_ascii=False).encode("utf-8")
        result = self._call_json(
            self._dll.wcdb_get_contact_status,
            self._handle,
            username_json,
        )
        if isinstance(result, dict):
            return result
        return {}

    def resolve_nickname(self, wxid):
        """Get display name for a wxid from cache."""
        if wxid in self._nicknames:
            return self._nicknames[wxid]
        # Try to look up
        names = self.get_display_names([wxid])
        if wxid in names:
            self._nicknames[wxid] = names[wxid]
            return names[wxid]
        self._nicknames[wxid] = wxid
        return wxid

    def get_group_members(self, chatroom_id: str) -> list[dict]:
        """Get members for a single chatroom.

        Args:
            chatroom_id: Chatroom ID (e.g., "123456@chatroom")

        Returns:
            List of member dicts. Each dict contains at least:
              - username / wxid: member's WeChat ID
              - avatarUrl: avatar URL
        """
        if not self._handle or not chatroom_id:
            return []
        if not self._dll.wcdb_get_group_members:
            logger.debug("wcdb_get_group_members not available in this DLL")
            return []
        # DLL expects plain chatroom ID string, NOT JSON array
        result = self._call_json(
            self._dll.wcdb_get_group_members,
            self._handle,
            chatroom_id.encode("utf-8"),
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("members", "data", "list"):
                if key in result and isinstance(result[key], list):
                    return result[key]
            members = []
            for v in result.values():
                if isinstance(v, list):
                    members.extend(v)
            return members
        return []

    # ── SNS (朋友圈) methods ──────────────────────────────────────────

    def get_sns_timeline(self, limit=20, offset=0, usernames=None,
                         keyword=None, start_time=0, end_time=0):
        """Get Moments timeline from sns.db.

        Args:
            limit: Max posts to return
            offset: Pagination offset
            usernames: Filter by list of users (wxids), or None for all.
                       DLL expects JSON array like ["wxid_xxx"].
            keyword: Search keyword
            start_time: Filter start timestamp (0 = no filter)
            end_time: Filter end timestamp (0 = no filter)
        """
        if not self._dll.wcdb_get_sns_timeline:
            logger.warning("wcdb_get_sns_timeline not available in this DLL")
            return []

        # DLL expects JSON array of usernames (matching WcdbSnsReader convention)
        usernames_json = json.dumps(usernames, ensure_ascii=False) if usernames else ""
        usernames_bytes = usernames_json.encode("utf-8")
        keyword_bytes = (keyword or "").encode("utf-8")

        result = self._call_json(
            self._dll.wcdb_get_sns_timeline,
            self._handle, limit, offset,
            usernames_bytes, keyword_bytes,
            start_time, end_time,
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("timeline", result.get("data", []))
        return []

    def get_sns_usernames(self):
        """Get all usernames who have posted Moments."""
        if not self._dll.wcdb_get_sns_usernames:
            return []
        result = self._call_json(
            self._dll.wcdb_get_sns_usernames, self._handle
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("usernames", [])
        return []

    def install_sns_block_delete_trigger(self):
        """Install trigger to block DELETE on SnsTimeLine table.

        Returns:
            dict: {success: bool, already_installed: bool, error: str}
        """
        if not self._dll.wcdb_install_sns_block_delete_trigger:
            return {"success": False, "error": "DLL does not support this function"}

        def _do():
            out_error = ct.c_void_p()
            ret = self._dll.wcdb_install_sns_block_delete_trigger(
                self._handle, ct.byref(out_error)
            )
            if ret == 0:
                return {"success": True, "already_installed": False}
            if ret == 1:
                return {"success": True, "already_installed": True}
            err_msg = ""
            if out_error.value:
                try:
                    err_msg = _read_gbk_string(out_error)
                    self._dll.wcdb_free_string(out_error)
                except Exception:
                    pass
            return {"success": False, "error": err_msg or f"install failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def uninstall_sns_block_delete_trigger(self):
        """Uninstall the SNS block-delete trigger."""
        if not self._dll.wcdb_uninstall_sns_block_delete_trigger:
            return {"success": False, "error": "DLL does not support this function"}

        def _do():
            out_error = ct.c_void_p()
            ret = self._dll.wcdb_uninstall_sns_block_delete_trigger(
                self._handle, ct.byref(out_error)
            )
            if ret == 0:
                return {"success": True}
            err_msg = ""
            if out_error.value:
                try:
                    err_msg = _read_gbk_string(out_error)
                    self._dll.wcdb_free_string(out_error)
                except Exception:
                    pass
            return {"success": False, "error": err_msg or f"uninstall failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def check_sns_block_delete_trigger(self):
        """Check if the SNS block-delete trigger is installed.

        Returns:
            dict: {success: bool, installed: bool}
        """
        if not self._dll.wcdb_check_sns_block_delete_trigger:
            return {"success": False, "error": "DLL does not support this function"}

        def _do():
            out_installed = ct.c_int32(0)
            ret = self._dll.wcdb_check_sns_block_delete_trigger(
                self._handle, ct.byref(out_installed)
            )
            if ret == 0:
                return {"success": True, "installed": bool(out_installed.value)}
            return {"success": False, "error": f"check failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def install_message_anti_revoke_trigger(self, session_id: str):
        """Install trigger to block message revocation for a specific session.

        Args:
            session_id: The chat session ID (e.g. "wxid_xxx" or "xxx@chatroom")

        Returns:
            dict: {success: bool, already_installed: bool, error: str}
        """
        if not self._dll.wcdb_install_message_anti_revoke_trigger:
            return {"success": False, "error": "DLL does not support this function"}
        if not session_id or not session_id.strip():
            return {"success": False, "error": "session_id cannot be empty"}

        sid = session_id.strip().encode("utf-8")

        def _do():
            out_error = ct.c_void_p()
            ret = self._dll.wcdb_install_message_anti_revoke_trigger(
                self._handle, sid, ct.byref(out_error)
            )
            if ret == 0:
                return {"success": True, "already_installed": False}
            if ret == 1:
                return {"success": True, "already_installed": True}
            err_msg = ""
            if out_error.value:
                try:
                    err_msg = _read_gbk_string(out_error)
                    self._dll.wcdb_free_string(out_error)
                except Exception:
                    pass
            return {"success": False, "error": err_msg or f"install failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def uninstall_message_anti_revoke_trigger(self, session_id: str):
        """Uninstall the message anti-revoke trigger for a specific session.

        Args:
            session_id: The chat session ID
        """
        if not self._dll.wcdb_uninstall_message_anti_revoke_trigger:
            return {"success": False, "error": "DLL does not support this function"}
        if not session_id or not session_id.strip():
            return {"success": False, "error": "session_id cannot be empty"}

        sid = session_id.strip().encode("utf-8")

        def _do():
            out_error = ct.c_void_p()
            ret = self._dll.wcdb_uninstall_message_anti_revoke_trigger(
                self._handle, sid, ct.byref(out_error)
            )
            if ret == 0:
                return {"success": True}
            err_msg = ""
            if out_error.value:
                try:
                    err_msg = _read_gbk_string(out_error)
                    self._dll.wcdb_free_string(out_error)
                except Exception:
                    pass
            return {"success": False, "error": err_msg or f"uninstall failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def check_message_anti_revoke_trigger(self, session_id: str):
        """Check if the message anti-revoke trigger is installed for a specific session.

        Args:
            session_id: The chat session ID

        Returns:
            dict: {success: bool, installed: bool}
        """
        if not self._dll.wcdb_check_message_anti_revoke_trigger:
            return {"success": False, "error": "DLL does not support this function"}
        if not session_id or not session_id.strip():
            return {"success": False, "error": "session_id cannot be empty"}

        sid = session_id.strip().encode("utf-8")

        def _do():
            out_installed = ct.c_int32(0)
            ret = self._dll.wcdb_check_message_anti_revoke_trigger(
                self._handle, sid, ct.byref(out_installed)
            )
            if ret == 0:
                return {"success": True, "installed": bool(out_installed.value)}
            return {"success": False, "error": f"check failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    def delete_sns_post(self, post_id):
        """Delete a SNS post by postId (bypasses the block-delete trigger).

        Args:
            post_id: The SnsTimeLine post ID to delete
        """
        if not self._dll.wcdb_delete_sns_post:
            return {"success": False, "error": "DLL does not support this function"}

        def _do():
            out_error = ct.c_void_p()
            ret = self._dll.wcdb_delete_sns_post(
                self._handle, post_id.encode("utf-8"), ct.byref(out_error)
            )
            if ret == 0:
                return {"success": True}
            err_msg = ""
            if out_error.value:
                try:
                    err_msg = _read_gbk_string(out_error)
                    self._dll.wcdb_free_string(out_error)
                except Exception:
                    pass
            return {"success": False, "error": err_msg or f"delete failed (ret={ret})"}

        result, timed_out = self._call_with_timeout(_do)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    # ── Generic SQL query (for fav.db etc.) ───────────────────────────

    def exec_query(self, kind, db_path="", sql=""):
        """Execute a SQL query via wcdb_exec_query.

        Args:
            kind: Database kind (e.g., "fav", "sns", "contact", "session")
            db_path: Specific database file path (empty for default)
            sql: SQL query string

        Returns:
            list: Query result rows
        """
        if not self._dll.wcdb_exec_query:
            logger.warning("wcdb_exec_query not available in this DLL")
            return []

        result = self._call_json(
            self._dll.wcdb_exec_query,
            self._handle,
            kind.encode("utf-8"),
            db_path.encode("utf-8"),
            sql.encode("utf-8"),
        )
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("rows", result.get("data", []))
        return []

    def get_favorites(self, limit=200, offset=0):
        """Read favorites from fav.db via wcdb_exec_query.

        Uses the generic query interface to read FavItem table.
        """
        sql = f"SELECT * FROM FavItem ORDER BY createTime DESC LIMIT {limit} OFFSET {offset}"
        return self.exec_query("fav", "", sql)

    # ── Voice data retrieval ─────────────────────────────────────────

    def get_voice_data(self, session_id, create_time, local_id, svr_id, candidates):
        """Retrieve voice (SILK) data for a voice message.

        Args:
            session_id: Chat session ID (wxid or chatroom ID)
            create_time: Message creation timestamp (int)
            local_id: Message local_id (int)
            svr_id: Message server_id (int or string)
            candidates: List of candidate wxids (e.g., [sender_wxid, my_wxid])

        Returns:
            dict: {"success": True, "hex": "<hex_encoded_silk_data>"} on success
                  {"success": False, "error": "<error_message>"} on failure
        """
        if not self._dll.wcdb_get_voice_data:
            return {"success": False, "error": "DLL does not support voice data retrieval"}

        # Prepare candidates JSON
        candidates_json = json.dumps(candidates).encode("utf-8")
        svr_id_int = int(svr_id) if svr_id else 0

        def _do():
            out_ptr = ct.c_void_p()
            try:
                ret = self._dll.wcdb_get_voice_data(
                    self._handle,
                    session_id.encode("utf-8") if session_id else b"",
                    int(create_time),
                    int(local_id),
                    ct.c_int64(svr_id_int),
                    candidates_json,
                    ct.byref(out_ptr)
                )
            except Exception as e:
                return {"success": False, "error": str(e)}

            if ret != 0:
                return {"success": False, "error": f"DLL call failed (ret={ret})"}

            if not out_ptr.value:
                return {"success": False, "error": "No voice data returned"}

            try:
                hex_str = _read_gbk_string(out_ptr)
                self._dll.wcdb_free_string(out_ptr)
            except Exception as e:
                return {"success": False, "error": f"Failed to read response: {e}"}

            if not hex_str:
                return {"success": False, "error": "Empty hex data"}

            return {"success": True, "hex": hex_str}

        result, timed_out = self._call_with_timeout(_do, timeout=30)
        if timed_out:
            return {"success": False, "error": "DLL call timed out"}
        return result

    # ── Cleanup ───────────────────────────────────────────────────────

    def _close_handle(self):
        """Close current DB handle safely (no-op if already closed)."""
        if self._handle:
            try:
                wcdb_close = self._dll.wcdb_close_account
                wcdb_close.argtypes = [ct.c_int64]
                wcdb_close.restype = ct.c_int32
                wcdb_close(self._handle)
            except Exception:
                pass
            self._handle = 0

    def close(self):
        self._close_handle()

    def reopen(self):
        """Close the current DB handle and re-open without re-initing the DLL.

        Safe to call after stop/start cycles.  The DLL is loaded once per
        process and cannot be safely re-initialized (wcdb_init crashes on
        second call).  This method reuses the existing DLL reference and
        only re-opens the database handle.
        """
        self._close_handle()
        if self._dll is not None:
            self.open()
        else:
            # DLL not loaded yet — do a full init + open
            self.init()
            self.open()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
