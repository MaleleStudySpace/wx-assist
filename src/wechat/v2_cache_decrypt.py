"""WeChat V2 cache image transformation.

Transforms V2-format .dat cache files found in WeChat's local storage
(<wxid_dir>/business/favorite/{data,mid,thumb}/).

V2 file structure:
  [6B magic: 07 08 V2 08 07] [4B aes_size LE] [4B xor_size LE] [1B padding]
  [aligned_aes_size bytes AES-128-ECB ciphertext]
  [raw_data (unencrypted)]
  [xor_size bytes XOR-encrypted tail]

AES key derivation (Windows):
  aes_key = MD5(str(uin) + "wxid_" + wxid_base).hexdigest()[:16]
  xor_key = uin & 0xFF

  where uin is extracted from kvcomm filenames:
    $APPDATA/Tencent/xwechat/net/kvcomm/key_0_{uin}_*_input.statistic

AES section alignment:
  aligned_aes_size = aes_size + (16 - aes_size % 16)  if aes_size % 16 != 0
  aligned_aes_size = aes_size + 16                      if aes_size % 16 == 0
  (PKCS7 always adds at least one full padding block)
"""

import hashlib
import logging
import os
import re
import shutil
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


V2_MAGIC = b'\x07\x08V2\x08\x07'
V1_MAGIC = b'\x07\x08V1\x08\x07'
V1_FIXED_KEY = b'cfcd208495d565ef'  # md5("0")[:16]

# JPEG/PNG/WebP magic for validation
IMAGE_MAGIC = {
    'jpg': (b'\xFF\xD8\xFF',),
    'png': (b'\x89PNG',),
    'gif': (b'GIF8',),
    'webp': (b'RIFF',),  # need secondary check for WEBP
    'wxgf': (b'wxgf',),  # WeChat HEVC container
    'bmp': (b'BM',),
}


def _read_dat_file(file_path, max_retries=3):
    """Read a .dat file with retry and copy-to-temp fallback for locked files.

    WeChat may lock .dat files while running, causing PermissionError.
    This helper retries with increasing delay, then falls back to
    copying the file to a temp location before reading.
    """
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as f:
                return f.read()
        except PermissionError:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            # Fallback: copy to temp then read
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.dat') as tmp:
                    shutil.copy2(file_path, tmp.name)
                    with open(tmp.name, 'rb') as f:
                        data = f.read()
                    os.unlink(tmp.name)
                    return data
            except Exception:
                return None
        except FileNotFoundError:
            return None
    return None


def _aligned_aes_block_size(aes_size: int) -> int:
    """PKCS7-aligned AES section size.

    Same formula as wechat-decrypt/decode_image.py:aligned_aes_block_size().
    When aes_size is already a multiple of 16, PKCS7 still adds a full
    16-byte padding block, so we add 16.
    """
    if aes_size % 16:
        return aes_size + (16 - aes_size % 16)
    return aes_size + 16


def _detect_image_format(header: bytes) -> str:
    """Detect image format from decrypted header bytes."""
    if len(header) < 4:
        return 'bin'
    if header[:3] == b'\xFF\xD8\xFF':
        return 'jpg'
    if header[:4] == b'\x89PNG':
        return 'png'
    if header[:3] == b'GIF':
        return 'gif'
    if header[:4] == b'RIFF' and len(header) >= 12 and header[8:12] == b'WEBP':
        return 'webp'
    if header[:4] == b'wxgf':
        return 'hevc'
    if header[:2] == b'BM':
        return 'bmp'
    return 'bin'


def decrypt_v2_cache(data: bytes, aes_key: str, xor_key: int) -> Optional[bytes]:
    """Decrypt a V2-format cache file from raw bytes.

    Args:
        data: Raw file bytes (including V2 header).
        aes_key: 16-char ASCII AES key string.
        xor_key: Single-byte XOR key (0-255).

    Returns:
        Decrypted image bytes, or None on failure.
    """
    if len(data) < 15:
        return None

    sig = data[:6]
    if sig not in (V2_MAGIC, V1_MAGIC):
        return None

    # V1 uses fixed key
    if sig == V1_MAGIC:
        aes_key = V1_FIXED_KEY.decode('ascii')

    aes_size, xor_size = struct.unpack_from('<LL', data, 6)
    aligned = _aligned_aes_block_size(aes_size)

    offset = 15
    if offset + aligned > len(data):
        return None

    # AES-128-ECB decrypt
    try:
        from Crypto.Cipher import AES
        from Crypto.Util import Padding

        key_bytes = aes_key.encode('ascii')[:16]
        if len(key_bytes) < 16:
            return None

        cipher = AES.new(key_bytes, AES.MODE_ECB)
        aes_ciphertext = data[offset:offset + aligned]
        decrypted_aes = Padding.unpad(cipher.decrypt(aes_ciphertext), AES.block_size)
    except (ValueError, KeyError):
        return None

    offset += aligned

    # Raw (unencrypted) middle section
    raw_end = len(data) - xor_size
    raw_data = data[offset:raw_end] if offset < raw_end else b''

    # XOR tail section
    xor_data = bytes(b ^ xor_key for b in data[raw_end:])

    result = decrypted_aes + raw_data + xor_data

    # Validate decryption succeeded
    fmt = _detect_image_format(result[:16])

    # Check for video formats (MP4, WebM, etc.) — not in _detect_image_format
    is_video = (len(result) >= 8 and result[4:8] == b'ftyp')  # MP4: ftyp box

    if fmt == 'bin' and not is_video:
        return None  # Key is likely wrong

    # Additional tail validation for JPEG/PNG (skip for video)
    if not is_video:
        if fmt == 'jpg' and len(result) >= 2 and result[-2:] != b'\xFF\xD9':
            return None
        if fmt == 'png' and b'IEND' not in result[-12:]:
            return None

    return result


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def extract_uin_from_kvcomm() -> Optional[int]:
    """Extract WeChat uin from kvcomm statistic filenames.

    Scans $APPDATA/Tencent/xwechat/net/kvcomm/ (and net_1/) for files
    containing uin patterns, then validates via XOR key voting against
    actual V2 .dat file tail bytes.

    The uin can appear in multiple filename patterns:
      key_0_{uin}_..._input.statistic       (appid=0, uin in 2nd slot)
      key_{uin}_{appid}_..._input.statistic (uin in 1st slot)
      monitordata_{uin}_...                 (uin in 1st slot)
      monitordata_0_...                     (no uin)

    Returns the validated uin as int, or None if not found.
    """
    appdata = os.environ.get('APPDATA', '')
    if not appdata:
        return None

    # Search both net/ and net_1/ directories
    candidate_dirs = [
        Path(appdata) / 'Tencent' / 'xwechat' / 'net' / 'kvcomm',
        Path(appdata) / 'Tencent' / 'xwechat' / 'net_1' / 'kvcomm',
    ]

    # Collect all candidate uins from filenames
    # Pattern 1: key_0_{uin}_... or monitordata_0_... → uin is 2nd number
    pattern_uin_slot2 = re.compile(r'key_0_(\d+)_')
    # Pattern 2: key_{uin}_{appid}_... → uin is 1st number (must be > 100000000)
    pattern_uin_slot1 = re.compile(r'key_(\d{9,11})_\d+_')
    # Pattern 3: monitordata_{uin}_... → uin is 1st number
    pattern_monitor_uin = re.compile(r'monitordata_(\d{9,11})_')

    candidate_uins = set()

    for kvcomm_dir in candidate_dirs:
        if not kvcomm_dir.exists():
            continue
        for f in kvcomm_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            # Slot 2: key_0_{uin}_*
            for m in pattern_uin_slot2.finditer(name):
                val = int(m.group(1))
                if val > 100000000:  # Plausible uin
                    candidate_uins.add(val)
            # Slot 1: key_{uin}_{appid}_*
            for m in pattern_uin_slot1.finditer(name):
                candidate_uins.add(int(m.group(1)))
            # Monitor: monitordata_{uin}_*
            for m in pattern_monitor_uin.finditer(name):
                candidate_uins.add(int(m.group(1)))

    if not candidate_uins:
        return None

    # Validate: the correct uin's xor_key = uin & 0xFF should match
    # the majority vote from V2 .dat file tail bytes (JPEG ends with FF D9)
    fav_cache_dir = _find_fav_cache_dir()
    xor_from_files = _vote_xor_key_from_dat_files(fav_cache_dir)

    if xor_from_files is not None:
        # Pick the uin whose xor_key matches the file-derived value
        for uin in candidate_uins:
            if (uin & 0xFF) == xor_from_files:
                return uin

    # Fallback: return the largest plausible uin (often correct for multi-account)
    return max(candidate_uins)


def _find_fav_cache_dir() -> Optional[Path]:
    """Find the favorite cache directory from WECHAT_DATA_DIR."""
    data_dir = os.getenv('WECHAT_DATA_DIR', '')
    if not data_dir:
        return None
    data_path = Path(data_dir)
    if not data_path.exists():
        return None

    # Find the most recently accessed wxid directory
    wxid_dirs = sorted(
        [d for d in data_path.iterdir()
         if d.is_dir() and d.name.startswith('wxid_')],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if wxid_dirs:
        fav_dir = wxid_dirs[0] / 'business' / 'favorite'
        if fav_dir.exists():
            return fav_dir
    return None


def _vote_xor_key_from_dat_files(fav_dir: Optional[Path],
                                  sample: int = 32,
                                  min_samples: int = 3) -> Optional[int]:
    """Derive XOR key from V2 .dat file tail bytes (JPEG EOI = FF D9).

    For each V2 .dat file, the last 2 bytes XORed with 0xFF, 0xD9
    should yield the same xor_key if the file is a JPEG thumbnail.
    Takes majority vote across samples.
    """
    if not fav_dir or not fav_dir.exists():
        return None

    v2_magic = V2_MAGIC
    tail_votes: dict[int, int] = {}

    # Check thumb/ directory (most likely JPEG thumbnails)
    thumb_dir = fav_dir / 'thumb'
    if not thumb_dir.exists():
        # Try data/ as fallback
        thumb_dir = fav_dir / 'data'

    if not thumb_dir.exists():
        return None

    count = 0
    for f in thumb_dir.rglob('*'):
        if not f.is_file() or f.stat().st_size < 20:
            continue
        try:
            raw = _read_dat_file(str(f))
            if raw and len(raw) >= 8:
                head = raw[:6]
                tail = raw[-2:]
            else:
                continue
            if head == v2_magic and len(tail) == 2:
                xor_key = tail[0] ^ 0xFF
                check = tail[1] ^ 0xD9
                if xor_key == check:
                    tail_votes[xor_key] = tail_votes.get(xor_key, 0) + 1
                    count += 1
        except (OSError, ValueError):
            continue
        if count >= sample:
            break

    if not tail_votes:
        return None

    return max(tail_votes, key=tail_votes.get)


def derive_v2_keys(uin: int, wxid: str) -> tuple[str, int]:
    """Derive V2 AES key and XOR key from uin and wxid.

    Formula (Windows WeChat 4.x):
      aes_key = MD5(str(uin) + "wxid_" + wxid_base).hexdigest()[:16]
      xor_key = uin & 0xFF

    where wxid_base = wxid without the trailing _XXXX hex suffix.
    e.g. "wxid_abc123def_89ce" → "wxid_abc123def"

    Returns:
        (aes_key_str, xor_key_int)
    """
    # Strip trailing _XXXX suffix from wxid
    wxid_base = wxid.rsplit('_', 1)[0] if '_' in wxid else wxid

    # MD5(str(uin) + "wxid_" + wxid_base) - wxid_base already has "wxid_" prefix!
    key_input = f'{uin}{wxid_base}'
    aes_key = hashlib.md5(key_input.encode()).hexdigest()[:16]
    xor_key = uin & 0xFF

    return aes_key, xor_key


# ---------------------------------------------------------------------------
# Cache Manager (singleton)
# ---------------------------------------------------------------------------

class V2CacheManager:
    """Manages V2 decryption keys for multiple WeChat accounts.

    Thread-safe singleton. Keys are auto-derived from kvcomm filenames
    on first access and cached in memory.
    """

    _instance = None
    _lock = threading.Lock()
    _detect_lock = threading.Lock()  # protects _detect_keys() from concurrent execution

    def __init__(self, wechat_data_dir: str):
        self._data_dir = Path(wechat_data_dir)
        self._keys: dict[str, tuple[str, int]] = {}
        self._uin: Optional[int] = None
        self._detected = False

    @classmethod
    def get_instance(cls, wechat_data_dir: str = '') -> 'V2CacheManager':
        with cls._lock:
            data_dir = wechat_data_dir or os.getenv('WECHAT_DATA_DIR', '')
            if cls._instance is None:
                cls._instance = cls(data_dir)
            elif data_dir and not cls._instance._data_dir_valid():
                # Rebuild if existing instance was created with an invalid path
                # (e.g. WECHAT_DATA_DIR wasn't set yet on first call)
                logger.info("V2CacheManager: rebuilding with valid data_dir=%s (old was %s)",
                            data_dir, cls._instance._data_dir)
                # Clean up old instance state before replacing
                if cls._instance is not None:
                    cls._instance._keys = {}
                    cls._instance._detected = False
                cls._instance = cls(data_dir)
            return cls._instance

    def _data_dir_valid(self) -> bool:
        """Check if the current data_dir points to an existing directory."""
        try:
            return self._data_dir.exists() and self._data_dir.is_dir()
        except Exception:
            return False

    def _detect_keys(self):
        """Auto-detect uin from kvcomm and derive keys for all accounts.

        Thread-safe: uses _detect_lock to prevent concurrent key detection.
        Double-checks _detected flag after acquiring lock.
        """
        if self._detected:
            return
        with self._detect_lock:
            if self._detected:  # double-check after acquiring lock
                return

            self._detected = True
            uin = extract_uin_from_kvcomm()
            if uin is None:
                return

            self._uin = uin

            # Derive keys for each wxid directory
            if self._data_dir.exists():
                for d in self._data_dir.iterdir():
                    if d.is_dir() and d.name.startswith('wxid_'):
                        aes_key, xor_key = derive_v2_keys(uin, d.name)
                        self._keys[d.name] = (aes_key, xor_key)

    def get_keys(self, wxid: str) -> Optional[tuple[str, int]]:
        """Get (aes_key, xor_key) for the given wxid.

        Returns None if keys could not be derived.
        """
        self._detect_keys()

        # Direct match
        if wxid in self._keys:
            return self._keys[wxid]

        # Try matching wxid directory (wxid may not include suffix)
        for full_wxid, keys in self._keys.items():
            if full_wxid.startswith(wxid):
                return keys

        return None

    @property
    def uin(self) -> Optional[int]:
        self._detect_keys()
        return self._uin

    def decrypt_fav_image(self, local_id: int, wxid: str,
                          size: str = 'original',
                          fullmd5: str = None,
                          fullsize: int = None) -> Optional[bytes]:
        """Find and decrypt a favorite image from local cache.

        Resolution strategy (in priority order):
        1. fullmd5 + hardlink.db → precise .dat path (best)
        2. <fullsize>/<thumbfullsize> + file size matching (fallback)

        Args:
            local_id: Favorite item's local_id from favorite.db
            wxid: WeChat user ID (full with suffix)
            size: "original" (data/), "mid", or "thumb"
            fullmd5: Explicit fullmd5 (from chat record dataitem, overrides XML lookup)
            fullsize: Explicit fullsize (from chat record dataitem)

        Returns:
            Decrypted image bytes, or None on failure.
        """
        self._detect_keys()
        keys = self.get_keys(wxid)
        if not keys:
            return None

        aes_key, xor_key = keys

        # ── Strategy 1: Use provided fullmd5 OR extract from XML ──────────
        file_path = None
        md5_info = None

        # If fullmd5 is explicitly provided (from chat records), use it directly
        if fullmd5:
            md5_info = fullmd5
            resolved = self._resolve_hardlink(md5_info, wxid)
            if resolved and Path(resolved).exists() and Path(resolved).stat().st_size > 0:
                file_path = resolved

        # Fallback: extract from XML if not provided
        if not file_path and not fullmd5:
            md5_info = self._get_fav_md5_from_xml(local_id, size)
            if md5_info:
                resolved = self._resolve_hardlink(md5_info, wxid)
                if resolved and Path(resolved).exists() and Path(resolved).stat().st_size > 0:
                    file_path = resolved

        # ── Strategy 2: size matching fallback ─────────────────────────
        if not file_path:
            # Use provided fullsize, or extract from XML
            target_size = fullsize if fullsize else None
            if not target_size:
                # Try to get size from XML
                xml_size = self._get_fav_size_from_xml(local_id, size)
                target_size = xml_size
            if target_size:
                file_path = self._find_by_size(local_id, wxid, size, target_size,
                                               has_fullmd5=bool(fullmd5))

        if not file_path:
            return None

        try:
            data = _read_dat_file(file_path)
            if data is None:
                return None

            # Check if it's V2 format
            if data[:6] == V2_MAGIC or data[:6] == V1_MAGIC:
                return decrypt_v2_cache(data, aes_key, xor_key)
            else:
                # Legacy format - return raw
                return data

        except Exception:
            return None

    def decrypt_chat_image(self, fullmd5: str, talker: str,
                           create_time: int = 0,
                           wxid: str = "",
                           size: str = "original") -> Optional[bytes]:
        """Find and decrypt a chat image from MsgAttach directory.

        Chat images are stored as V2-encrypted .dat files under:
          {accountDir}/msg/attach/{sessionMd5}/{yyyy-MM}/Img/{md5}.dat

        Resolution strategy:
          1. Compute sessionMd5 from talker (chatroom or wxid)
          2. Determine year-month from create_time
          3. Look for {fullmd5}.dat (original) or {fullmd5}_t.dat (thumb)
          4. Decrypt using V2 cache keys

        Args:
            fullmd5: Image MD5 from packed_info_data
            talker: Session ID (chatroom or wxid)
            create_time: Message timestamp (used to find year-month directory)
            wxid: WeChat user ID
            size: "original" or "thumb"

        Returns:
            Decrypted image bytes, or None on failure.
        """
        self._detect_keys()

        if not wxid:
            data_dir = os.getenv("WECHAT_DATA_DIR", "")
            if data_dir:
                wxid_dirs = sorted(
                    [d for d in Path(data_dir).iterdir()
                     if d.is_dir() and d.name.startswith("wxid_")],
                    key=lambda d: d.stat().st_mtime, reverse=True,
                )
                if wxid_dirs:
                    wxid = wxid_dirs[0].name

        if not wxid:
            return None

        keys = self.get_keys(wxid)
        if not keys:
            return None

        aes_key, xor_key = keys

        # Compute session MD5 for directory lookup
        session_md5 = hashlib.md5(talker.encode("utf-8")).hexdigest()

        # Determine year-month directories to search
        search_dirs = []
        if create_time and create_time > 0:
            from datetime import datetime
            try:
                dt = datetime.fromtimestamp(create_time)
                ym = dt.strftime("%Y-%m")
                search_dirs.append(ym)
                # Also check adjacent months (messages near month boundary)
                if dt.day <= 1:
                    prev = datetime.fromtimestamp(create_time - 86400)
                    search_dirs.append(prev.strftime("%Y-%m"))
                elif dt.day >= 28:
                    nxt = datetime.fromtimestamp(create_time + 86400)
                    search_dirs.append(nxt.strftime("%Y-%m"))
            except Exception:
                pass

        # Build base attach directory
        attach_base = self._data_dir / wxid / "msg" / "attach" / session_md5
        if not attach_base.exists():
            # Fallback: try without session MD5 (some images may be in other dirs)
            return None

        # Determine filename suffixes to try
        if size == "thumb":
            suffixes = ["_t", ""]  # Prefer thumbnail, fall back to full
        else:
            # original: try _h (high-res), then full, then _t (fallback)
            suffixes = ["_h", "", "_t"]

        # If no create_time, scan all available year-month dirs
        if not search_dirs:
            try:
                search_dirs = sorted([d.name for d in attach_base.iterdir() if d.is_dir()])
            except Exception:
                return None

        # Search for the .dat file
        # Returns (decrypted_data, is_wxgf) or None
        def _try_decrypt(dat_path):
            if not dat_path.exists() or dat_path.stat().st_size == 0:
                return None
            try:
                data = _read_dat_file(str(dat_path))
                if data is None:
                    return None
                if data[:6] == V2_MAGIC or data[:6] == V1_MAGIC:
                    result = decrypt_v2_cache(data, aes_key, xor_key)
                    if result:
                        return result
                else:
                    # Legacy/unencrypted format
                    return data
            except Exception:
                pass
            return None

        for ym in search_dirs:
            img_dir = attach_base / ym / "Img"
            if not img_dir.exists():
                continue

            for suffix in suffixes:
                dat_path = img_dir / f"{fullmd5}{suffix}.dat"
                result = _try_decrypt(dat_path)
                if result:
                    # wxgf (WeChat HEVC) can't be displayed in browser
                    # When size=original, skip wxgf and try next suffix (_h -> full -> _t)
                    # When size=thumb, also skip wxgf and try next
                    if result[:4] == b"wxgf" and suffix != "_t":
                        # Skip wxgf, try next suffix in the list
                        continue
                    return result

            # If all non-wxgf suffixes failed, return wxgf original as last resort
            for suffix in ["", "_h"]:
                dat_path = img_dir / f"{fullmd5}{suffix}.dat"
                result = _try_decrypt(dat_path)
                if result:
                    return result

        # Fallback: scan all Img directories if targeted search failed
        try:
            for ym_dir in sorted(attach_base.iterdir()):
                if not ym_dir.is_dir():
                    continue
                img_dir = ym_dir / "Img"
                if not img_dir.exists():
                    continue
                for suffix in suffixes:
                    dat_path = img_dir / f"{fullmd5}{suffix}.dat"
                    result = _try_decrypt(dat_path)
                    if result:
                        if result[:4] == b"wxgf" and suffix != "_t":
                            continue
                        return result

                # Last resort: return wxgf if nothing else worked
                for suffix in ["", "_h"]:
                    dat_path = img_dir / f"{fullmd5}{suffix}.dat"
                    result = _try_decrypt(dat_path)
                    if result:
                        return result
        except Exception:
            pass

        return None

    def _get_fav_md5_from_xml(self, local_id: int, size_type: str) -> Optional[str]:
        """Extract image/video MD5 from favorite XML content for hardlink lookup.

        For original images, uses <fullmd5>.
        For thumbnails, uses <thumbfullmd5>.

        Fallback: try get_items(500) first, if JSON parse error, fall back to get_by_id.
        """
        try:
            import re as _re

            if not _readers:
                from src.wechat.v2_cache_decrypt import _get_reader_for_wxid
                data_dir = os.getenv('WECHAT_DATA_DIR', '')
                if not data_dir:
                    return None
                data_path = Path(data_dir)
                for d in data_path.iterdir():
                    if d.is_dir() and d.name.startswith('wxid_'):
                        _get_reader_for_wxid(d.name)
                        break

            # Search across all favorite types (not just type=2 images)
            # because videos (type=4) and chat records (type=14) also have fullmd5
            for check_wxid, reader in _readers.items():
                try:
                    if not hasattr(reader, 'get_items'):
                        continue
                    # Strategy 1: batch query (efficient but may trigger JSON truncation)
                    items = reader.get_items(limit=500)
                    for item in items:
                        if str(item.get('local_id')) == str(local_id):
                            content = item.get('content_raw', '')
                            if size_type in ('thumb', 'thumbnail'):
                                match = _re.search(r'<thumbfullmd5>([a-f0-9]{32})</thumbfullmd5>', content)
                            else:
                                match = _re.search(r'<fullmd5>([a-f0-9]{32})</fullmd5>', content)
                            if match:
                                return match.group(1)
                except Exception:
                    # Strategy 2: fallback to single-item query if batch fails
                    try:
                        if hasattr(reader, 'get_by_id'):
                            item = reader.get_by_id(local_id)
                            if item:
                                content = item.get('content_raw', '')
                                if size_type in ('thumb', 'thumbnail'):
                                    match = _re.search(r'<thumbfullmd5>([a-f0-9]{32})</thumbfullmd5>', content)
                                else:
                                    match = _re.search(r'<fullmd5>([a-f0-9]{32})</fullmd5>', content)
                                if match:
                                    return match.group(1)
                    except Exception:
                        continue
            return None
        except Exception:
            return None

    def _resolve_hardlink(self, md5: str, wxid: str) -> Optional[str]:
        """Use wcdb_api.dll hardlink resolution to find .dat file by MD5.

        Calls wcdb_resolve_image_hardlink(handle, md5, accountDir, &outPtr)
        which queries hardlink.db internally.
        """
        try:
            import ctypes as ct

            # Get a reader with an active DLL handle
            reader = _get_reader_for_wxid(wxid)
            if not reader or not hasattr(reader, '_dll') or not hasattr(reader, '_handle'):
                return None

            dll = reader._dll
            handle = reader._handle

            # Configure function signature
            dll.wcdb_resolve_image_hardlink.argtypes = [
                ct.c_int64, ct.c_char_p, ct.c_char_p,
                ct.POINTER(ct.c_void_p),
            ]
            dll.wcdb_resolve_image_hardlink.restype = ct.c_int32
            dll.wcdb_free_string.argtypes = [ct.c_void_p]
            dll.wcdb_free_string.restype = None

            account_dir = str(self._data_dir / wxid)

            out_ptr = ct.c_void_p()
            ret = dll.wcdb_resolve_image_hardlink(
                handle, md5.encode(), account_dir.encode(), ct.byref(out_ptr)
            )

            if ret == 0 and out_ptr.value:
                import json
                raw = ct.cast(out_ptr, ct.c_char_p).value
                result_str = raw.decode('utf-8', errors='replace') if raw else ""
                dll.wcdb_free_string(out_ptr)

                if result_str and result_str != '{}':
                    data = json.loads(result_str)
                    full_path = data.get('full_path', '')
                    if full_path:
                        return full_path

            return None
        except Exception:
            return None

    def _find_by_size(self, local_id: int, wxid: str,
                      size: str = 'original',
                      target_size: int = None,
                      has_fullmd5: bool = False) -> Optional[str]:
        """Fallback: find cache file by matching <fullsize>/<thumbfullsize> + V2 header.

        Args:
            has_fullmd5: True when the caller provided an explicit fullmd5
                (e.g. from chat_records). In this case, skip the last-resort
                thumb/ fallback because returning a random thumbnail for a
                multi-image item is worse than showing nothing.
        """
        fav_dir = self._data_dir / wxid / 'business' / 'favorite'
        if not fav_dir.exists():
            return None

        if target_size is None:
            target_size = self._get_fav_size_from_xml(local_id, size)
        if target_size is None:
            return None

        size_tolerance = 100
        dir_map = {
            'original': ('data', target_size),
            'mid': ('mid', target_size),
            'thumb': ('thumb', target_size),
            'thumbnail': ('thumb', target_size),
        }

        dir_name, expected_size = dir_map.get(size, ('data', target_size))
        target_dir = fav_dir / dir_name

        if not target_dir.exists():
            for alt_dir in ['data', 'mid', 'thumb']:
                alt_path = fav_dir / alt_dir
                if alt_path.exists():
                    target_dir = alt_path
                    break

        for f in target_dir.rglob('*'):
            if not f.is_file():
                continue
            file_size = f.stat().st_size
            if file_size == 0:
                continue
            if abs(file_size - (expected_size + 31)) < size_tolerance:
                return str(f)

        # Last resort: return first file from thumb/
        # Only when no fullmd5 was provided (type=2 single image scenario).
        # When fullmd5 is provided (type=14 chat records), skip last resort
        # because returning a random thumbnail is worse than showing nothing.
        if not has_fullmd5:
            thumb_dir = fav_dir / 'thumb'
            if thumb_dir.exists():
                for f in sorted(thumb_dir.rglob('*'), key=lambda p: p.stat().st_size, reverse=True):
                    if f.is_file() and f.stat().st_size > 1000:
                        return str(f)

        return None

    def _get_fav_size_from_xml(self, local_id: int, size_type: str) -> Optional[int]:
        """Extract image size from favorite XML content.

        Fallback: try get_items(500) first, if JSON parse error, fall back to get_by_id.
        """
        try:
            import sys
            import re as _re

            # Try to get a reader - check cached readers first
            if not _readers:
                from src.wechat.v2_cache_decrypt import _get_reader_for_wxid
                data_dir = os.getenv('WECHAT_DATA_DIR', '')
                if not data_dir:
                    return None

                # Try each wxid
                data_path = Path(data_dir)
                for d in data_path.iterdir():
                    if d.is_dir() and d.name.startswith('wxid_'):
                        wxid = d.name
                        _get_reader_for_wxid(wxid)
                        break

            # Search through cached readers (all types, not just type=2)
            for check_wxid, reader in _readers.items():
                try:
                    if not hasattr(reader, 'get_items'):
                        continue
                    # Strategy 1: batch query (efficient but may trigger JSON truncation)
                    items = reader.get_items(limit=500)
                    for item in items:
                        # local_id is stored as string, convert for comparison
                        item_local_id = item.get('local_id')
                        if str(item_local_id) == str(local_id):
                            content = item.get('content_raw', '')
                            if size_type in ('thumb', 'thumbnail'):
                                match = _re.search(r'<thumbfullsize>(\d+)</thumbfullsize>', content)
                                if match:
                                    return int(match.group(1))
                            else:  # original
                                match = _re.search(r'<fullsize>(\d+)</fullsize>', content)
                                if match:
                                    return int(match.group(1))
                except Exception:
                    # Strategy 2: fallback to single-item query if batch fails
                    try:
                        if hasattr(reader, 'get_by_id'):
                            item = reader.get_by_id(local_id)
                            if item:
                                content = item.get('content_raw', '')
                                if size_type in ('thumb', 'thumbnail'):
                                    match = _re.search(r'<thumbfullsize>(\d+)</thumbfullsize>', content)
                                    if match:
                                        return int(match.group(1))
                                else:  # original
                                    match = _re.search(r'<fullsize>(\d+)</fullsize>', content)
                                    if match:
                                        return int(match.group(1))
                    except Exception:
                        continue

            return None

        except Exception:
            return None


# ---------------------------------------------------------------------------
# Shared reader pool (reuse WcdbFavReader instances)
# ---------------------------------------------------------------------------

_reader_lock = threading.Lock()
_readers: dict[str, object] = {}


def _get_reader_for_wxid(wxid: str):
    """Get or create a WcdbFavReader for the given wxid.

    Uses the shared WcdbNativeClient instead of independent DLL lifecycle.
    """
    global _readers

    with _reader_lock:
        if wxid in _readers:
            return _readers[wxid]

        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            from src.web.api_handlers import get_wcdb_client

            client = get_wcdb_client()
            if not client:
                return None

            reader = WcdbFavReader(client)
            _readers[wxid] = reader
            return reader

        except Exception:
            return None
