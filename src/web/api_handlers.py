"""
API Handlers for 收藏/朋友圈/公众号模块

This module is imported by server.py to handle new API endpoints.
"""
import json
import logging
import threading
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from ..utils.op_logger import op_log, op_log_error

from src.assistant.config import (
    AssistantConfig,
    load_assistant_config,
    save_assistant_config,
)
from src.assistant.oa_digest import OADigestService
from src.assistant.oa_groups import OAGroupManager
from src.wechat.wcdb_client import WcdbNativeClient

logger = logging.getLogger(__name__)


# ── WebSocket event broadcast helper ───────────────────────────────────

def broadcast_event(event_type: str, data: dict):
    """Broadcast a custom WebSocket event to all connected clients.

    Uses server.py's _status._broadcast to push structured events.
    Event format: {"type": event_type, **data}
    """
    try:
        from src.web.server import _status
        payload = {"type": event_type, **data}
        _status._broadcast(payload)
    except Exception as e:
        logger.debug(f"Failed to broadcast event {event_type}: {e}")


# ── 数据库客户端 ──────────────────────────────────────────────────────

# Cache for database client (singleton per process)
_wcdb_client = None
_wcdb_fav_reader = None
_wcdb_sns_reader = None
_wcdb_init_lock = threading.RLock()  # RLock: allows re-entry from get_wcdb_client() inside _get_wcdb_fav_reader/_get_wcdb_sns_reader


def _load_wcdb_config():
    """Load WCDB_KEY and WECHAT_DATA_DIR from .env, with os.environ fallback.

    The onboarding flow writes to both .env and os.environ simultaneously,
    but some code paths (e.g. wcdb_client._key_candidates) only read
    os.environ.  This function checks .env first, then falls back to
    os.environ so both paths are covered.
    """
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    key = ""
    wxid_data_dir = ""
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("WCDB_KEY="):
                key = line.split("=", 1)[1].strip()
            elif line.startswith("WECHAT_DATA_DIR="):
                wxid_data_dir = line.split("=", 1)[1].strip()
    # Fallback to os.environ (e.g. set by onboarding or _save_key_to_env)
    if not key:
        key = os.environ.get("WCDB_KEY", "")
    if not wxid_data_dir:
        wxid_data_dir = os.environ.get("WECHAT_DATA_DIR", "")
    return key, wxid_data_dir


def _get_wcdb_fav_reader():
    """Get or create WcdbFavReader singleton, backed by the shared WcdbNativeClient."""
    global _wcdb_fav_reader
    if _wcdb_fav_reader is not None:
        return _wcdb_fav_reader
    with _wcdb_init_lock:
        if _wcdb_fav_reader is not None:
            return _wcdb_fav_reader
        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            client = get_wcdb_client()
            if not client:
                logger.error("Cannot create WcdbFavReader: WCDB client not available")
                return None
            _wcdb_fav_reader = WcdbFavReader(client)
        except Exception as e:
            logger.error(f"Failed to create WcdbFavReader: {e}")
            return None
    return _wcdb_fav_reader


def _get_wcdb_sns_reader():
    """Get or create WcdbSnsReader singleton, backed by the shared WcdbNativeClient."""
    global _wcdb_sns_reader
    if _wcdb_sns_reader is not None:
        return _wcdb_sns_reader
    with _wcdb_init_lock:
        if _wcdb_sns_reader is not None:
            return _wcdb_sns_reader
        try:
            from src.wechat.wcdb_sns_reader import WcdbSnsReader
            client = get_wcdb_client()
            if not client:
                logger.error("Cannot create WcdbSnsReader: WCDB client not available")
                return None
            _wcdb_sns_reader = WcdbSnsReader(client)
        except Exception as e:
            logger.error(f"Failed to create WcdbSnsReader: {e}")
            return None
    return _wcdb_sns_reader


def reset_wcdb_client():
    """Clear the WCDB client singleton cache.

    Must be called when the bot backend is stopped, because the backend's
    WcdbNativeClient is closed (wcdb_close) on shutdown.  If we don't
    clear the cache, the next get_wcdb_client() returns a stale client
    with an invalid handle, causing DLL calls to fail (ret=-2) and
    potentially crashing the process.
    """
    global _wcdb_client, _wcdb_fav_reader, _wcdb_sns_reader
    with _wcdb_init_lock:
        _wcdb_client = None
        _wcdb_fav_reader = None
        _wcdb_sns_reader = None
    logger.info("WCDB client cache cleared (bot stopped)")


def get_wcdb_client():
    """Get or create WCDB client singleton.

    Prefers reusing the backend's already-initialized client to avoid
    a second WcdbNativeClient() which crashes the DLL (access violation).
    Falls back to creating a new one only if no backend exists.
    """
    global _wcdb_client
    if _wcdb_client is not None:
        return _wcdb_client

    logger.info("[API-TRACE] get_wcdb_client: _wcdb_client is None, acquiring _wcdb_init_lock thread=%s", threading.current_thread().name)
    with _wcdb_init_lock:
        # Double-check after acquiring lock
        if _wcdb_client is not None:
            return _wcdb_client

        # Try to reuse the backend's client first (avoids DLL double-init crash)
        try:
            from src.web.server import _bot_control
            backend = getattr(_bot_control, "backend", None)
            if backend is not None and hasattr(backend, "_client") and backend._client is not None:
                _wcdb_client = backend._client
                logger.info("Reusing backend WCDB client (no double-init)")
                return _wcdb_client
        except Exception:
            pass

        # Fallback: create our own (only works if no other instance exists)
        try:
            _wcdb_client = WcdbNativeClient()
            _wcdb_client.init()
            _wcdb_client.open()
        except Exception as e:
            logger.error(f"Failed to initialize WCDB client: {e}")
            return None
        return _wcdb_client


# ── 收藏 API ─────────────────────────────────────────────────────────

def handle_fav_list(params, config: AssistantConfig):
    """GET /api/fav/list — List favorites"""
    logger.info("[API-TRACE] handle_fav_list ENTER thread=%s", threading.current_thread().name)
    reader = _get_wcdb_fav_reader()
    logger.info("[API-TRACE] handle_fav_list got reader=%s", reader is not None)
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        t0 = time.monotonic()
        # Parse limit/offset from params
        limit = int(params.get("limit", [200])[0]) if params.get("limit") else 200
        offset = int(params.get("offset", [0])[0]) if params.get("offset") else 0

        # Use test module's working get_items method
        logger.info("[API-TRACE] /api/fav/list: calling get_items thread=%s", threading.current_thread().name)
        items = reader.get_items(limit=limit, offset=offset)
        logger.info("[API-TRACE] /api/fav/list: get_items took %.0fms, got %d items",
                    (time.monotonic() - t0) * 1000, len(items) if items else 0)

        # ── Build tag mapping (fav_local_id -> [tag_info]) ──
        tags_data = reader.get_tags() or []
        bindings_data = reader.get_tag_bindings() or []
        tag_map = {t.get("local_id", ""): t.get("name", "") for t in tags_data}
        fav_tags = {}  # fav_local_id -> [{"id": ..., "name": ...}]
        for b in bindings_data:
            tid = b.get("tag_local_id", "")
            fid = b.get("fav_local_id", "")
            if tid and fid:
                fav_tags.setdefault(str(fid), []).append(
                    {"id": str(tid), "name": tag_map.get(tid, "")}
                )

        # Map to frontend-expected fields
        favorites = []
        for item in items:
            ftype = int(item.get("type", 0))
            # content: 如果有 description 用它，否则只有非XML的纯文本才显示
            raw_content = item.get("content_raw", "")
            desc = item.get("description", "")
            # 只有当 description 有值，或者 content_raw 不是 XML 格式时才使用
            content = desc if desc else (raw_content if raw_content and not raw_content.strip().startswith("<") else "")

            # Extract images (CDN URLs + keys)
            images = _extract_fav_image_info(item)

            # Extract chat records for nested chat records (type 14)
            # _parse_fav_xml merges results into item, so chat_records is at top level
            chat_records = item.get("chat_records", [])

            # For type 14, enrich chat records with voice/image metadata from datalist
            if ftype == 14 and raw_content and "<datalist>" in raw_content:
                chat_records = _enrich_chat_records_metadata(chat_records, raw_content)

            # For type 3 (voice), also enrich with duration from datalist
            if ftype == 3 and raw_content and "<datalist>" in raw_content:
                chat_records = _enrich_chat_records_metadata(chat_records, raw_content)

            # For type 14, enrich nested chat records (datatype=17) from recordxml
            # recordxml contains <datalist> with per-message details including images
            if ftype == 14 and raw_content and "<recordxml>" in raw_content:
                chat_records = _enrich_nested_chat_records(chat_records, raw_content)

            fav_id_str = str(item.get("local_id", ""))
            favorites.append({
                "id": item.get("local_id", 0),
                "type": ftype,
                "type_name": item.get("type_name", ""),
                "create_time": item.get("update_time", 0),
                "datetime": item.get("datetime", ""),
                "title": item.get("title", ""),
                "content": content,
                "from_user": item.get("from_user", ""),
                "link": item.get("link", ""),
                "images": images,
                "chat_records": chat_records,
                # Also include raw content for special types
                "content_raw": raw_content,
                # Tags from fav_tag_db_item (correct encoding, not from XML)
                "tags": fav_tags.get(fav_id_str, []),
            })

        logger.info("[API-TRACE] /api/fav/list: TOTAL %.0fms, %d favorites returned",
                    (time.monotonic() - t0) * 1000, len(favorites))

        return {
            "ok": True,
            "data": favorites,
            "total": len(favorites),
        }
    except Exception as e:
        logger.error(f"Failed to list favorites: {e}")
        return {"ok": False, "error": str(e)}


def handle_fav_tags(params, config: AssistantConfig):
    """GET /api/fav/tags — 获取所有标签及关联数量"""
    reader = _get_wcdb_fav_reader()
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        tags = reader.get_tags() or []
        bindings = reader.get_tag_bindings() or []

        # Build: tag_id -> [fav_local_ids]
        tag_favs = {}
        for b in bindings:
            tid = b.get("tag_local_id", "")
            fid = b.get("fav_local_id", "")
            tag_favs.setdefault(tid, []).append(fid)

        # Merge tag definitions with their fav counts
        result = []
        for t in tags:
            tid = t.get("local_id", "")
            result.append({
                "id": str(tid),
                "name": t.get("name", ""),
                "fav_ids": tag_favs.get(tid, []),
                "fav_count": len(tag_favs.get(tid, [])),
            })

        return {"ok": True, "data": result}
    except Exception as e:
        logger.error(f"Failed to get fav tags: {e}")
        return {"ok": False, "error": str(e)}


def _enrich_chat_records_metadata(chat_records: list, content_raw: str) -> list:
    """Enrich chat record items (especially voice type=3) with metadata from <datalist>.

    The <datalist><dataitem> in the fav XML contains voice metadata like
    srcMsgCreateTime, fromnewmsgid, duration that are not in the basic
    chat_records from the reader. This function matches by dataid and
    merges the metadata back.
    """
    import re as _re
    import xml.etree.ElementTree as _ET

    # Extract all dataitem elements from the top-level datalist
    # (not from recordxml — those are handled separately)
    # Match <datalist> that is NOT inside <recordxml>
    # Simple approach: find the first <datalist> and parse it
    datalist_match = _re.search(r'<datalist>(.*?)</datalist>', content_raw, _re.DOTALL)
    if not datalist_match:
        return chat_records

    try:
        clean = datalist_match.group(1).replace("&#x0A;", "\n")
        clean = _re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#x[0-9a-fA-F]+|#\d+);)', '&amp;', clean)
        root = _ET.fromstring(f"<root>{clean}</root>")
    except Exception:
        return chat_records

    # Build a map: dataid -> metadata dict
    dataid_map = {}
    for di in root.findall("dataitem"):
        dataid = di.get("dataid", "")
        dtype = di.get("datatype", "")
        if not dataid:
            continue

        meta = {"type": dtype}
        # Voice metadata
        if dtype == "3":
            duration = _get_xml_text(di, "duration")
            src_msg_ct = _get_xml_text(di, "srcMsgCreateTime")
            from_msg_id = _get_xml_text(di, "fromnewmsgid") or _get_xml_text(di, "datasourceid")
            if duration:
                meta["duration"] = int(duration)
            if src_msg_ct:
                meta["srcMsgCreateTime"] = int(src_msg_ct)
            if from_msg_id:
                meta["fromnewmsgid"] = from_msg_id

        # Image metadata
        elif dtype == "2":
            fullmd5 = _get_xml_text(di, "fullmd5")
            fullsize = _get_xml_text(di, "fullsize")
            if fullmd5:
                meta["fullmd5"] = fullmd5
            if fullsize:
                meta["fullsize"] = int(fullsize)

        dataid_map[dataid] = meta

    # Merge metadata into chat records
    if not dataid_map:
        return chat_records

    for rec in chat_records:
        dataid = rec.get("dataid", "")
        if dataid and dataid in dataid_map:
            meta = dataid_map[dataid]
            for k, v in meta.items():
                if k != "type" and k not in rec:  # Don't override existing fields
                    rec[k] = v

    return chat_records


def _enrich_nested_chat_records(chat_records: list, content_raw: str) -> list:
    """Enrich type=17 (nested chat record) items with data from <recordxml>.

    The XML contains <recordxml><recordinfo><datalist> with per-message
    details including images, files, and sub-chat records. This function
    parses those and merges them back into the chat_records list, replacing
    the flat type=17 items with their expanded sub-messages.
    """
    import re as _re
    import xml.etree.ElementTree as _ET

    # Find all <recordxml> sections
    recordxml_sections = _re.findall(r'<recordxml>(.*?)</recordxml>', content_raw, _re.DOTALL)
    if not recordxml_sections:
        return chat_records

    # Parse each recordxml section into sub-messages
    sub_chat_lists = []  # List of lists, one per recordxml
    for section_xml in recordxml_sections:
        try:
            # Clean XML
            clean = section_xml.replace("&#x0A;", "\n")
            clean = _re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#x[0-9a-fA-F]+|#\d+);)', '&amp;', clean)
            root = _ET.fromstring(f"<root>{clean}</root>")
        except Exception:
            sub_chat_lists.append(None)
            continue

        recordinfo = root.find("recordinfo")
        if recordinfo is None:
            sub_chat_lists.append(None)
            continue

        datalist = recordinfo.find("datalist")
        if datalist is None:
            sub_chat_lists.append(None)
            continue

        sub_records = []
        for di in datalist.findall("dataitem"):
            dtype = di.get("datatype", "")
            rec = {
                "type": dtype,
                "src_name": (_get_xml_text(di, "datasrcname") or _get_xml_text(di, "sourcename")) or "",
                "desc": _get_xml_text(di, "datadesc") or "",
                "time": _get_xml_text(di, "datasrctime") or "",
                "head_url": _get_xml_text(di, "sourceheadurl") or "",
            }
            dataid = di.get("dataid", "")
            if dataid:
                rec["dataid"] = dataid

            # Image details
            if dtype == "2":
                fullmd5 = _get_xml_text(di, "fullmd5")
                fullsize = _get_xml_text(di, "fullsize")
                cdn_dataurl = _get_xml_text(di, "cdn_dataurl")
                if fullmd5:
                    rec["fullmd5"] = fullmd5
                if fullsize:
                    rec["fullsize"] = int(fullsize)
                if cdn_dataurl:
                    rec["cdn_dataurl"] = cdn_dataurl

            # Voice details (datatype=3)
            if dtype == "3":
                duration = _get_xml_text(di, "duration")
                src_msg_create_time = _get_xml_text(di, "srcMsgCreateTime")
                from_new_msg_id = _get_xml_text(di, "fromnewmsgid") or _get_xml_text(di, "datasourceid")
                fullmd5 = _get_xml_text(di, "fullmd5")
                fullsize = _get_xml_text(di, "fullsize")
                if duration:
                    rec["duration"] = int(duration)
                if src_msg_create_time:
                    rec["srcMsgCreateTime"] = int(src_msg_create_time)
                if from_new_msg_id:
                    rec["fromnewmsgid"] = from_new_msg_id
                if fullmd5:
                    rec["fullmd5"] = fullmd5
                if fullsize:
                    rec["fullsize"] = int(fullsize)
                    rec["fullsize"] = int(fullsize)
                if cdn_dataurl:
                    rec["cdn_dataurl"] = cdn_dataurl

            # File details
            if dtype == "8":
                datatitle = _get_xml_text(di, "datatitle")
                datafmt = _get_xml_text(di, "datafmt")
                if datatitle:
                    rec["file_name"] = datatitle
                if datafmt:
                    rec["file_type"] = datafmt

            sub_records.append({k: v for k, v in rec.items() if v})

        title = _get_xml_text(recordinfo, "title")
        sub_chat_lists.append({"records": sub_records, "title": title})

    # Now merge: replace each type=17 record with its expanded sub-messages
    result = []
    sub_idx = 0
    for rec in chat_records:
        if rec.get("type") == "17" and sub_idx < len(sub_chat_lists):
            sub_data = sub_chat_lists[sub_idx]
            sub_idx += 1
            if sub_data and sub_data.get("records"):
                # Wrap sub-messages in a nested group with a title
                result.append({
                    "type": "17",
                    "title": sub_data.get("title") or rec.get("datatitle") or "聊天记录",
                    "sub_records": sub_data["records"],
                    "src_name": rec.get("src_name", ""),
                    "time": rec.get("time", ""),
                    "head_url": rec.get("head_url", ""),
                })
            else:
                result.append(rec)
        else:
            result.append(rec)

    return result


def _get_xml_text(element, tag: str) -> str | None:
    """Get text content of a child XML element."""
    el = element.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _extract_fav_image_info(item: dict) -> list[dict]:
    """Extract image CDN info from a fav item (test module's get_items result).

    Returns list of {url, key} dicts. The fav XML stores <cdn_dataurl> and
    <cdn_datakey> in <datalist>/<dataitem> elements which the reader puts
    into item["image_cdn"] or item["image_list"].

    For type=14 (聊天记录), skip image_list entirely — the images belong
    inside chat_records, not at the top level. They are downloaded separately
    by _download_fav_media() via chat_records items.
    """
    images = []
    ftype = int(item.get("type", 0) or 0)

    # Type 14 (chat records): images are part of the chat records,
    # not standalone top-level images. Skip to avoid duplication.
    if ftype == 14:
        return images

    # Type 3 (voice): image_list/image_cdn are voice waveform thumbnails,
    # not meaningful images. Skip to avoid mysterious images under voice.
    if ftype == 3:
        return images

    # Reader returns image_list with all CDN images (including files with CDN URLs)
    image_list = item.get("image_list")
    if isinstance(image_list, list) and image_list:
        for img in image_list:
            dataurl = img.get("dataurl")
            if dataurl:
                # Protobuf CDN URLs (306/307) — use local V2 cache decryption
                if isinstance(dataurl, str) and (dataurl.startswith("306") or dataurl.startswith("307") or dataurl.startswith("306c") or dataurl.startswith("306b") or dataurl.startswith("306d") or dataurl.startswith("307c") or dataurl.startswith("307b")):
                    images.append({"url": dataurl, "key": "v2_cache", "fullmd5": img.get("fullmd5"), "fullsize": img.get("fullsize")})
                else:
                    images.append({"url": dataurl, "key": img.get("datakey", 0)})
        # Filter to only type=2 (image) — exclude file/attachment CDN URLs
        # This is already handled by the reader only putting datatype=2 items in image_list

    # Fallback: single image_cdn dict (for simple type=2 image items)
    if not images:
        ic = item.get("image_cdn")
        if isinstance(ic, dict):
            dataurl = ic.get("dataurl")
            if dataurl:
                if isinstance(dataurl, str) and (dataurl.startswith("306") or dataurl.startswith("307") or dataurl.startswith("306c") or dataurl.startswith("306b") or dataurl.startswith("306d")):
                    images.append({"url": dataurl, "key": "v2_cache", "fullmd5": ic.get("fullmd5"), "fullsize": ic.get("fullsize")})
                else:
                    images.append({"url": dataurl, "key": ic.get("datakey", 0)})

    # Fallback: thumbUrl field
    thumb = item.get("thumbUrl") or item.get("thumb_url")
    if thumb and not any(img["url"] == thumb for img in images):
        # For video type (type=4), mark the thumb as thumbnail-only
        if ftype == 4:
            images.append({"url": thumb, "key": 0, "is_thumb": True})
        else:
            images.append({"url": thumb, "key": 0})

    # For video type (type=4), mark all images so frontend knows
    if ftype == 4:
        for img in images:
            img["is_video"] = True

    return images


def _resolve_fav_item_for_export(item: dict) -> dict:
    """Convert test module's fav row into the canonical export schema."""
    raw_content = item.get("content_raw", "")
    desc = item.get("description", "")
    content = desc if desc else (raw_content if raw_content and not raw_content.strip().startswith("<") else "")

    ftype = int(item.get("type", 0) or 0)
    # Only type 14 (聊天记录) items have meaningful chat_records.
    # Other types have a <datalist> with their own media metadata,
    # which is NOT chat record content.
    chat_records = item.get("chat_records", []) if ftype == 14 else []

    return {
        "id": item.get("local_id", 0),
        "type": ftype,
        "type_name": item.get("type_name", ""),
        "create_time": int(item.get("update_time", 0) or 0),
        "datetime": item.get("datetime", ""),
        "title": item.get("title", ""),
        "content": content,
        "from_user": item.get("from_user", ""),
        "link": item.get("link", ""),
        "images": _extract_fav_image_info(item),
        "chat_records": chat_records,
        "content_raw": raw_content,
    }


def _detect_image_ext(data: bytes) -> str:
    """Detect image/video extension from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    elif data[:2] == b"\xff\xd8":
        return "jpg"
    elif data[:4] == b"GIF8":
        return "gif"
    elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "webp"
    elif len(data) >= 8 and data[4:8] == b"ftyp":
        return "mp4"
    return "jpg"


def _decrypt_fav_chat_record_voice(item: dict, rec: dict) -> Optional[bytes]:
    """Decrypt a voice from a chat record inside a favorite item.
    Uses the same WCDB voice lookup as _decrypt_fav_voice, but with
    metadata from the chat record instead of XML parsing."""
    try:
        import re as _re
        from src.wechat.voice_decode import silk_to_wav

        fav_id = int(item.get("id", 0))
        # Voice metadata comes from _enrich_chat_records_metadata
        src_msg_ct = rec.get("srcMsgCreateTime")
        from_msg_id = rec.get("fromnewmsgid") or rec.get("datasourceid")

        if not src_msg_ct or not from_msg_id:
            return None

        # Get fromusr/tousr from the parent fav item's content_raw
        content_raw = item.get("content_raw", "")
        fromusr_match = _re.search(r'<fromusr>([^<]+)</fromusr>', content_raw)
        tousr_match = _re.search(r'<tousr>([^<]+)</tousr>', content_raw)

        fromusr = fromusr_match.group(1) if fromusr_match else ""
        tousr = tousr_match.group(1) if tousr_match else ""
        candidates = [t for t in [tousr, fromusr] if t]

        if not candidates:
            return None

        client = get_wcdb_client()
        if not client:
            return None

        create_time = int(src_msg_ct)
        msg_id = int(from_msg_id)

        for candidate in candidates:
            result = client.get_voice_data(
                session_id=candidate,
                create_time=create_time,
                local_id=0,
                svr_id=msg_id,
                candidates=candidates,
            )
            if result and result.get("success") and result.get("hex"):
                return silk_to_wav(result["hex"])

        return None
    except Exception as e:
        logger.warning(f"Failed to decrypt chat record voice: {e}")
        return None


def _download_fav_media(items: list[dict], export_dir: str, broadcast=None) -> dict:
    """Download and decrypt all media (images, videos, voices) for fav items.

    Mutates items to set local_path on images and adds voice_path/video_path.

    Args:
        items: List of canonical fav dicts (each with 'images' list and 'type')
        export_dir: Target export root directory (images/ and voice/ subdirs created inside)
        broadcast: Optional callable(name, dict) to send progress

    Returns:
        {total: N, downloaded: M, errors: K, items_with_media: I, skipped: S}
    """
    from src.wechat.image_decrypt import download_and_decrypt

    MAX_DOWNLOAD_ITEMS = 1000
    MAX_DOWNLOAD_SECONDS = 300

    images_dir = os.path.join(export_dir, "images")
    voice_dir = os.path.join(export_dir, "voice")
    videos_dir = os.path.join(export_dir, "videos")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(voice_dir, exist_ok=True)
    os.makedirs(videos_dir, exist_ok=True)
    stats = {"total": 0, "downloaded": 0, "errors": 0, "items_with_media": 0, "skipped": 0}
    start_time = time.monotonic()

    for item in items:
        # Safety: check download limits
        if stats["downloaded"] >= MAX_DOWNLOAD_ITEMS:
            stats["skipped"] = len(items) - items.index(item)
            logger.warning("Fav export media: hit MAX_DOWNLOAD_ITEMS=%d, skipping %d remaining",
                           MAX_DOWNLOAD_ITEMS, stats["skipped"])
            break
        if time.monotonic() - start_time > MAX_DOWNLOAD_SECONDS:
            stats["skipped"] = len(items) - items.index(item)
            logger.warning("Fav export media: hit %ds timeout, skipping %d remaining",
                           MAX_DOWNLOAD_SECONDS, stats["skipped"])
            break

        ftype = item.get("type", 0)
        has_media = False

        # ── Voice (type=3): SILK → WAV via DLL ──────────────────────────
        if ftype == 3:
            has_media = True
            stats["total"] += 1
            try:
                voice_data = _decrypt_fav_voice(item)
                if voice_data:
                    filename = f"voice_{item['id']}.wav"
                    out_path = os.path.join(voice_dir, filename)
                    with open(out_path, "wb") as f:
                        f.write(voice_data)
                    item["voice_path"] = f"voice/{filename}"
                    stats["downloaded"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                logger.warning(f"Failed to export fav voice {item['id']}: {e}")
                stats["errors"] += 1

        # ── Images & Videos: V2 cache or CDN ────────────────────────────
        imgs = item.get("images") or []
        if imgs:
            has_media = True
            for idx, img in enumerate(imgs):
                stats["total"] += 1
                url = img.get("url", "")
                key = img.get("key", 0)
                fullmd5 = img.get("fullmd5", "")
                fullsize = img.get("fullsize")

                if not url:
                    continue

                try:
                    data = None

                    # V2 cache: use V2CacheManager (local .dat file decryption)
                    if key == "v2_cache" or (isinstance(url, str) and url.startswith(("306", "307"))):
                        data = _decrypt_fav_v2_media(int(item['id']), fullmd5, fullsize)

                    # CDN URL: use ISAAC-64 download_and_decrypt
                    if not data:
                        data = download_and_decrypt(url, key if key and key != "v2_cache" else None, timeout=15)

                    if not data:
                        stats["errors"] += 1
                        continue

                    ext = _detect_image_ext(data)

                    filename = f"fav_{item['id']}_{idx}.{ext}"
                    out_path = os.path.join(images_dir, filename)
                    with open(out_path, "wb") as f:
                        f.write(data)
                    img["local_path"] = f"images/{filename}"
                    # Mark video type for HTML rendering
                    if ext == "mp4":
                        img["is_video"] = True
                    stats["downloaded"] += 1
                except Exception as e:
                    logger.warning(f"Failed to download fav media {url}: {e}")
                    stats["errors"] += 1

        # ── Chat records media (type=14): images and voices inside chat_records ──
        chat_records = item.get("chat_records") or []
        if chat_records:
            has_media = True
            for ri, rec in enumerate(chat_records):
                rec_type = str(rec.get("type", "0"))

                # Image in chat record
                if rec_type == "2":
                    fullmd5 = rec.get("fullmd5", "")
                    fullsize = rec.get("fullsize")
                    cdn_dataurl = rec.get("cdn_dataurl", "")
                    cdn_datakey = rec.get("cdn_datakey", "")

                    if fullmd5 or cdn_dataurl:
                        stats["total"] += 1
                        try:
                            data = None
                            # Try V2 cache first
                            if fullmd5:
                                data = _decrypt_fav_v2_media(int(item['id']), fullmd5, fullsize)
                            # Fallback to CDN
                            if not data and cdn_dataurl and cdn_datakey:
                                data = download_and_decrypt(cdn_dataurl, cdn_datakey, timeout=15)

                            if data:
                                ext = _detect_image_ext(data)
                                filename = f"fav_{item['id']}_cr_{ri}.{ext}"
                                out_path = os.path.join(images_dir, filename)
                                with open(out_path, "wb") as f:
                                    f.write(data)
                                rec["local_path"] = f"images/{filename}"
                                stats["downloaded"] += 1
                            else:
                                stats["errors"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to download chat record image: {e}")
                            stats["errors"] += 1

                # Voice in chat record
                elif rec_type == "3":
                    dataid = rec.get("dataid", "")
                    if dataid:
                        stats["total"] += 1
                        try:
                            voice_data = _decrypt_fav_chat_record_voice(item, rec)
                            if voice_data:
                                filename = f"voice_{item['id']}_cr_{ri}.wav"
                                out_path = os.path.join(voice_dir, filename)
                                with open(out_path, "wb") as f:
                                    f.write(voice_data)
                                rec["voice_path"] = f"voice/{filename}"
                                stats["downloaded"] += 1
                            else:
                                stats["errors"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to download chat record voice: {e}")
                            stats["errors"] += 1

                # Nested chat records (type=17): process sub_records
                elif rec_type == "17":
                    sub_records = rec.get("sub_records") or []
                    for si, sub in enumerate(sub_records):
                        sub_type = str(sub.get("type", "0"))

                        # Image in nested sub-record
                        if sub_type == "2":
                            fullmd5 = sub.get("fullmd5", "")
                            fullsize = sub.get("fullsize")
                            cdn_dataurl = sub.get("cdn_dataurl", "")
                            cdn_datakey = sub.get("cdn_datakey", "")

                            if fullmd5 or cdn_dataurl:
                                stats["total"] += 1
                                try:
                                    data = None
                                    if fullmd5:
                                        data = _decrypt_fav_v2_media(int(item['id']), fullmd5, fullsize)
                                    if not data and cdn_dataurl and cdn_datakey:
                                        data = download_and_decrypt(cdn_dataurl, cdn_datakey, timeout=15)
                                    if data:
                                        ext = _detect_image_ext(data)
                                        filename = f"fav_{item['id']}_cr_{ri}_sub_{si}.{ext}"
                                        out_path = os.path.join(images_dir, filename)
                                        with open(out_path, "wb") as f:
                                            f.write(data)
                                        sub["local_path"] = f"images/{filename}"
                                        stats["downloaded"] += 1
                                    else:
                                        stats["errors"] += 1
                                except Exception as e:
                                    logger.warning(f"Failed to download nested chat record image: {e}")
                                    stats["errors"] += 1

                        # Voice in nested sub-record
                        elif sub_type == "3":
                            dataid = sub.get("dataid", "")
                            if dataid:
                                stats["total"] += 1
                                try:
                                    voice_data = _decrypt_fav_chat_record_voice(item, sub)
                                    if voice_data:
                                        filename = f"voice_{item['id']}_cr_{ri}_sub_{si}.wav"
                                        out_path = os.path.join(voice_dir, filename)
                                        with open(out_path, "wb") as f:
                                            f.write(voice_data)
                                        sub["voice_path"] = f"voice/{filename}"
                                        stats["downloaded"] += 1
                                    else:
                                        stats["errors"] += 1
                                except Exception as e:
                                    logger.warning(f"Failed to download nested chat record voice: {e}")
                                    stats["errors"] += 1

        if has_media:
            stats["items_with_media"] += 1

    return stats


def _decrypt_fav_v2_media(local_id: int, fullmd5: str = "", fullsize: int = None) -> Optional[bytes]:
    """Decrypt a favorite image/video from local V2 cache using V2CacheManager."""
    try:
        from src.wechat.v2_cache_decrypt import V2CacheManager
        import os as _os
        from pathlib import Path as _Path

        # Auto-detect wxid
        data_dir = _os.getenv("WECHAT_DATA_DIR", "")
        if not data_dir:
            return None
        wxid_dirs = sorted(
            [d for d in _Path(data_dir).iterdir()
             if d.is_dir() and d.name.startswith("wxid_")],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not wxid_dirs:
            return None
        wxid = wxid_dirs[0].name

        manager = V2CacheManager.get_instance(data_dir)
        return manager.decrypt_fav_image(
            local_id, wxid, size="original",
            fullmd5=fullmd5 if fullmd5 else None,
            fullsize=fullsize
        )
    except Exception as e:
        logger.debug(f"V2 cache decrypt failed for fav {local_id}: {e}")
        return None


def _decrypt_fav_voice(item: dict) -> Optional[bytes]:
    """Decrypt a favorite voice (type=3) via DLL → SILK → WAV."""
    try:
        from src.wechat.voice_decode import silk_to_wav
        from src.web.api_handlers import _get_wcdb_fav_reader, get_wcdb_client
        import re

        fav_id = int(item.get("id", 0))

        # Get fav item content (need XML for voice metadata) — O(1) lookup
        fav_reader = _get_wcdb_fav_reader()
        if not fav_reader:
            return None

        fav_item = fav_reader.get_by_id(fav_id)

        if not fav_item:
            return None

        content = fav_item.get("content", "") or fav_item.get("content_raw", "")
        if not content:
            return None

        fromusr_match = re.search(r'<fromusr>([^<]+)</fromusr>', content)
        tousr_match = re.search(r'<tousr>([^<]+)</tousr>', content)
        createtime_match = re.search(r'<createtime>(\d+)</createtime>', content)
        msgid_match = re.search(r'<msgid>(\d+)</msgid>', content)

        if not all([fromusr_match, tousr_match, createtime_match, msgid_match]):
            return None

        fromusr = fromusr_match.group(1)
        tousr = tousr_match.group(1)
        createtime = int(createtime_match.group(1))
        msgid = int(msgid_match.group(1))

        client = get_wcdb_client()
        if not client:
            return None

        result = client.get_voice_data(
            session_id=tousr,
            create_time=createtime,
            local_id=0,
            svr_id=msgid,
            candidates=[tousr, fromusr]
        )

        if not result.get("success"):
            return None

        hex_data = result.get("hex", "")
        if not hex_data:
            return None

        return silk_to_wav(hex_data)
    except Exception as e:
        logger.warning(f"Failed to decrypt fav voice {item.get('id')}: {e}")
        return None


def _render_cr_msg(rec: dict, html_lib) -> str:
    """Render a single chat record message as HTML for export."""
    import re as _re
    rec_type = str(rec.get("type", "0"))
    name = html_lib.escape(rec.get("src_name", "未知"))
    initial = (rec.get("src_name") or "?")[0].upper()
    time_str = rec.get("time", "")

    row = "<div class='cr-msg'>"
    # Avatar
    head_url = rec.get("head_url", "")
    if head_url:
        row += (f"<div class='cr-avatar'>"
                f"<img src='{html_lib.escape(head_url)}' "
                f"onerror=\"this.style.display='none';this.parentElement.textContent="
                f"'{html_lib.escape(initial)}'\"></div>")
    else:
        row += f"<div class='cr-avatar'>{html_lib.escape(initial)}</div>"

    row += "<div>"
    row += f"<span class='cr-name'>{name}</span>"
    if time_str:
        row += f"<span class='cr-time'>{html_lib.escape(time_str)}</span>"

    # Content by type
    if rec_type == "1" and rec.get("desc"):
        row += f"<div class='cr-text'>{html_lib.escape(rec['desc']).replace(chr(10), '<br>')}</div>"
    elif rec_type == "2":
        local_path = rec.get("local_path", "")
        if local_path:
            row += f"<img class='cr-img' src='{local_path}' loading='lazy' onclick='openLb(this.src)'>"
        else:
            row += "<div class='cr-text'>[图片]</div>"
    elif rec_type == "3":
        voice_path = rec.get("voice_path", "")
        duration = rec.get("duration", 0)
        dur_str = f"{duration/1000:.1f}s" if duration else ""
        if voice_path:
            row += f"<div class='cr-voice'>🎤 语音 {html_lib.escape(dur_str)} <audio controls src='{voice_path}' preload='metadata'></audio></div>"
        else:
            row += f"<div class='cr-voice'>🎤 语音 {html_lib.escape(dur_str)}</div>"
    elif rec_type == "8":
        file_name = rec.get("file_name", "文件")
        file_type = rec.get("file_type", "")
        label = f"{html_lib.escape(file_name)}.{html_lib.escape(file_type)}" if file_type else html_lib.escape(file_name)
        row += f"<div class='cr-file'>📎 {label}</div>"
    elif rec_type == "17" and rec.get("sub_records"):
        sub_records = rec.get("sub_records", [])
        sub_title = html_lib.escape(rec.get("title") or rec.get("datatitle") or "聊天记录")
        row += f"<div class='cr-nested'>"
        row += f"<div class='cr-header'><span class='cr-title'>{sub_title}</span><span class='cr-count'>{len(sub_records)}条</span></div>"
        row += "<div class='cr-body'>"
        for sub in sub_records:
            row += _render_cr_msg(sub, html_lib)
        row += "</div></div>"
    elif rec.get("desc"):
        row += f"<div class='cr-text'>{html_lib.escape(rec['desc']).replace(chr(10), '<br>')}</div>"

    row += "</div></div>"
    return row


def _build_fav_index_html(items: list[dict], output_path: str) -> int:
    """Render a simple browsable index.html for the exported favs. Returns 0/1."""
    html = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<title>微信收藏导出</title>",
        "<style>",
        "*{box-sizing:border-box}body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#F0EEE9;color:#3d3d3d;margin:0;padding:20px}",
        ".hd{max-width:900px;margin:0 auto 16px}h2{font-size:20px;margin:0}.info{font-size:12px;color:#999;margin-top:4px}",
        ".list{max-width:900px;margin:0 auto;display:grid;gap:16px}",
        ".item{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.04)}",
        ".meta{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}",
        ".badge{font-size:11px;padding:2px 10px;border-radius:999px;background:#8B7355;color:#fff}",
        ".ts{font-size:12px;color:#999}.title{font-weight:600;margin:4px 0}",
        ".content{font-size:14px;line-height:1.6;white-space:pre-wrap;word-break:break-word}",
        ".imgs{display:grid;gap:6px;margin-top:12px;grid-template-columns:repeat(3,1fr);max-width:480px}",
        ".imgs img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;cursor:zoom-in}",
        ".imgs video{width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px}",
        ".imgs .img-placeholder{width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;border-radius:8px;background:rgba(0,0,0,.03);border:1px dashed rgba(0,0,0,.08);color:#999;font-size:12px}",
        ".imgs.single{grid-template-columns:1fr;max-width:400px}",
        ".imgs.single img{aspect-ratio:auto;max-height:480px;object-fit:contain}",
        ".imgs.single video{aspect-ratio:auto;max-height:480px;object-fit:contain}",
        ".voice{margin-top:12px;display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:10px;background:rgba(0,0,0,.03);border:1px solid rgba(0,0,0,.06)}",
        ".voice audio{flex:1;height:36px}",
        ".voice-label{font-size:13px;color:#8B7355}",
        ".link{color:#8B7355;text-decoration:none;word-break:break-all}",
        ".chat-records{margin-top:12px;border:1px solid rgba(0,0,0,.08);border-radius:10px;overflow:hidden;background:#fafafa}",
        ".chat-records .cr-header{display:flex;align-items:center;gap:6px;padding:8px 12px;background:#f5f5f0;border-bottom:1px solid rgba(0,0,0,.06);font-size:12px;color:#8B7355}",
        ".chat-records .cr-header .cr-title{font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".chat-records .cr-header .cr-count{color:#999}",
        ".chat-records .cr-body{padding:8px 12px;max-height:300px;overflow-y:auto}",
        ".chat-records .cr-msg{display:flex;gap:8px;margin-bottom:8px;align-items:flex-start}",
        ".chat-records .cr-avatar{width:24px;height:24px;border-radius:4px;background:#8B7355;color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;flex-shrink:0;overflow:hidden}",
        ".chat-records .cr-avatar img{width:100%;height:100%;object-fit:cover}",
        ".chat-records .cr-name{font-size:11px;color:#8B7355;font-weight:600}",
        ".chat-records .cr-time{font-size:10px;color:#bbb;margin-left:4px}",
        ".chat-records .cr-text{font-size:12px;line-height:1.5;margin-top:2px;padding:4px 8px;border-radius:6px;background:#fff;max-width:85%;word-break:break-word;display:inline-block}",
        ".chat-records .cr-img{max-width:60%;max-height:120px;border-radius:6px;margin-top:2px;cursor:zoom-in}",
        ".chat-records .cr-voice{display:flex;align-items:center;gap:6px;margin-top:2px;padding:4px 8px;border-radius:6px;background:#fff;font-size:11px;color:#8B7355}",
        ".chat-records .cr-voice audio{height:24px;flex:1}",
        ".chat-records .cr-file{display:flex;align-items:center;gap:4px;margin-top:2px;padding:4px 8px;border-radius:6px;background:#fff;font-size:11px;color:#999}",
        ".chat-records .cr-nested{margin-top:4px;border:1px solid rgba(0,0,0,.06);border-radius:8px;overflow:hidden;margin-left:8px}",
        "img.lb{display:none;position:fixed;inset:0;max-width:92vw;max-height:92vh;margin:auto;background:rgba(0,0,0,.92);padding:20px;z-index:99;cursor:zoom-out}",
        "img.lb.on{display:block}",
        "</style></head><body>",
        f"<div class='hd'><h2>微信收藏导出</h2><div class='info'>共 {len(items)} 条</div></div>",
        "<div class='list'>",
    ]

    type_names = {1: "文字", 2: "图片", 3: "语音", 4: "视频", 5: "链接", 8: "文件", 14: "笔记", 16: "位置", 17: "联系人", 33: "文章"}

    for item in items:
        ftype = item.get("type", 0)

        # ── Voice (type=3): <audio> player ──────────────────────────────
        voice_html = ""
        voice_path = item.get("voice_path", "")
        if ftype == 3 and voice_path:
            voice_html = f"<div class='voice'><span class='voice-label'>🎤 语音</span><audio controls src='{voice_path}' preload='metadata'></audio></div>"

        # ── Images & Videos ──────────────────────────────────────────────
        imgs = item.get("images") or []
        imgs_html = ""
        if imgs:
            cls = "imgs single" if len(imgs) == 1 else "imgs"
            imgs_html = f"<div class='{cls}'>"
            for img in imgs:
                local = img.get("local_path") or ""
                is_video = img.get("is_video", False) or (local and local.lower().endswith(".mp4"))
                if local:
                    if is_video:
                        imgs_html += f"<video src='{local}' controls preload='metadata'></video>"
                    else:
                        imgs_html += f"<img src='{local}' loading='lazy' onclick='openLb(this.src)'>"
                else:
                    label = "🎬 视频" if is_video else "未下载"
                    imgs_html += f"<div class='img-placeholder'>{label}</div>"
            imgs_html += "</div>"

        link_html = ""
        if item.get("link"):
            link_html = f"<div style='margin-top:8px'><a class='link' href='{item['link']}' target='_blank'>{item['link']}</a></div>"

        content_html = ""
        if item.get("content"):
            import html as html_lib
            escaped = html_lib.escape(item["content"]).replace("\n", "<br>")
            content_html = f"<div class='content'>{escaped}</div>"

        # ── Chat records (type=14 聊天记录) ───────────────────────────
        # Only type 14 items have meaningful chat_records.
        # For chat record items: skip title/content/imgs (redundant with
        # the chat-records card), use the item title as card header.
        chat_records_html = ""
        is_chat_record = ftype == 14 and (item.get("chat_records") or [])
        if is_chat_record:
            import html as html_lib
            chat_records = item.get("chat_records") or []
            # Use the fav item's title as the card header (e.g. "Cloud的聊天记录")
            card_title = html_lib.escape(item.get("title") or "聊天记录")
            chat_records_html = "<div class='chat-records'>"
            chat_records_html += f"<div class='cr-header'><span class='cr-title'>{card_title}</span><span class='cr-count'>{len(chat_records)}条</span></div>"
            chat_records_html += "<div class='cr-body'>"
            for rec in chat_records:
                chat_records_html += _render_cr_msg(rec, html_lib)
            chat_records_html += "</div></div>"

        type_label = type_names.get(ftype, f"类型{ftype}")
        ts = item.get("datetime") or ""
        title_html = f"<div class='title'>{item.get('title', '')}</div>" if item.get("title") else ""

        # For type 14 chat records: badge shows "聊天记录", skip title/content/imgs
        if is_chat_record:
            type_label = "聊天记录"
            title_html = ""
            content_html = ""
            imgs_html = ""

        html.append(
            f"<div class='item'>"
            f"<div class='meta'><span class='badge'>{type_label}</span><span class='ts'>{ts}</span></div>"
            f"{title_html}{content_html}{voice_html}{imgs_html}{link_html}{chat_records_html}"
            f"</div>"
        )

    html.append("</div>")
    html.append("<img class='lb' id='lb' onclick='closeLb()' src=''>")
    html.append("<script>"
                "function openLb(s){var l=document.getElementById('lb');l.src=s;l.classList.add('on')}"
                "function closeLb(){document.getElementById('lb').classList.remove('on')}"
                "document.addEventListener('keydown',function(e){if(e.key==='Escape')closeLb()})"
                "</script>")
    html.append("</body></html>")

    try:
        Path(output_path).write_text("\n".join(html), encoding="utf-8")
        return 1
    except Exception as e:
        logger.error(f"Failed to write fav index.html: {e}")
        return 0


def handle_fav_export(params, config: AssistantConfig):
    """POST /api/fav/export — Export favorites as JSON + decrypted images + index.html.

    Query params:
        format: comma-separated list of {json, image, html}. Default: json,image,html.
        dry_run: if "true", only estimate size without actually exporting.
        type_filter: fav type to filter (e.g. "1" for text, "2" for images)
        tag_id: tag ID to filter
        search: search keyword
    """
    reader = _get_wcdb_fav_reader()
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        export_dir = config.fav_export.output_dir or "data/fav_export"
        export_dir = os.path.abspath(export_dir)

        # Parse export formats
        formats_param = params.get("format", ["json,image,html"])
        if isinstance(formats_param, list):
            formats = formats_param[0].split(",") if formats_param else ["json", "image", "html"]
        elif isinstance(formats_param, str):
            formats = formats_param.split(",")
        else:
            formats = ["json", "image", "html"]
        formats = [f.strip().lower() for f in formats if f.strip()]
        if not formats:
            formats = ["json", "image", "html"]

        # Parse filter params
        type_filter = (params.get("type_filter", [""])[0] or "")
        tag_id = (params.get("tag_id", [""])[0] or "")
        search_kw = (params.get("search", [""])[0] or "")
        date_range = (params.get("date_range", [""])[0] or "")

        # Fetch items
        items_raw = reader.get_items(limit=5000)
        items = [_resolve_fav_item_for_export(it) for it in items_raw]

        # Build tag mapping (same as handle_fav_list) for tag filtering
        if tag_id:
            tags_data = reader.get_tags() or []
            bindings_data = reader.get_tag_bindings() or []
            tag_map = {t.get("local_id", ""): t.get("name", "") for t in tags_data}
            fav_tags = {}
            for b in bindings_data:
                tid = b.get("tag_local_id", "")
                fid = b.get("fav_local_id", "")
                if tid and fid:
                    fav_tags.setdefault(str(fid), []).append(
                        {"id": str(tid), "name": tag_map.get(tid, "")}
                    )
            for it in items:
                it["tags"] = fav_tags.get(str(it.get("id", "")), [])

        # Enrich chat records for type 14 items (same as handle_fav_list)
        for it in items:
            if it.get("type") == 14:
                raw_content = it.get("content_raw", "")
                chat_records = it.get("chat_records", [])
                if raw_content and "<datalist>" in raw_content:
                    chat_records = _enrich_chat_records_metadata(chat_records, raw_content)
                if raw_content and "<recordxml>" in raw_content:
                    chat_records = _enrich_nested_chat_records(chat_records, raw_content)
                it["chat_records"] = chat_records

        # Apply filters (same logic as handle_fav_list)
        if type_filter:
            items = [it for it in items if it.get("type") == int(type_filter)]
        if tag_id:
            items = [it for it in items
                     if any(t.get("id") == tag_id for t in (it.get("tags") or []))]
        if search_kw:
            kw = search_kw.lower()
            items = [it for it in items
                     if (it.get("title") or "").lower().find(kw) >= 0
                     or (it.get("content") or "").lower().find(kw) >= 0
                     or (it.get("from_user") or "").lower().find(kw) >= 0]
        if date_range:
            import time as _time
            now = _time.time()
            today_start = _time.mktime(_time.localtime(now)[:3] + (0, 0, 0, 0, 0, 0))
            week_start = now - 7 * 86400
            month_start = now - 30 * 86400
            if date_range == 'today':
                items = [it for it in items if it.get("create_time", 0) >= today_start]
            elif date_range == 'week':
                items = [it for it in items if it.get("create_time", 0) >= week_start]
            elif date_range == 'month':
                items = [it for it in items if it.get("create_time", 0) >= month_start]
            elif date_range == 'older':
                items = [it for it in items if it.get("create_time", 0) < month_start]

        # ── dry_run: estimate size only ──
        dry_run = (params.get("dry_run", [""])[0] or "").lower() == "true"
        if dry_run:
            image_count = _count_fav_images(items)
            result = {"ok": True, **_estimate_export_size(len(items), image_count)}
            result["filtered_count"] = len(items)
            result["total_count"] = len(items_raw)
            return result

        broadcast_event("fav_export_progress", {"status": "started", "formats": formats})

        result = {"ok": True, "export_dir": export_dir, "formats": {}}
        result["total"] = len(items)

        # 1. JSON
        if "json" in formats:
            broadcast_event("fav_export_progress", {"status": "exporting", "format": "json"})
            json_path = os.path.join(export_dir, "favorites.json")
            os.makedirs(export_dir, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            result["formats"]["json"] = {"path": json_path, "count": len(items)}

        # 2. Media (download + decrypt images/videos/voices)
        if "image" in formats or "images" in formats:
            broadcast_event("fav_export_progress", {"status": "exporting", "format": "media"})
            media_stats = _download_fav_media(items, export_dir)
            result["formats"]["image"] = media_stats
            # Re-write JSON with updated local_path info
            if "json" in formats:
                json_path = os.path.join(export_dir, "favorites.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(items, f, ensure_ascii=False, indent=2)

        # 3. Index.html
        if "html" in formats:
            broadcast_event("fav_export_progress", {"status": "exporting", "format": "html"})
            html_path = os.path.join(export_dir, "index.html")
            ok = _build_fav_index_html(items, html_path)
            result["formats"]["html"] = {"path": html_path, "ok": bool(ok)}

        broadcast_event("fav_export_progress", {"status": "completed", "formats": result["formats"]})
        return result
    except Exception as e:
        logger.error(f"Failed to export favorites: {e}")
        broadcast_event("fav_export_progress", {"status": "error", "error": str(e)})
        return {"ok": False, "error": str(e)}


# ── 会话管理 API ───────────────────────────────────────────────────────

# Cache for service account IDs (refreshed once per server lifetime)
_service_ids_cache: set[str] | None = None


def _detect_service_accounts(client, sessions: list) -> set[str]:
    """Detect service accounts (服务号) using biz_info.type first, fallback to verify_flag.

    biz_info.type=1 → service account (微信支付, 信用卡还款 etc.)
    biz_info.type=0 → public account (公众号/订阅号)
    verify_flag & 16 is unreliable (公众号 like 新智元 also has bit 4 set).
    """

    global _service_ids_cache
    if _service_ids_cache is not None:
        return _service_ids_cache

    gh_ids = [s.get("username", "") for s in sessions
              if s.get("username", "").startswith("gh_")
              and re.fullmatch(r'[a-zA-Z0-9_@]+', s.get("username", ""))]
    if not gh_ids:
        _service_ids_cache = set()
        return set()

    service_ids: set[str] = set()
    try:
        # 优先用 biz_info.type 字段（精确区分服务号和公众号）
        quoted = ",".join(f"'{gid}'" for gid in gh_ids)
        sql = f"SELECT username, type FROM biz_info WHERE username IN ({quoted})"
        rows = client.exec_query("contact", "", sql)
        if rows:
            for row in rows:
                biz_type = int(row.get("type", 0) or 0)
                if biz_type == 1:  # 服务号
                    service_ids.add(row.get("username", ""))
            logger.info("Detected service accounts (biz_info.type=1): %d accounts", len(service_ids))
            _service_ids_cache = service_ids
            return service_ids
    except Exception as e:
        logger.warning("biz_info query failed, falling back to verify_flag: %s", e)

    # Fallback: verify_flag & 16 (原有逻辑，不够精确)
    try:
        quoted = ",".join(f"'{gid}'" for gid in gh_ids)
        sql = f"SELECT username, verify_flag FROM contact WHERE username IN ({quoted})"
        rows = client.exec_query("contact", "", sql)
        for row in rows:
            vflag = int(row.get("verify_flag", 0) or 0)
            if vflag & 16:
                service_ids.add(row.get("username", ""))
        if service_ids:
            logger.info("Detected service accounts (verify_flag & 16 fallback): %d accounts", len(service_ids))
    except Exception as e:
        logger.warning("Failed to detect service accounts: %s", e)

    _service_ids_cache = service_ids
    return service_ids


def handle_chat_image(params, config):
    """GET /api/chat/image — Serve chat image/video from local V2 cache or plain mp4."""
    fullmd5 = params.get("fullmd5", [""])[0] if isinstance(params.get("fullmd5"), list) else (params.get("fullmd5", "") or "")
    fullsize_str = params.get("fullsize", [""])[0] if isinstance(params.get("fullsize"), list) else (params.get("fullsize", "") or "")
    talker = params.get("talker", [""])[0] if isinstance(params.get("talker"), list) else (params.get("talker", "") or "")
    create_time_str = params.get("create_time", ["0"])[0] if isinstance(params.get("create_time"), list) else (params.get("create_time", "0") or "0")
    size = params.get("size", ["original"])[0] if isinstance(params.get("size"), list) else (params.get("size", "original") or "original")

    if not fullmd5:
        return {"ok": False, "error": "Missing fullmd5"}

    create_time = int(create_time_str) if create_time_str.isdigit() else 0
    fullsize = int(fullsize_str) if fullsize_str.isdigit() else None

    from src.wechat.v2_cache_decrypt import V2CacheManager
    from pathlib import Path

    # Resolve data_dir and wxid
    data_dir = ""
    wxid = ""
    try:
        client = get_wcdb_client()
        if client:
            data_dir = client._config.get("dbPath", "")
            my_wxid = client._config.get("myWxid", "")
            if my_wxid:
                wxid = my_wxid
    except Exception:
        pass

    # V2 cache (images)
    manager = V2CacheManager.get_instance(data_dir)
    data = None
    if talker:
        data = manager.decrypt_chat_image(fullmd5, talker, create_time, wxid, size)
    if not data:
        data = manager.decrypt_fav_image(0, wxid, size="original", fullmd5=fullmd5, fullsize=fullsize)

    # Plain mp4 video (msg/video/{yyyy-MM}/{fullmd5}.mp4)
    if not data and create_time and wxid:
        try:
            from datetime import datetime
            dt = datetime.fromtimestamp(create_time)
            ym = dt.strftime("%Y-%m")
            video_dir = Path(data_dir) / wxid / "msg" / "video" / ym
            if video_dir.exists():
                video_path = video_dir / f"{fullmd5}.mp4"
                if video_path.exists():
                    data = video_path.read_bytes()
        except Exception:
            pass

    if data:
        # Detect content type
        content_type = "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            content_type = "image/png"
        elif data[:4] == b"GIF8":
            content_type = "image/gif"
        elif data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
            content_type = "image/webp"
        elif len(data) >= 8 and data[4:8] == b"ftyp":
            content_type = "video/mp4"

        return {
            "_binary": True,
            "data": data,
            "content_type": content_type,
            "content_length": len(data),
        }

    return {"ok": False, "error": "not found"}


def handle_chat_sessions(params, config: AssistantConfig):
    """GET /api/chat/sessions — List chat sessions with optional keyword search."""
    logger.info("[API-TRACE] handle_chat_sessions ENTER thread=%s", threading.current_thread().name)
    client = get_wcdb_client()
    logger.info("[API-TRACE] handle_chat_sessions got client=%s", client is not None)
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        t0 = time.monotonic()
        keyword = params.get("keyword", [""])[0] if params.get("keyword") else ""

        # Get recent sessions
        logger.info("[API-TRACE] /api/chat/sessions: calling get_sessions thread=%s", threading.current_thread().name)
        sessions = client.get_sessions(limit=500)
        logger.info("[API-TRACE] /api/chat/sessions: get_sessions took %.0fms, got %d sessions",
                    (time.monotonic() - t0) * 1000, len(sessions) if sessions else 0)

        # If keyword provided, also search contacts and merge
        if keyword:
            t1 = time.monotonic()
            contacts = client.get_contacts(keyword=keyword, limit=200)
            logger.info("[API-TRACE] /api/chat/sessions: get_contacts took %.0fms",
                        (time.monotonic() - t1) * 1000)
            existing = {s.get("username", "") for s in sessions}
            for c in contacts:
                uname = c.get("username", "")
                if uname and uname not in existing:
                    sessions.append({
                        "username": uname,
                        "displayName": c.get("nickname", "") or c.get("displayName", ""),
                        "nTime": 0,
                    })

        # Resolve display names for sessions that don't have them
        unnamed = [s.get("username", "") for s in sessions
                   if s.get("username") and not s.get("displayName")]
        if unnamed:
            t2 = time.monotonic()
            names = client.get_display_names(unnamed)
            logger.info("[API-TRACE] /api/chat/sessions: get_display_names took %.0fms (%d names)",
                        (time.monotonic() - t2) * 1000, len(unnamed))
            for s in sessions:
                uname = s.get("username", "")
                if not s.get("displayName") and uname in names:
                    s["displayName"] = names[uname]

        # Resolve avatar URLs for all sessions
        all_usernames = [s.get("username", "") for s in sessions if s.get("username")]
        if all_usernames:
            try:
                t3 = time.monotonic()
                avatar_map = client.get_avatar_urls(all_usernames)
                logger.info("[API-TRACE] /api/chat/sessions: get_avatar_urls took %.0fms",
                            (time.monotonic() - t3) * 1000)
                for s in sessions:
                    uname = s.get("username", "")
                    if uname in avatar_map:
                        s["avatarUrl"] = avatar_map[uname]
            except Exception:
                pass

        # Also include unread_count from session data
        for s in sessions:
            s["unread_count"] = int(s.get("unread_count", 0) or 0)
            s["nTime"] = int(s.get("nTime", s.get("last_timestamp", 0) or 0))

        # Resolve folded/muted state for group chats from contact.extra_buffer.
        # This is how WeChat marks groups folded into "折叠的群聊".
        chatroom_ids = [s.get("username", "") for s in sessions
                        if s.get("username", "").endswith("@chatroom")]
        if chatroom_ids:
            try:
                t4 = time.monotonic()
                status_map = client.get_contact_status(chatroom_ids)
                logger.info("[API-TRACE] /api/chat/sessions: get_contact_status took %.0fms",
                            (time.monotonic() - t4) * 1000)
                for s in sessions:
                    uname = s.get("username", "")
                    if uname in status_map:
                        state = status_map.get(uname) or {}
                        s["isFolded"] = bool(state.get("isFolded"))
                        s["isMuted"] = bool(state.get("isMuted"))
            except Exception as e:
                logger.warning("Failed to load contact status: %s", e)

        # Classify sessions into groups:
        # 1. Normal sessions (individuals, group chats)
        # 2. Public accounts (gh_*) — should be folded into "公众号"
        #    Service accounts (服务号, verify_flag & 16) go to normal_sessions
        # 3. Folded group chats — @placeholder_foldgroup
        # 4. Service accounts (服务号) — 微信支付, 信用卡还款 etc.
        #    These are gh_* but NOT content publishers, keep in normal list.
        service_gh_ids = _detect_service_accounts(client, sessions)

        normal_sessions = []
        oa_sessions = []       # 公众号/订阅号 (gh_* minus service accounts)
        folded_sessions = []   # 折叠的群聊 (from @placeholder_foldgroup)

        WECHAT_INTERNAL_USERS = {'brandsessionholder', 'brandservicesessionholder'}

        fold_placeholder = None
        for s in sessions:
            uname = s.get("username", "")
            if uname in WECHAT_INTERNAL_USERS:
                continue  # 跳过微信内部占位符
            elif uname == "@placeholder_foldgroup":
                fold_placeholder = s
            elif uname.startswith("gh_"):
                if uname in service_gh_ids:
                    # 服务号 → 放入 normal sessions (不在公众号折叠区)
                    normal_sessions.append(s)
                else:
                    # 真正的公众号/订阅号
                    oa_sessions.append(s)
            elif uname.endswith("@chatroom") and s.get("isFolded"):
                # 被微信折叠的群聊 → 不在主列表显示，点击折叠入口后展示
                folded_sessions.append(s)
            else:
                normal_sessions.append(s)

        # Build 公众号 fold entry if there are OA sessions
        oa_entry = None
        if oa_sessions:
            latest_oa = max(oa_sessions, key=lambda s: s.get("nTime") or 0)
            latest_display = latest_oa.get("displayName", "") or latest_oa.get("username", "")
            oa_entry = {
                "username": "@placeholder_oa",
                "displayName": "公众号",
                "nTime": latest_oa.get("nTime", 0),
                "unread_count": sum(s.get("unread_count", 0) for s in oa_sessions),
                "avatarUrl": "",
                "isFoldGroup": True,
                "foldType": "oa",
                "summary": f"{latest_display}: ..." if latest_display else f"{len(oa_sessions)} 个公众号",
            }

        # Build 折叠的群聊 fold entry
        # Use actual folded_sessions if we have them (from isFolded flag),
        # otherwise fall back to fold_placeholder metadata.
        folded_entry = None
        if folded_sessions:
            latest_folded = max(folded_sessions, key=lambda s: s.get("nTime") or 0)
            latest_display = latest_folded.get("displayName", "") or latest_folded.get("username", "")
            folded_entry = {
                "username": "@placeholder_foldgroup",
                "displayName": "折叠的群聊",
                "nTime": latest_folded.get("nTime", 0),
                "unread_count": sum(s.get("unread_count", 0) for s in folded_sessions),
                "avatarUrl": "",
                "isFoldGroup": True,
                "foldType": "foldgroup",
                "summary": f"{latest_display}: ..." if latest_display else f"{len(folded_sessions)} 个群聊",
            }
        elif fold_placeholder:
            folded_entry = {
                "username": "@placeholder_foldgroup",
                "displayName": "折叠的群聊",
                "nTime": fold_placeholder.get("nTime", 0),
                "unread_count": fold_placeholder.get("unread_count", 0),
                "avatarUrl": "",
                "isFoldGroup": True,
                "foldType": "foldgroup",
                "summary": fold_placeholder.get("last_sender_display_name", "") or f"{fold_placeholder.get('unread_count', 0)} 条未读",
            }

        # If keyword, filter sessions by display name or username
        if keyword:
            kw = keyword.lower()
            normal_sessions = [s for s in normal_sessions
                        if kw in (s.get("displayName", "") or "").lower()
                        or kw in (s.get("username", "") or "").lower()]
            # Also search in OA/folded sessions
            oa_sessions = [s for s in oa_sessions
                        if kw in (s.get("displayName", "") or "").lower()
                        or kw in (s.get("username", "") or "").lower()]
            if oa_sessions:
                oa_entry = None
                normal_sessions.extend(oa_sessions)

        # Sort by nTime (most recent first)
        normal_sessions.sort(key=lambda s: s.get("nTime") or 0, reverse=True)

        # Insert fold entries at the right position (sorted by nTime)
        if oa_entry:
            normal_sessions.append(oa_entry)
        if folded_entry:
            normal_sessions.append(folded_entry)
        normal_sessions.sort(key=lambda s: s.get("nTime") or 0, reverse=True)

        # Add myWxid for is_self detection
        my_wxid = client._config.get("myWxid", "")

        logger.info("[API-TRACE] /api/chat/sessions: TOTAL %.0fms, %d sessions returned",
                    (time.monotonic() - t0) * 1000, len(normal_sessions))

        return {"ok": True, "data": normal_sessions, "myWxid": my_wxid,
                "oaSessions": oa_sessions if not keyword else [],
                "foldedSessions": folded_sessions}
    except Exception as e:
        logger.error(f"Failed to list chat sessions: {e}")
        return {"ok": False, "error": str(e)}


def _decompress_content(content: str) -> str:
    """Decompress zstd-compressed hex-encoded message content.

    WCDB stores most message content as hex-encoded zstd-compressed data.
    The zstd magic number is 0x28B52FFD, which appears as "28b52ffd" in hex.
    After decompression, the content is either plain text, XML, or
    sender_id:\\ncontent (group chat format).
    """
    if not content or len(content) < 16:
        return content

    # Quick check: is this hex-encoded data?
    try:
        is_hex = all(c in '0123456789abcdef' for c in content[:100].lower())
    except Exception:
        return content

    if not is_hex:
        return content

    # Check for zstd magic (28b52ffd) at the start
    if not content.lower().startswith('28b52ffd'):
        return content

    try:
        raw = bytes.fromhex(content)
        import zstandard
        dctx = zstandard.ZstdDecompressor()
        decompressed = dctx.decompress(raw, max_output_size=10 * 1024 * 1024)
        text = decompressed.decode('utf-8', errors='replace')
        # If >20% replacement chars, decompression likely produced garbage
        replacement_count = text.count('�')
        if len(text) > 0 and replacement_count > len(text) * 0.2:
            return content
        return text
    except Exception:
        return content


def _strip_wxid_prefix(content: str) -> str:
    """Strip sender ID prefix from group chat message content.

    In WeChat group chats, messages are stored as 'sender_id:\\nactual text'
    or 'sender_id:actual text'. The sender ID can be:
    - wxid_xxx (WeChat ID)
    - qq123456789 (QQ number)
    - Other alphanumeric IDs

    This prefix should not be displayed to the user.
    """
    import re as _re
    # Match sender_id followed by colon at the start, optionally followed by newline
    # Sender IDs can be: wxid_xxx, qq123, 12345@openim, user@domain, etc.
    return _re.sub(r'^[a-zA-Z0-9_@.\-]+:\n?', '', content)


def _extract_system_msg_text(content: str) -> str:
    """Extract readable text from WeChat system/app message XML.

    System messages (type 10000) may be plain text or XML like:
      <sysmsg type="revokemsg"><revokemsg><content>"xxx" 撤回了一条消息</content>...</sysmsg>
    App messages (various localTypes) contain XML with <title> tags.

    For unrecognizable sysmsg types (e.g. mmchatroomtopmsg, mmchatroominvitemsg),
    returns empty string to avoid leaking raw XML to the frontend.

    Returns the readable text portion, or empty string if nothing meaningful found.
    """
    if not content or not content.strip().startswith('<'):
        return content
    import re as _re
    # Try <content>...</content> inside <sysmsg> (system messages)
    match = _re.search(r'<content>(.*?)</content>', content, _re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try <plain>...</plain> (some system messages use this)
    match = _re.search(r'<plain>(.*?)</plain>', content, _re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try <title>...</title> (app messages, forwarded chat records, etc.)
    match = _re.search(r'<title>(.*?)</title>', content, _re.DOTALL)
    if match:
        title = match.group(1).strip()
        if title:
            return title
    # Try <nickname>...</nickname> for mmchatroomtopmsg (群置顶通知)
    # Format: <sysmsg type="mmchatroomtopmsg"><mmchatroomtopmsg><nickname>xxx</nickname>...</mmchatroomtopmsg></sysmsg>
    match = _re.search(r'<nickname>(.*?)</nickname>', content, _re.DOTALL)
    if match:
        nickname = match.group(1).strip()
        if nickname:
            return f"{nickname} 置顶了一条消息"
    # Unrecognizable XML — return empty to avoid leaking raw XML
    return ""


def _parse_msg_media(msg: dict) -> dict:
    """Extract media metadata from a chat message.

    Handles both XML-based content (text messages, type 49) and
    protobuf/hex content (images type 3, videos type 43) by using
    packed_info_data for MD5 extraction.
    """
    import re as _re
    import html as _html
    content = msg.get("content", "") or msg.get("message_content", "") or ""
    content = _strip_wxid_prefix(_decompress_content(content))
    # WeChat 4.x sometimes returns HTML-encoded XML (&lt; instead of <)
    if '&lt;' in content and '<' not in content[:100]:
        content = _html.unescape(content)
    local_type = int(msg.get("local_type", msg.get("localType", msg.get("msg_type", 1))) or 1)
    media = {"images": [], "voice": None, "link": None}

    # WeChat 4.x encodes app messages with high-bit flags: e.g. 0x2100000031, 0x1300000031
    # All share low byte 0x31 (=49), but some wrap <img>/<videomsg> instead of <appmsg>.
    # Check content structure for accurate classification.
    is_appmsg_code = (local_type & 0xFF) == 0x31
    if is_appmsg_code and content and content.lstrip().startswith('<'):
        if '<appmsg' in content.lower():
            is_appmsg_type = True
        elif '<img' in content[:200].lower() and '<videomsg' not in content[:200].lower():
            local_type = 3  # treat as image
            is_appmsg_type = False
        elif '<videomsg' in content[:200].lower():
            local_type = 43  # treat as video
            is_appmsg_type = False
        else:
            is_appmsg_type = True  # default: treat as appmsg
    else:
        is_appmsg_type = (local_type == 49)

    if local_type == 3:
        # Image message: content is hex-encoded protobuf, NOT XML.
        # Extract MD5 from packed_info_data field (WeFlow pattern).
        # packed_info_data is hex-encoded and contains the MD5 as a 32-char hex string.
        img_info = {}
        packed = msg.get("packed_info_data", "") or ""
        if packed:
            try:
                packed_raw = bytes.fromhex(packed) if all(c in '0123456789abcdef' for c in packed.lower()) else packed.encode('utf-8', errors='replace')
                packed_text = packed_raw.decode('utf-8', errors='replace')
                md5_match = _re.search(r'([a-f0-9]{32})', packed_text)
                if md5_match:
                    img_info["fullmd5"] = md5_match.group(1)
            except Exception:
                pass

        # Fallback: try XML parsing (older WeChat versions may use XML content)
        if not img_info.get("fullmd5") and content:
            # Try decoding hex content first
            try:
                if all(c in '0123456789abcdef' for c in content[:100].lower()):
                    decoded = bytes.fromhex(content).decode('utf-8', errors='replace')
                else:
                    decoded = content
            except Exception:
                decoded = content

            md5_match = _re.search(r'<img[^>]+md5="([a-f0-9]{32})"', decoded)
            fullmd5_match = _re.search(r'<img[^>]+fullmd5="([a-f0-9]{32})"', decoded)
            if fullmd5_match:
                img_info["fullmd5"] = fullmd5_match.group(1)
            elif md5_match:
                img_info["fullmd5"] = md5_match.group(1)

            fullsize_match = _re.search(r'<img[^>]+fullsize="(\d+)"', decoded)
            if fullsize_match:
                img_info["fullsize"] = int(fullsize_match.group(1))

        if img_info:
            media["images"] = [img_info]

    elif local_type == 43:
        # Video message: similar to image, use packed_info_data for MD5
        img_info = {"is_video": True}
        packed = msg.get("packed_info_data", "") or ""
        if packed:
            try:
                packed_raw = bytes.fromhex(packed) if all(c in '0123456789abcdef' for c in packed.lower()) else packed.encode('utf-8', errors='replace')
                packed_text = packed_raw.decode('utf-8', errors='replace')
                md5_match = _re.search(r'([a-f0-9]{32})', packed_text)
                if md5_match:
                    img_info["fullmd5"] = md5_match.group(1)
            except Exception:
                pass

        # Fallback: XML
        if not img_info.get("fullmd5") and content:
            try:
                if all(c in '0123456789abcdef' for c in content[:100].lower()):
                    decoded = bytes.fromhex(content).decode('utf-8', errors='replace')
                else:
                    decoded = content
            except Exception:
                decoded = content

            md5_match = _re.search(r'<videomsg[^>]+md5="([a-f0-9]{32})"', decoded, _re.IGNORECASE)
            fullmd5_match = _re.search(r'<img[^>]+fullmd5="([a-f0-9]{32})"', decoded)
            if fullmd5_match:
                img_info["fullmd5"] = fullmd5_match.group(1)
            elif md5_match:
                img_info["fullmd5"] = md5_match.group(1)

        if img_info.get("fullmd5"):
            media["images"] = [img_info]

    elif local_type == 34:
        # Voice message: metadata already in message fields
        media["voice"] = {
            "local_id": msg.get("local_id", msg.get("localId", 0)),
            "server_id": msg.get("server_id", msg.get("svrId", msg.get("serverId", 0))),
            "create_time": msg.get("create_time", msg.get("createTime", 0)),
        }

    elif is_appmsg_type:
        # App message: content is XML, extract link URL and title
        # Decode hex content if needed
        try:
            if content and all(c in '0123456789abcdef' for c in content[:100].lower()):
                decoded = bytes.fromhex(content).decode('utf-8', errors='replace')
            else:
                decoded = content
        except Exception:
            decoded = content

        # Extract appmsg sub-type (strip <refermsg> to avoid matching nested <type>)
        appmsg_inner = _re.sub(r'<refermsg[\s\S]*?</refermsg>', '', decoded, flags=_re.IGNORECASE)
        type_match = _re.search(r'<type>(.*?)</type>', appmsg_inner, _re.IGNORECASE)
        appmsg_type = type_match.group(1).strip() if type_match else ''

        # --- Quote/Reply message (appmsg type=57) ---
        if appmsg_type == '57':
            refer_match = _re.search(r'<refermsg>([\s\S]*?)</refermsg>', decoded, _re.IGNORECASE)
            if refer_match:
                refer_xml = refer_match.group(1)
                refer_content = _re.search(r'<content>(.*?)</content>', refer_xml, _re.DOTALL)
                refer_sender = _re.search(r'<displayname>(.*?)</displayname>', refer_xml, _re.DOTALL)
                refer_type = _re.search(r'<type>(.*?)</type>', refer_xml, _re.DOTALL)
                media["quote"] = {
                    "content": (refer_content.group(1).strip() if refer_content else ""),
                    "sender": (refer_sender.group(1).strip() if refer_sender else ""),
                    "type": (refer_type.group(1).strip() if refer_type else "1"),
                }
            # In WeChat 4.x the reply text may be in <title> (not <des>)
            des_match = _re.search(r'<des>(.*?)</des>', decoded, _re.DOTALL)
            title_match_57 = _re.search(r'<title>(.*?)</title>', decoded, _re.DOTALL)
            if des_match and des_match.group(1).strip():
                media["reply_text"] = des_match.group(1).strip()
            elif title_match_57 and title_match_57.group(1).strip():
                # In some versions, <title> contains the actual reply text
                # But <title> inside <refermsg> is the quoted text, so we need
                # the outer <title> (which comes before <refermsg>)
                # The regex above with DOTALL matches first occurrence = outer <title>
                media["reply_text"] = title_match_57.group(1).strip()

        # --- Chat records (appmsg type=19) ---
        elif appmsg_type == '19':
            title_match = _re.search(r'<title>(.*?)</title>', decoded, _re.DOTALL)
            record_match = _re.search(r'<recorditem>([\s\S]*?)</recorditem>', decoded, _re.IGNORECASE)
            chat_records = []
            if record_match:
                record_inner = record_match.group(1)
                # Parse <dataitem> elements — they contain rich metadata
                # (head_url, dataid, voicelength, cdn URLs, etc.)
                data_items = list(_re.finditer(
                    r'<dataitem[^>]*>([\s\S]*?)</dataitem>',
                    record_inner, _re.IGNORECASE
                ))

                if data_items:
                    # Build rich records from <dataitem> XML
                    for dm in data_items:
                        item_xml = dm.group(1)
                        # Extract attributes from dataitem XML
                        datatype = _re.search(r'<datatype>(.*?)</datatype>', item_xml, _re.DOTALL)
                        datadesc = _re.search(r'<datadesc>(.*?)</datadesc>', item_xml, _re.DOTALL)
                        sourcename = _re.search(r'<(?:datasrcname|sourcename)>(.*?)</(?:datasrcname|sourcename)>', item_xml, _re.DOTALL)
                        sourceheadurl = _re.search(r'<sourceheadurl>(.*?)</sourceheadurl>', item_xml, _re.DOTALL)
                        datatitle = _re.search(r'<datatitle>(.*?)</datatitle>', item_xml, _re.DOTALL)
                        dataid = _re.search(r'<dataid>(.*?)</dataid>', item_xml, _re.DOTALL)
                        voicelength = _re.search(r'<voicelength>(.*?)</voicelength>', item_xml, _re.DOTALL)
                        sourcetime = _re.search(r'<sourcetime>(.*?)</sourcetime>', item_xml, _re.DOTALL)
                        cdn_dataurl = _re.search(r'<cdn_dataurl>(.*?)</cdn_dataurl>', item_xml, _re.DOTALL)
                        cdn_datakey = _re.search(r'<cdn_datakey>(.*?)</cdn_datakey>', item_xml, _re.DOTALL)
                        fullmd5 = _re.search(r'<fullmd5>(.*?)</fullmd5>', item_xml, _re.DOTALL)
                        fullsize = _re.search(r'<fullsize>(.*?)</fullsize>', item_xml, _re.DOTALL)
                        link_url = _re.search(r'<link_url>(.*?)</link_url>', item_xml, _re.DOTALL)
                        # Also try <fromusr> for sender wxid
                        fromusr = _re.search(r'<fromusr>(.*?)</fromusr>', item_xml, _re.DOTALL)

                        dtype = datatype.group(1).strip() if datatype else "1"
                        # Map type codes: WeChat uses 1/2/3/5/6/8/34/43
                        # We normalize 34→3 (voice), 43→4 (video) for consistency
                        if dtype == "34":
                            dtype = "3"
                        elif dtype == "43":
                            dtype = "4"

                        # WeChat sometimes mis-tags dataitems: images may have
                        # datatype=1 but include CDN URLs / fullmd5. Fix by
                        # inferring actual type from available fields.
                        has_cdn = (cdn_dataurl and cdn_dataurl.group(1).strip()
                                   and cdn_datakey and cdn_datakey.group(1).strip())
                        has_md5 = fullmd5 and fullmd5.group(1).strip()
                        has_voice = voicelength and voicelength.group(1).strip()
                        if dtype == "1" and (has_cdn or has_md5):
                            dtype = "2"  # Image with CDN data
                        if dtype == "1" and has_voice:
                            dtype = "3"  # Voice with duration

                        rec = {
                            "type": dtype,
                            "src_name": sourcename.group(1).strip() if sourcename else "",
                            "desc": datadesc.group(1).strip() if datadesc else "",
                            "time": sourcetime.group(1).strip() if sourcetime else "",
                        }
                        # Save fromusr wxid for later batch resolution (not used as src_name
                        # because it's a wxid like "wxid_abc", not a display name)
                        if fromusr and fromusr.group(1).strip():
                            rec["_fromusr"] = fromusr.group(1).strip()
                        # Head URL (avatar)
                        if sourceheadurl and sourceheadurl.group(1).strip():
                            rec["head_url"] = sourceheadurl.group(1).strip()
                        # Data ID (for voice/image lookup)
                        if dataid and dataid.group(1).strip():
                            rec["dataid"] = dataid.group(1).strip()
                        # Voice length (duration in ms)
                        if voicelength and voicelength.group(1).strip():
                            rec["duration"] = int(voicelength.group(1).strip())
                        # Image CDN URLs (for actual image loading)
                        if cdn_dataurl and cdn_dataurl.group(1).strip():
                            rec["cdn_dataurl"] = cdn_dataurl.group(1).strip()
                        if cdn_datakey and cdn_datakey.group(1).strip():
                            rec["cdn_datakey"] = cdn_datakey.group(1).strip()
                        # Image MD5/size for V2 cache decryption
                        if fullmd5 and fullmd5.group(1).strip():
                            rec["fullmd5"] = fullmd5.group(1).strip()
                        if fullsize and fullsize.group(1).strip():
                            rec["fullsize"] = int(fullsize.group(1).strip())
                        # Link URL and title for type=5 (link)
                        if link_url and link_url.group(1).strip():
                            rec["link_url"] = link_url.group(1).strip()
                        if datatitle and datatitle.group(1).strip():
                            rec["link_title"] = datatitle.group(1).strip()

                        chat_records.append(rec)
                else:
                    # Fallback: parse <desc> text line-by-line
                    desc_match = _re.search(r'<desc>(.*?)</desc>', record_inner, _re.DOTALL)
                    if desc_match:
                        desc_text = desc_match.group(1).strip()
                        for line in desc_text.split('\n'):
                            line = line.strip()
                            if not line:
                                continue
                            parts = line.split(': ', 1)
                            if len(parts) == 2:
                                src_name, content = parts
                            else:
                                src_name = ''
                                content = line
                            msg_type = "1"
                            if content.startswith('[图片]') or content.startswith('[Image]'):
                                msg_type = "2"
                                content = content[4:].strip()
                            elif content.startswith('[语音]') or content.startswith('[Voice]'):
                                msg_type = "3"
                                content = content[4:].strip()
                            elif content.startswith('[视频]') or content.startswith('[Video]'):
                                msg_type = "4"
                                content = content[4:].strip()
                            elif content.startswith('[链接]') or content.startswith('[Link]'):
                                msg_type = "5"
                                content = content[4:].strip()
                            elif content.startswith('[文件]') or content.startswith('[File]'):
                                msg_type = "8"
                                content = content[4:].strip()
                            chat_records.append({
                                "type": msg_type,
                                "src_name": src_name,
                                "time": "",
                                "desc": content,
                            })

            media["chat_records"] = {
                "title": title_match.group(1).strip() if title_match else "聊天记录",
                "items": chat_records,
            }

        # --- Regular link/file (other appmsg types) ---
        else:
            url_match = _re.search(r'<url><!\[CDATA\[(.*?)\]\]></url>', decoded, _re.DOTALL)
            if not url_match:
                url_match = _re.search(r'<url>(.*?)</url>', decoded, _re.DOTALL)
            title_match = _re.search(r'<title><!\[CDATA\[(.*?)\]\]></title>', decoded, _re.DOTALL)
            if not title_match:
                title_match = _re.search(r'<title>(.*?)</title>', decoded, _re.DOTALL)
            filename_match = _re.search(r'<filename><!\[CDATA\[(.*?)\]\]></filename>', decoded, _re.DOTALL)
            if not filename_match:
                filename_match = _re.search(r'<filename>(.*?)</filename>', decoded, _re.DOTALL)

            if url_match:
                media["link"] = {
                    "url": url_match.group(1),
                    "title": title_match.group(1) if title_match else "",
                }
            elif title_match:
                # App messages without <url> (e.g. Bot/service account text appmsg type=1)
                # still have a <title> with readable content — expose it so the frontend
                # can render something instead of a blank bubble.
                media["link"] = {
                    "url": "",
                    "title": title_match.group(1),
                }
            if filename_match:
                media["link"] = media.get("link") or {}
                media["link"]["filename"] = filename_match.group(1)

    return media


def handle_chat_messages(params, config: AssistantConfig):
    """GET /api/chat/messages — Get messages for a specific chat session."""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        talker = params.get("talker", [""])[0] if params.get("talker") else ""
        if not talker:
            return {"ok": False, "error": "Missing talker parameter"}

        limit = int(params.get("limit", [200])[0]) if params.get("limit") else 200
        offset = int(params.get("offset", [0])[0]) if params.get("offset") else 0
        start_time = int(params.get("start_time", [0])[0]) if params.get("start_time") else 0
        end_time = int(params.get("end_time", [0])[0]) if params.get("end_time") else 0

        messages = client.get_messages(talker, limit=limit, offset=offset)

        # Time filtering
        if start_time or end_time:
            filtered = []
            for m in messages:
                ct = int(m.get("create_time", m.get("createTime", 0)) or 0)
                if start_time and ct < start_time:
                    continue
                if end_time and ct > end_time:
                    continue
                filtered.append(m)
            messages = filtered

        # Batch resolve sender display names
        sender_ids = set()
        for m in messages:
            sid = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
            if sid:
                sender_ids.add(sid)
        # Also add talker itself
        sender_ids.add(talker)
        name_map = {}
        avatar_map = {}
        if sender_ids:
            name_map = client.get_display_names(list(sender_ids))
            try:
                avatar_map = client.get_avatar_urls(list(sender_ids))
            except Exception:
                pass

        # My wxid
        my_wxid = client._config.get("myWxid", "")

        # Normalize and enrich messages
        result_messages = []
        for m in messages:
            # DLL returns snake_case field names (local_type, not localType)
            local_type = int(m.get("local_type", m.get("localType", m.get("msg_type", 1))) or 1)
            sender = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
            create_time = int(m.get("create_time", m.get("createTime", 0)) or 0)
            # For text messages (local_type=1), content is plain text
            # For image/video (local_type=3/43), content may be XML after decompression
            # For app messages (local_type=49), content is XML
            content = m.get("message_content", "") or m.get("content", "") or ""
            content = _decompress_content(content)
            # WeChat 4.x sometimes returns HTML-encoded XML (&lt; instead of <)
            if '&lt;' in content and '<' not in content[:100]:
                content = __import__('html').unescape(content)
            local_id = m.get("local_id", m.get("localId", 0))
            server_id = m.get("server_id", m.get("svrId", m.get("serverId", 0)))

            # For text messages, content is readable text; for media, show a label
            display_content = content
            if local_type in (3, 43) and content:
                display_content = "[图片]" if local_type == 3 else "[视频]"
            elif local_type == 34:
                display_content = "[语音]"

            # Strip sender_id: prefix from group chat text/system/app messages
            # In group chats, decompressed content often starts with 'sender_id:\n'
            # This applies to ALL types (text, app messages, system messages, etc.)
            if talker.endswith('@chatroom') and display_content:
                display_content = _strip_wxid_prefix(display_content)
            # Extract readable text from XML content (system msgs, app msgs, etc.)
            if display_content and display_content.lstrip().startswith('<'):
                display_content = _extract_system_msg_text(display_content)

            # Normalize type: WeChat 4.x uses new type codes (e.g. 0x2100000031 for appmsg)
            # All share low byte 0x31 (=49), but some are img/video variants.
            # Use content structure AND media parse results for accurate classification.
            is_appmsg_code = (local_type & 0xFF) == 0x31
            if is_appmsg_code and content and content.lstrip().startswith('<'):
                # Content-based type normalization for WeChat 4.x appmsg-type codes
                if '<appmsg' in content.lower():
                    normalized_type = 49
                elif '<img' in content[:200].lower() and '<videomsg' not in content[:200].lower():
                    normalized_type = 3
                elif '<videomsg' in content[:200].lower():
                    normalized_type = 43
                else:
                    normalized_type = 49  # default: treat as appmsg
            elif is_appmsg_code:
                # Content is plain text (already extracted), but type code indicates appmsg.
                # _parse_msg_media already parsed the raw XML — check its results.
                normalized_type = 49  # appmsg codes with text content = app message
            elif local_type in (1, 3, 34, 43, 47, 10000):
                normalized_type = local_type
            else:
                normalized_type = local_type

            # Parse media metadata
            media = _parse_msg_media(m)

            # For quote/reply messages (type 49, appmsg type=57), display the
            # actual reply text (from <des>) instead of the quoted text (<title>)
            if media.get("reply_text"):
                display_content = media["reply_text"]

            result_messages.append({
                "local_id": local_id,
                "server_id": server_id,
                "localType": normalized_type,
                "sender": sender,
                "sender_name": name_map.get(sender, sender),
                "sender_avatar": avatar_map.get(sender, ""),
                "is_self": sender == my_wxid,
                "content": display_content,
                "create_time": create_time,
                "images": media["images"],
                "voice": media["voice"],
                "link": media["link"],
                "quote": media.get("quote"),
                "reply_text": media.get("reply_text"),
                "chat_records": media.get("chat_records"),
            })

        # Sort chronologically (oldest first, newest at bottom)
        result_messages.sort(key=lambda m: m.get("create_time", 0))

        # ── Enrich chat record sub-messages with names & avatars ──
        # When someone forwards another's chat records, <datasrcname> and
        # <sourceheadurl> are often empty, but <fromusr> (wxid) is present.
        # We saved it as _fromusr in Step 1 — now batch-resolve via DLL.
        cr_wxids = set()
        for msg in result_messages:
            cr_data = msg.get("chat_records")
            if not cr_data:
                continue
            for item in cr_data.get("items", []):
                wxid = item.get("_fromusr", "")
                if wxid and not item.get("src_name"):
                    cr_wxids.add(wxid)
                elif wxid and not item.get("head_url"):
                    cr_wxids.add(wxid)

        if cr_wxids:
            try:
                cr_name_map = client.get_display_names(list(cr_wxids))
            except Exception:
                cr_name_map = {}
            try:
                cr_avatar_map = client.get_avatar_urls(list(cr_wxids))
            except Exception:
                cr_avatar_map = {}
            logger.debug(f"chat_records name resolution: wxids={list(cr_wxids)}, names={cr_name_map}, avatars_keys={list(cr_avatar_map.keys())}")

            for msg in result_messages:
                cr_data = msg.get("chat_records")
                if not cr_data:
                    continue
                for item in cr_data.get("items", []):
                    wxid = item.get("_fromusr", "")
                    if wxid:
                        if not item.get("src_name"):
                            item["src_name"] = cr_name_map.get(wxid, "")
                        if not item.get("head_url"):
                            item["head_url"] = cr_avatar_map.get(wxid, "")
                        # Clean up: _fromusr is internal, not needed by frontend
                        item.pop("_fromusr", None)

        return {"ok": True, "data": result_messages, "total": len(result_messages), "myWxid": my_wxid}
    except Exception as e:
        logger.error(f"Failed to get chat messages: {e}")
        return {"ok": False, "error": str(e)}



def _download_chat_media(messages: list[dict], export_dir: str, talker: str, my_wxid: str, broadcast=None) -> dict:
    """Download and decrypt media for chat messages during export.

    Returns stats dict.

    Safety: limits total downloads and wall-clock time to prevent
    runaway exports on huge conversations.
    """
    MAX_DOWNLOAD_ITEMS = 1000  # Max images+voices to download
    MAX_DOWNLOAD_SECONDS = 300  # 5-minute wall-clock timeout

    stats = {"downloaded": 0, "errors": 0, "images": 0, "voices": 0, "videos": 0, "skipped": 0}
    start_time = time.monotonic()

    images_dir = os.path.join(export_dir, "images")
    voice_dir = os.path.join(export_dir, "voice")
    videos_dir = os.path.join(export_dir, "videos")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(voice_dir, exist_ok=True)
    os.makedirs(videos_dir, exist_ok=True)

    # Auto-detect data_dir and wxid from WCDB client config (primary) or env (fallback)
    from src.wechat.v2_cache_decrypt import V2CacheManager
    from pathlib import Path as _P
    data_dir = os.getenv("WECHAT_DATA_DIR", "")
    wxid = ""
    try:
        _client_cfg = get_wcdb_client()
        if _client_cfg:
            _dd = _client_cfg._config.get("dbPath", "")
            _wx = _client_cfg._config.get("myWxid", "")
            if _dd:
                data_dir = _dd
            if _wx:
                wxid = _wx
    except Exception:
        pass
    # Fallback: scan data_dir for wxid_ directories
    if not wxid and data_dir:
        wxid_dirs = sorted(
            [d for d in _P(data_dir).iterdir() if d.is_dir() and d.name.startswith("wxid_")],
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        if wxid_dirs:
            wxid = wxid_dirs[0].name

    client = get_wcdb_client()
    manager = V2CacheManager.get_instance(data_dir) if data_dir else None

    for i, msg in enumerate(messages):
        # Safety: check download limits
        if stats["downloaded"] >= MAX_DOWNLOAD_ITEMS:
            stats["skipped"] = len(messages) - i
            logger.warning("Chat export media: hit MAX_DOWNLOAD_ITEMS=%d, skipping %d remaining",
                           MAX_DOWNLOAD_ITEMS, stats["skipped"])
            break
        if time.monotonic() - start_time > MAX_DOWNLOAD_SECONDS:
            stats["skipped"] = len(messages) - i
            logger.warning("Chat export media: hit %ds timeout, skipping %d remaining",
                           MAX_DOWNLOAD_SECONDS, stats["skipped"])
            break

        local_type = msg.get("localType", 1)
        local_id = msg.get("local_id", 0)

        # ── Voice (localType=34) ──
        if local_type == 34 and client:
            try:
                voice_info = msg.get("voice") or {}
                ct = voice_info.get("create_time") or msg.get("create_time", 0)
                lid = voice_info.get("local_id") or local_id
                sid = voice_info.get("server_id") or msg.get("server_id", 0)

                # Build candidates list
                sender = msg.get("sender", "")
                candidates = [talker]
                if sender and sender != talker:
                    candidates.append(sender)
                if my_wxid and my_wxid not in candidates:
                    candidates.append(my_wxid)

                result = client.get_voice_data(
                    session_id=talker,
                    create_time=int(ct) if ct else 0,
                    local_id=int(lid) if lid else 0,
                    svr_id=int(sid) if sid else 0,
                    candidates=candidates,
                )

                if result.get("success"):
                    from src.wechat.voice_decode import silk_to_wav
                    wav_data = silk_to_wav(result.get("hex", ""))
                    if wav_data:
                        fname = f"msg_{local_id}.wav"
                        with open(os.path.join(voice_dir, fname), "wb") as f:
                            f.write(wav_data)
                        msg["voice_path"] = f"voice/{fname}"
                        stats["voices"] += 1
                        stats["downloaded"] += 1
            except Exception as e:
                logger.debug(f"Chat voice download failed for msg {local_id}: {e}")
                stats["errors"] += 1

        # ── Videos (localType=43) — plain mp4, no encryption ──
        elif local_type == 43 and wxid and data_dir:
            for img in msg.get("images", []):
                fullmd5 = img.get("fullmd5", "")
                if not fullmd5:
                    continue
                try:
                    from datetime import datetime
                    create_time = msg.get("create_time", 0)
                    if create_time:
                        dt = datetime.fromtimestamp(create_time)
                        ym = dt.strftime("%Y-%m")
                        video_dir = _P(data_dir) / wxid / "msg" / "video" / ym
                        video_path = video_dir / f"{fullmd5}.mp4"
                        if video_path.exists():
                            data = video_path.read_bytes()
                            fname = f"msg_{local_id}.mp4"
                            with open(os.path.join(videos_dir, fname), "wb") as vf:
                                vf.write(data)
                            img["local_path"] = f"videos/{fname}"
                            img["is_video"] = True
                            stats["videos"] += 1
                            stats["downloaded"] += 1
                except Exception as e:
                    logger.debug(f"Chat video download failed for msg {local_id}: {e}")
                    stats["errors"] += 1

        # ── Images (localType=3) ──
        elif local_type == 3 and manager and wxid:
            for img in msg.get("images", []):
                fullmd5 = img.get("fullmd5", "")
                fullsize = img.get("fullsize")
                if not fullmd5:
                    continue
                try:
                    # Strategy 1: Chat image from MsgAttach directory
                    data = manager.decrypt_chat_image(
                        fullmd5=fullmd5,
                        talker=talker,
                        create_time=msg.get("create_time", 0),
                        wxid=wxid,
                        size="original",
                    )
                    # Strategy 2: Fallback to favorite/V2 cache
                    if not data:
                        data = manager.decrypt_fav_image(
                            0, wxid, size="original",
                            fullmd5=fullmd5,
                            fullsize=fullsize,
                        )
                    if data:
                        ext = _detect_image_ext(data)
                        fname = f"msg_{local_id}.{ext}"
                        with open(os.path.join(images_dir, fname), "wb") as f:
                            f.write(data)
                        img["local_path"] = f"images/{fname}"
                        stats["images"] += 1
                        stats["downloaded"] += 1
                except Exception as e:
                    logger.debug(f"Chat image download failed for msg {local_id}: {e}")
                    stats["errors"] += 1

        # Progress broadcast
        if broadcast and (i + 1) % 20 == 0:
            broadcast("chat_export_progress", {
                "status": "downloading",
                "progress": i + 1,
                "total": len(messages),
            })

    return stats


def _build_chat_html(messages: list[dict], contact_name: str, date_range: str = "") -> str:
    """Build a chat-style HTML export page. Returns HTML string."""
    import html as html_lib

    lines = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>聊天记录 - {html_lib.escape(contact_name)}</title>",
        "<style>",
        "*{box-sizing:border-box;margin:0;padding:0}",
        "body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#ECE5DD;color:#333}",
        ".hd{text-align:center;padding:16px;background:#F0EEE9;border-bottom:1px solid #D9D4CF}",
        ".hd h2{font-size:18px;color:#333}.hd .info{font-size:12px;color:#999;margin-top:4px}",
        ".chat{max-width:800px;margin:0 auto;padding:16px 12px;display:flex;flex-direction:column;gap:10px}",
        ".msg{display:flex;gap:8px;max-width:85%}",
        ".msg.self{align-self:flex-end;flex-direction:row-reverse}",
        ".msg .avatar{width:36px;height:36px;border-radius:50%;background:#8B7355;color:#fff;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;flex-shrink:0;overflow:hidden}",
        ".msg .avatar img{width:100%;height:100%;object-fit:cover}",
        ".msg .bubble{background:#fff;border-radius:12px;padding:10px 14px;box-shadow:0 1px 2px rgba(0,0,0,.06);position:relative}",
        ".msg.self .bubble{background:#95EC69}",
        ".msg .sender{font-size:11px;color:#999;margin-bottom:3px}",
        ".msg .time{font-size:10px;color:#B0B0B0;margin-top:4px;text-align:right}",
        ".msg .text{font-size:14px;line-height:1.6;white-space:pre-wrap;word-break:break-word}",
        ".msg .imgs img{max-width:240px;max-height:240px;border-radius:6px;cursor:zoom-in;margin-top:6px;display:block}",
        ".msg .imgs video{max-width:280px;max-height:200px;border-radius:6px;margin-top:6px;display:block}",
        ".msg .voice-player{display:flex;align-items:center;gap:8px;padding:6px 0}",
        ".msg .voice-player audio{height:32px}",
        ".msg .link-card{margin-top:6px;padding:8px 12px;border-radius:8px;background:rgba(0,0,0,.03);border:1px solid rgba(0,0,0,.06)}",
        ".msg .link-card a{color:#576B95;text-decoration:none;font-size:13px}",
        ".msg .sys{font-size:12px;color:#B0B0B0;text-align:center;padding:6px 0;background:transparent}",
        ".sys-msg{text-align:center;font-size:12px;color:#B0B0B0;padding:4px 0}",
        "img.lb{display:none;position:fixed;inset:0;max-width:92vw;max-height:92vh;margin:auto;background:rgba(0,0,0,.92);padding:20px;z-index:99;cursor:zoom-out}",
        "img.lb.on{display:block}",
        ".chat-records{margin-top:6px;border:1px solid rgba(0,0,0,.08);border-radius:8px;overflow:hidden;background:rgba(0,0,0,.02);max-width:90%}",
        ".chat-records .cr-header{display:flex;align-items:center;gap:4px;padding:6px 10px;background:rgba(0,0,0,.02);border-bottom:1px solid rgba(0,0,0,.06);font-size:11px;color:#576B95}",
        ".chat-records .cr-header .cr-title{font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".chat-records .cr-header .cr-count{color:#999;font-size:10px}",
        ".chat-records .cr-body{padding:6px 10px;max-height:240px;overflow-y:auto}",
        ".chat-records .cr-msg{display:flex;gap:6px;margin-bottom:6px;align-items:flex-start}",
        ".chat-records .cr-avatar{width:20px;height:20px;border-radius:4px;background:#8B7355;color:#fff;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;flex-shrink:0;overflow:hidden}",
        ".chat-records .cr-avatar img{width:100%;height:100%;object-fit:cover}",
        ".chat-records .cr-name{font-size:10px;color:#576B95;font-weight:600;white-space:nowrap}",
        ".chat-records .cr-text{font-size:12px;line-height:1.4;margin-top:1px;color:#333}",
        ".quote-box{margin-bottom:6px;padding:6px 10px;border-left:2px solid rgba(0,0,0,.15);background:rgba(0,0,0,.02);border-radius:0 6px 6px 0;font-size:12px;color:#999}",
        ".quote-box .quote-sender{font-weight:600;color:#576B95}",
        "@media(prefers-color-scheme:dark){body{background:#111;color:#f5f5f5}.hd{background:#1a1a1a;border-color:#333}.hd h2{color:#f5f5f5}.msg .bubble{background:#1e1e1e;color:#f5f5f5}.msg.self .bubble{background:#2b5a1e}.msg .sender{color:#888}.msg .time{color:#666}}",
        "</style></head><body>",
        f"<div class='hd'><h2>聊天记录 · {html_lib.escape(contact_name)}</h2>",
        f"<div class='info'>共 {len(messages)} 条消息" + (f" · {html_lib.escape(date_range)}" if date_range else "") + "</div></div>",
        "<div class='chat'>",
    ]

    last_date = ""
    for msg in messages:
        local_type = msg.get("localType", 1)
        is_self = msg.get("is_self", False)
        sender_name = msg.get("sender_name", "")
        content = msg.get("content", "")
        ct = msg.get("create_time", 0)

        # Time separator
        if ct:
            from datetime import datetime as _dt
            try:
                dt = _dt.fromtimestamp(ct)
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M")
            except Exception:
                date_str = ""
                time_str = ""
            if date_str and date_str != last_date:
                lines.append(f"<div class='sys-msg'>{html_lib.escape(date_str)}</div>")
                last_date = date_str
        else:
            time_str = ""

        # System message
        if local_type == 10000:
            lines.append(f"<div class='sys-msg'>{html_lib.escape(content)}</div>")
            continue

        # Chat bubble
        cls = "msg self" if is_self else "msg"
        initial = (sender_name or "?")[0].upper()
        avatar_url = msg.get("sender_avatar", "")
        lines.append(f"<div class='{cls}'>")
        if avatar_url:
            lines.append(f"<div class='avatar'><img src='{html_lib.escape(avatar_url)}' onerror=\"this.style.display='none';this.parentElement.textContent='{html_lib.escape(initial)}'\"></div>")
        else:
            lines.append(f"<div class='avatar'>{html_lib.escape(initial)}</div>")
        lines.append(f"<div class='bubble'>")

        if not is_self and sender_name:
            lines.append(f"<div class='sender'>{html_lib.escape(sender_name)}</div>")

        # Content by type
        if local_type == 1:
            # Text
            escaped = html_lib.escape(content).replace("\n", "<br>")
            lines.append(f"<div class='text'>{escaped}</div>")

        elif local_type in (3, 43):
            # Image / Video
            for img in msg.get("images", []):
                local = img.get("local_path", "")
                is_video = img.get("is_video", False) or (local and local.lower().endswith(".mp4"))
                if local:
                    if is_video:
                        lines.append(f"<div class='imgs'><video src='{local}' controls preload='metadata'></video></div>")
                    else:
                        lines.append(f"<div class='imgs'><img src='{local}' loading='lazy' onclick='openLb(this.src)'></div>")

        elif local_type == 34:
            # Voice
            voice_path = msg.get("voice_path", "")
            if voice_path:
                lines.append(f"<div class='voice-player'>🎤 <audio controls src='{voice_path}' preload='metadata'></audio></div>")

        elif local_type == 49:
            # Check for chat records (appmsg type=19)
            chat_records_data = msg.get("chat_records")
            if chat_records_data:
                cr_title = html_lib.escape(chat_records_data.get("title", "聊天记录"))
                cr_items = chat_records_data.get("items", [])
                lines.append("<div class='chat-records'>")
                lines.append(f"<div class='cr-header'><span class='cr-title'>{cr_title}</span><span class='cr-count'>{len(cr_items)}条</span></div>")
                lines.append("<div class='cr-body'>")
                for cr_item in cr_items:
                    cr_type = str(cr_item.get("type", "0"))
                    cr_name = cr_item.get("src_name", "未知")
                    cr_initial = (cr_name or "?")[0].upper()
                    cr_head_url = cr_item.get("head_url", "")
                    cr_time = html_lib.escape(cr_item.get("time", ""))
                    lines.append("<div class='cr-msg'>")
                    # Avatar with img + fallback
                    if cr_head_url:
                        lines.append(f"<div class='cr-avatar'><img src='{html_lib.escape(cr_head_url)}' onerror=\"this.style.display='none';this.parentElement.textContent='{html_lib.escape(cr_initial)}'\"></div>")
                    else:
                        lines.append(f"<div class='cr-avatar'>{html_lib.escape(cr_initial)}</div>")
                    lines.append(f"<div><span class='cr-name'>{html_lib.escape(cr_name)}</span>")
                    if cr_time:
                        lines.append(f"<span style='font-size:9px;color:#bbb;margin-left:2px'>{cr_time}</span>")
                    # Content by type
                    if cr_type == "2":
                        cr_img_path = cr_item.get("local_path", "")
                        if cr_img_path:
                            lines.append(f"<div class='cr-text'><img src='{html_lib.escape(cr_img_path)}' style='max-width:120px;max-height:120px;border-radius:4px;margin-top:2px'></div>")
                        else:
                            lines.append("<span class='cr-text'>[图片]</span>")
                    elif cr_type in ("3", "34"):
                        lines.append("<span class='cr-text'>[语音]</span>")
                    elif cr_type == "4":
                        lines.append("<span class='cr-text'>[视频]</span>")
                    elif cr_type == "5":
                        link_url = cr_item.get("link_url", "")
                        link_title = cr_item.get("link_title", cr_item.get("desc", ""))
                        if link_url:
                            lines.append(f"<span class='cr-text'><a href='{html_lib.escape(link_url)}' target='_blank' style='color:#576B95'>{html_lib.escape(link_title or '链接')}</a></span>")
                        else:
                            lines.append(f"<span class='cr-text'>{html_lib.escape(cr_item.get('desc', '[链接]'))}</span>")
                    elif cr_type == "8":
                        file_name = cr_item.get("file_name", cr_item.get("desc", ""))
                        lines.append(f"<span class='cr-text'>[文件] {html_lib.escape(file_name or '')}</span>")
                    elif cr_item.get("desc"):
                        lines.append(f"<span class='cr-text'>{html_lib.escape(cr_item['desc'])}</span>")
                    lines.append("</div></div>")
                lines.append("</div></div>")

            # Check for quote/reply (appmsg type=57)
            elif msg.get("quote"):
                quote = msg["quote"]
                quote_sender = html_lib.escape(quote.get("sender", ""))
                quote_content = html_lib.escape(quote.get("content", ""))
                lines.append("<div class='quote-box'>")
                if quote_sender:
                    lines.append(f"<span class='quote-sender'>{quote_sender}: </span>")
                lines.append(f"{quote_content}</div>")
                # Reply text
                reply_text = msg.get("reply_text", "")
                if reply_text:
                    escaped = html_lib.escape(reply_text).replace("\n", "<br>")
                    lines.append(f"<div class='text'>{escaped}</div>")
                elif content and not content.strip().startswith("<"):
                    escaped = html_lib.escape(content).replace("\n", "<br>")
                    lines.append(f"<div class='text'>{escaped}</div>")

            # Regular link/file (other appmsg types)
            else:
                link = msg.get("link") or {}
                link_url = link.get("url", "")
                link_title = link.get("title", "")
                link_filename = link.get("filename", "")
                if link_url:
                    display = html_lib.escape(link_title or link_filename or link_url)
                    lines.append(f"<div class='link-card'><a href='{html_lib.escape(link_url)}' target='_blank'>{display}</a></div>")
                # Also show text content if any (appmsg often has description)
                if content and not content.strip().startswith("<"):
                    escaped = html_lib.escape(content).replace("\n", "<br>")
                    lines.append(f"<div class='text'>{escaped}</div>")

        elif local_type == 47:
            lines.append("<div class='text'>[表情]</div>")

        else:
            # Fallback: show text if not XML
            if content and not content.strip().startswith("<"):
                escaped = html_lib.escape(content).replace("\n", "<br>")
                lines.append(f"<div class='text'>{escaped}</div>")

        if time_str:
            lines.append(f"<div class='time'>{time_str}</div>")

        lines.append("</div></div>")  # close bubble, msg

    lines.append("</div>")  # close chat
    lines.append("<img class='lb' id='lb' onclick='closeLb()' src=''>")
    lines.append("<script>"
                 "function openLb(s){var l=document.getElementById('lb');l.src=s;l.classList.add('on')}"
                 "function closeLb(){document.getElementById('lb').classList.remove('on')}"
                 "document.addEventListener('keydown',function(e){if(e.key==='Escape')closeLb()})"
                 "</script>")
    lines.append("</body></html>")

    return "\n".join(lines)


def handle_chat_export(params, config: AssistantConfig):
    """POST /api/chat/export — Export chat history as HTML+JSON+media."""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        body = params.get("_body", {}) or {}
        talker = body.get("talker", "") or params.get("talker", [""])[0]
        start_time = int(body.get("start_time", 0) or 0)
        end_time = int(body.get("end_time", 0) or 0)
        dry_run = body.get("dry_run", False) or params.get("dry_run", [""])[0] == "true"
        media_types = body.get("media_types", None) or None

        if not talker:
            return {"ok": False, "error": "Missing talker parameter"}

        # Fetch all messages (paginated) with early termination for time-filtered exports
        MAX_EXPORT_MESSAGES = 5000  # Hard cap to prevent OOM on huge groups
        all_messages = []
        batch_size = 200
        offset = 0
        max_batches = MAX_EXPORT_MESSAGES // batch_size + 1  # Safety: limit page count
        for _ in range(max_batches):
            batch = client.get_messages(talker, limit=batch_size, offset=offset)
            if not batch:
                break
            if not batch:
                break
            # Early termination: if ALL messages in this batch are older than
            # start_time, no need to fetch more pages (safe regardless of sort
            # order — only stops when entire batch is out of range).
            if start_time:
                all_before = True
                for m in batch:
                    ct = int(m.get("create_time", m.get("createTime", 0)) or 0)
                    if ct >= start_time:
                        all_before = False
                        break
                if all_before:
                    logger.info("Chat export: early termination at offset %d (all messages before start_time=%d)", offset, start_time)
                    break
            all_messages.extend(batch)
            if len(all_messages) >= MAX_EXPORT_MESSAGES:
                all_messages = all_messages[:MAX_EXPORT_MESSAGES]
                logger.warning("Chat export: truncated to %d messages (MAX_EXPORT_MESSAGES)", MAX_EXPORT_MESSAGES)
                break
            if len(batch) < batch_size:
                break
            offset += batch_size

        truncated = len(all_messages) >= MAX_EXPORT_MESSAGES

        # Time filtering
        if start_time or end_time:
            filtered = []
            for m in all_messages:
                ct = int(m.get("create_time", m.get("createTime", 0)) or 0)
                if start_time and ct < start_time:
                    continue
                if end_time and ct > end_time:
                    continue
                filtered.append(m)
            all_messages = filtered

        # Media type filtering (images: localType=3, videos: localType=43, voices: localType=34)
        if media_types and isinstance(media_types, dict):
            # Default to all types if not specified
            include_images = media_types.get("images", True)
            include_voices = media_types.get("voices", True)
            include_videos = media_types.get("videos", True)
            include_links = media_types.get("links", True)
            # localType mapping: images→3, voices→34, videos→43
            allowed_local_types = set()
            if include_images:
                allowed_local_types.add(3)
            if include_voices:
                allowed_local_types.add(34)
            if include_videos:
                allowed_local_types.add(43)
            if include_links:
                allowed_local_types.add(49)  # appmsg/link card
            # Always include text (type 1) — but only if at least one media type is deselected
            any_unchecked = not (include_images and include_voices and include_videos and include_links)
            if any_unchecked:
                filtered = []
                for m in all_messages:
                    lt = int(m.get("local_type", m.get("localType", 1)) or 1)
                    if lt == 1:
                        filtered.append(m)  # always include text
                    elif lt in allowed_local_types:
                        filtered.append(m)
                    # else: exclude this media type
                all_messages = filtered

        # Count media (images: localType=3, videos: localType=43, voices: localType=34)
        image_count = sum(1 for m in all_messages if int(m.get("local_type", m.get("localType", 1))) == 3)
        video_count = sum(1 for m in all_messages if int(m.get("local_type", m.get("localType", 1))) == 43)
        voice_count = sum(1 for m in all_messages if int(m.get("local_type", m.get("localType", 1))) == 34)

        # Dry run: return estimate
        if dry_run:
            result = {
                "ok": True,
                "dry_run": True,
                "item_count": len(all_messages),
                "image_count": image_count,
                "video_count": video_count,
                "voice_count": voice_count,
                "truncated": truncated,
                **_estimate_export_size(
                    len(all_messages), image_count, voice_count, video_count,
                    avg_image_kb=300, avg_voice_kb=10, warning_mb=50,
                ),
            }
            return result

        # Normalize and enrich messages
        my_wxid = client._config.get("myWxid", "")

        # Batch resolve display names and avatar URLs
        sender_ids = set()
        for m in all_messages:
            sid = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
            if sid:
                sender_ids.add(sid)
        sender_ids.add(talker)
        name_map = client.get_display_names(list(sender_ids)) if sender_ids else {}
        avatar_map = {}
        try:
            avatar_map = client.get_avatar_urls(list(sender_ids)) if sender_ids else {}
        except Exception:
            pass

        # Get contact display name
        contact_name = name_map.get(talker, talker)

        # Normalize messages
        normalized = []
        for m in all_messages:
            local_type = int(m.get("local_type", m.get("localType", m.get("msg_type", 1))) or 1)
            sender = m.get("sender_username", m.get("senderUsername", m.get("sender", "")))
            create_time = int(m.get("create_time", m.get("createTime", 0)) or 0)
            content = m.get("message_content", "") or m.get("content", "") or ""
            content = _decompress_content(content)
            # WeChat 4.x sometimes returns HTML-encoded XML (&lt; instead of <)
            if '&lt;' in content and '<' not in content[:100]:
                content = __import__('html').unescape(content)
            local_id = m.get("local_id", m.get("localId", 0))
            server_id = m.get("server_id", m.get("svrId", m.get("serverId", 0)))
            media = _parse_msg_media(m)

            # Normalize type for WeChat 4.x new type codes
            is_appmsg_code = (local_type & 0xFF) == 0x31
            if is_appmsg_code and content and content.lstrip().startswith('<'):
                if '<appmsg' in content.lower():
                    normalized_type = 49
                elif '<img' in content[:200].lower() and '<videomsg' not in content[:200].lower():
                    normalized_type = 3
                elif '<videomsg' in content[:200].lower():
                    normalized_type = 43
                else:
                    normalized_type = 49
            else:
                normalized_type = local_type

            # For text messages, content is readable; for media, use label
            display_content = content
            if normalized_type in (3, 43) and content:
                display_content = "[图片]" if normalized_type == 3 else "[视频]"
            elif normalized_type == 34:
                display_content = "[语音]"

            # Strip sender_id: prefix from group chat text/system/app messages
            # In group chats, decompressed content often starts with 'sender_id:\n'
            # This applies to ALL types (text, app messages, system messages, etc.)
            if talker.endswith('@chatroom') and display_content:
                display_content = _strip_wxid_prefix(display_content)
            # Extract readable text from XML content (system msgs, app msgs, etc.)
            if display_content and display_content.lstrip().startswith('<'):
                display_content = _extract_system_msg_text(display_content)

            # For quote/reply messages, display the actual reply text
            if media.get("reply_text"):
                display_content = media["reply_text"]

            normalized.append({
                "local_id": local_id,
                "server_id": server_id,
                "localType": normalized_type,
                "sender": sender,
                "sender_name": name_map.get(sender, sender),
                "sender_avatar": avatar_map.get(sender, ""),
                "is_self": sender == my_wxid,
                "content": display_content,
                "create_time": create_time,
                "images": media["images"],
                "voice": media["voice"],
                "link": media["link"],
                "quote": media.get("quote"),
                "reply_text": media.get("reply_text"),
                "chat_records": media.get("chat_records"),
            })

        # Sort by time ascending for export
        normalized.sort(key=lambda m: m.get("create_time", 0))

        # Enrich chat record sub-messages with names & avatars (same as handle_chat_messages)
        cr_wxids = set()
        for msg in normalized:
            cr_data = msg.get("chat_records")
            if not cr_data:
                continue
            for item in cr_data.get("items", []):
                wxid = item.get("_fromusr", "")
                if wxid and (not item.get("src_name") or not item.get("head_url")):
                    cr_wxids.add(wxid)
        if cr_wxids:
            try:
                cr_name_map = client.get_display_names(list(cr_wxids))
            except Exception:
                cr_name_map = {}
            try:
                cr_avatar_map = client.get_avatar_urls(list(cr_wxids))
            except Exception:
                cr_avatar_map = {}
            for msg in normalized:
                cr_data = msg.get("chat_records")
                if not cr_data:
                    continue
                for item in cr_data.get("items", []):
                    wxid = item.get("_fromusr", "")
                    if wxid:
                        if not item.get("src_name"):
                            item["src_name"] = cr_name_map.get(wxid, "")
                        if not item.get("head_url"):
                            item["head_url"] = cr_avatar_map.get(wxid, "")
                        item.pop("_fromusr", None)

        # Create export directory
        import time as _time
        ts = _time.strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', contact_name)
        export_dir = os.path.abspath(f"data/chat_export/{safe_name}_{ts}")
        os.makedirs(export_dir, exist_ok=True)

        broadcast_event("chat_export_progress", {"status": "starting", "talker": talker})

        # Save JSON
        json_path = os.path.join(export_dir, "messages.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2, default=str)

        # Download media
        broadcast_event("chat_export_progress", {"status": "downloading", "total": len(normalized)})
        media_stats = _download_chat_media(normalized, export_dir, talker, my_wxid, broadcast=broadcast_event)

        # Build HTML
        broadcast_event("chat_export_progress", {"status": "exporting", "format": "html"})
        date_range = ""
        if start_time or end_time:
            from datetime import datetime as _dt
            parts = []
            if start_time:
                parts.append(_dt.fromtimestamp(start_time).strftime("%Y-%m-%d"))
            if end_time:
                parts.append(_dt.fromtimestamp(end_time).strftime("%Y-%m-%d"))
            date_range = " ~ ".join(parts)

        html_content = _build_chat_html(normalized, contact_name, date_range)
        html_path = os.path.join(export_dir, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        broadcast_event("chat_export_progress", {"status": "completed", "path": export_dir})

        return {
            "ok": True,
            "path": export_dir,
            "message_count": len(normalized),
            "image_count": media_stats["images"],
            "voice_count": media_stats["voices"],
            "truncated": truncated,
        }
    except Exception as e:
        logger.error(f"Failed to export chat: {e}")
        broadcast_event("chat_export_progress", {"status": "error", "error": str(e)})
        return {"ok": False, "error": str(e)}


# ── 群成员 / 共同群聊 API ──────────────────────────────────────────────

def handle_chat_group_members(params, config: AssistantConfig):
    """GET /api/chat/members?chatroom=xxx@chatroom — Get member list for a group.

    Returns all members of a chatroom (not just message senders).
    Supports optional keyword filter for searching by display name.
    """
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        chatroom = params.get("chatroom", [""])[0]
        if not chatroom:
            return {"ok": False, "error": "Missing chatroom parameter"}

        keyword = (params.get("keyword", [""])[0] or "").strip().lower()

        # Get all members from DLL
        members = client.get_group_members(chatroom)

        if not members:
            return {"ok": True, "data": [], "total": 0}

        # Batch resolve display names for member wxids
        member_wxids = []
        for m in members:
            wxid = m.get("wxid") or m.get("userName") or m.get("username") or ""
            if wxid:
                member_wxids.append(wxid)

        name_map = {}
        if member_wxids:
            try:
                name_map = client.get_display_names(member_wxids)
            except Exception:
                pass

        # Check which members are friends (local_type=1 in contact table)
        # WeChat 4.x contact table: local_type=1 = friends, 0 = special/deleted,
        # 2 = chatroom, 3 = non-friend contacts (strangers from groups etc.)
        # Also exclude known system accounts
        _FRIEND_EXCLUDE = {'weixin', 'fmessage', 'medianote', 'floatbottle', 'qmessage', 'qqmail'}
        friend_set = set()
        if member_wxids:
            try:
                # Validate all wxids to prevent SQL injection
                for wxid in member_wxids:
                    if not re.fullmatch(r'[a-zA-Z0-9_@]+', wxid):
                        logger.warning(f"Skipping invalid wxid in group_members: {wxid}")
                        continue
                quoted = ",".join(f"'{wxid}'" for wxid in member_wxids
                                  if re.fullmatch(r'[a-zA-Z0-9_@]+', wxid))
                sql = f"SELECT username FROM contact WHERE username IN ({quoted}) AND local_type = 1"
                rows = client.exec_query("contact", "", sql)
                for row in rows:
                    uname = row.get("username", "")
                    if uname not in _FRIEND_EXCLUDE:
                        friend_set.add(uname)
            except Exception as e:
                logger.warning(f"Failed to check friend status: {e}")

        # Enrich members with display names and apply keyword filter
        enriched = []
        for m in members:
            wxid = m.get("wxid") or m.get("userName") or m.get("username") or ""
            if not wxid:
                continue

            # Prefer DLL-resolved name, then group-specific nickname, then raw field
            display_name = (
                name_map.get(wxid, "")
                or m.get("nickname") or m.get("nickName")
                or m.get("groupNickName")
                or wxid
            )

            # Apply keyword filter
            if keyword and keyword not in display_name.lower() and keyword not in wxid.lower():
                continue

            enriched.append({
                "wxid": wxid,
                "display_name": display_name,
                "group_nickname": m.get("groupNickName") or m.get("nickname") or "",
                "is_friend": wxid in friend_set,
            })

        # Sort by display name
        enriched.sort(key=lambda x: x.get("display_name", ""))

        return {"ok": True, "data": enriched, "total": len(enriched)}
    except Exception as e:
        logger.error(f"Failed to get group members: {e}")
        return {"ok": False, "error": str(e)}


# ── 共同群聊倒排索引缓存 ──────────────────────────────────────
_member_to_groups_cache: dict[str, tuple[dict[str, set[str]], float]] = {}
_COMMON_GROUPS_CACHE_TTL = 300  # 5 minutes


def _get_member_to_groups_index(client) -> dict[str, set[str]]:
    """Build wxid -> {chatroom_ids} inverted index, cached for 5 min.

    First call iterates all chatrooms and builds the full index.
    Subsequent calls within TTL return the cached index instantly.
    """
    now = time.time()
    cached = _member_to_groups_cache.get("index")
    if cached and (now - cached[1]) < _COMMON_GROUPS_CACHE_TTL:
        return cached[0]

    index: dict[str, set[str]] = {}
    sessions = client.get_sessions(limit=500)
    chatroom_ids = [
        s.get("username", "")
        for s in sessions
        if s.get("username", "").endswith("@chatroom")
    ]

    for chatroom_id in chatroom_ids:
        try:
            members = client.get_group_members(chatroom_id)
            for m in members:
                wxid = m.get("wxid") or m.get("userName") or m.get("username") or ""
                if wxid:
                    index.setdefault(wxid, set()).add(chatroom_id)
        except Exception:
            continue

    _member_to_groups_cache["index"] = (index, now)
    logger.info(f"Built member-to-groups index: {len(index)} wxids across {len(chatroom_ids)} chatrooms")
    return index


# ── Batch group member counts (with cache) ──────────────────────────

_group_member_count_cache: dict[str, tuple[int, float]] = {}
_GROUP_MEMBER_COUNT_TTL = 300  # 5 minutes
_MEMBER_COUNTS_FILE = Path("data/member_counts.json")


def _load_member_counts_cache() -> dict[str, int]:
    """Load persisted member counts from JSON file."""
    if not _MEMBER_COUNTS_FILE.exists():
        return {}
    try:
        return json.loads(_MEMBER_COUNTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_member_counts_cache(counts: dict[str, int]) -> None:
    """Persist member counts to JSON file."""
    try:
        _MEMBER_COUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MEMBER_COUNTS_FILE.write_text(
            json.dumps(counts, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except OSError:
        pass  # Non-critical, continue without persisting


def handle_group_member_counts(params, config: AssistantConfig):
    """GET /api/groups/member-counts — Batch get real member counts for all chatrooms.

    Returns {chatroom_id: member_count} using WCDB DLL with 5-min cache.
    First tries to load from persisted cache (data/member_counts.json),
    then falls back to WCDB queries and updates the cache.
    """
    client = get_wcdb_client()
    if not client:
        # Fallback: return persisted cache if available
        return {"ok": True, "counts": _load_member_counts_cache(), "from_cache": True}

    now = time.time()

    # Load persisted cache on first call
    if not _group_member_count_cache:
        persisted = _load_member_counts_cache()
        for chat_id, count in persisted.items():
            _group_member_count_cache[chat_id] = (count, 0)  # timestamp=0 means persistent

    # Check if overall cache is still valid
    cache_ts = _group_member_count_cache.get("_ts", (0, 0))[0]
    if cache_ts and (now - cache_ts) < _GROUP_MEMBER_COUNT_TTL:
        counts = {k: v[0] for k, v in _group_member_count_cache.items() if k != "_ts"}
        return {"ok": True, "counts": counts}

    try:
        sessions = client.get_sessions(limit=500)
        chatroom_ids = [
            s.get("username", "")
            for s in sessions
            if s.get("username", "").endswith("@chatroom")
        ]

        counts = {}
        for chatroom_id in chatroom_ids:
            # Check per-group cache
            cached = _group_member_count_cache.get(chatroom_id)
            if cached and (now - cached[1]) < _GROUP_MEMBER_COUNT_TTL:
                counts[chatroom_id] = cached[0]
                continue

            try:
                members = client.get_group_members(chatroom_id)
                count = len(members) if members else 0
                counts[chatroom_id] = count
                _group_member_count_cache[chatroom_id] = (count, now)
            except Exception:
                counts[chatroom_id] = 0

        _group_member_count_cache["_ts"] = (now, now)
        # Persist to disk so next restart doesn't need DLL queries
        _save_member_counts_cache(counts)
        return {"ok": True, "counts": counts}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_chat_common_groups(params, config: AssistantConfig):
    """GET /api/chat/common-groups?wxid=xxx — Get groups shared with a friend.

    Uses WCDB chatroom_member table to find common groups via a single SQL query,
    avoiding the O(N) DLL call per group that the old inverted-index approach required.
    """
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        friend_wxid = params.get("wxid", [""])[0]
        if not friend_wxid:
            return {"ok": False, "error": "Missing wxid parameter"}
        # Validate wxid format to prevent SQL injection (WCDB exec_query
        # doesn't support parameterized queries, so we whitelist chars)
        if not re.fullmatch(r'[a-zA-Z0-9_@]+', friend_wxid):
            return {"ok": False, "error": "Invalid wxid format"}

        # Strategy: use chatroom_member table (numeric IDs) joined with
        # contact/chat_room to get usernames. Single SQL query, no DLL calls.
        #
        # chatroom_member.member_id → contact.id → contact.username (wxid)
        # chatroom_member.room_id   → chat_room.id → chat_room.username (@chatroom)
        try:
            sql = (
                "SELECT cr.username AS chatroom_id, c.username AS member_wxid "
                "FROM chatroom_member cm "
                "JOIN chat_room cr ON cr.id = cm.room_id "
                "JOIN contact c ON c.id = cm.member_id "
                f"WHERE c.username = '{friend_wxid}'"
            )
            rows = client.exec_query("contact", "", sql)
            matched_chatrooms = [r.get("chatroom_id", "") for r in rows if r.get("chatroom_id")]
        except Exception as e:
            logger.warning(f"chatroom_member SQL failed, fallback to DLL: {e}")
            # Fallback to old DLL-based approach if SQL fails
            matched_chatrooms = _get_common_groups_fallback(client, friend_wxid)

        if not matched_chatrooms:
            return {"ok": True, "data": [], "total": 0}

        # Resolve display names for matched chatrooms
        try:
            chatroom_names = client.get_display_names(matched_chatrooms)
        except Exception:
            chatroom_names = {}

        # Get member counts — also via SQL instead of per-group DLL calls
        member_count_map = {}
        try:
            quoted = ",".join(f"'{cid}'" for cid in matched_chatrooms)
            sql = (
                "SELECT cr.username AS chatroom_id, COUNT(cm.member_id) AS cnt "
                "FROM chatroom_member cm "
                "JOIN chat_room cr ON cr.id = cm.room_id "
                f"WHERE cr.username IN ({quoted}) "
                "GROUP BY cr.username"
            )
            count_rows = client.exec_query("contact", "", sql)
            for r in count_rows:
                member_count_map[r.get("chatroom_id", "")] = int(r.get("cnt", 0))
        except Exception:
            # Fallback: use DLL if SQL fails
            for cid in matched_chatrooms:
                try:
                    members = client.get_group_members(cid)
                    member_count_map[cid] = len(members)
                except Exception:
                    member_count_map[cid] = 0

        common_groups = []
        for chatroom_id in matched_chatrooms:
            group_display = chatroom_names.get(chatroom_id, chatroom_id)
            common_groups.append({
                "chatroom_id": chatroom_id,
                "group_name": group_display or chatroom_id,
                "member_count": member_count_map.get(chatroom_id, 0),
            })

        common_groups.sort(key=lambda x: x.get("group_name", ""))
        return {"ok": True, "data": common_groups, "total": len(common_groups)}
    except Exception as e:
        logger.error(f"Failed to get common groups: {e}")
        return {"ok": False, "error": str(e)}


def _get_common_groups_fallback(client, friend_wxid):
    """Fallback: build inverted index via DLL calls (old approach).

    Only used when the SQL-based chatroom_member query fails.
    """
    index = _get_member_to_groups_index(client)
    return list(index.get(friend_wxid, set()))


# ── 朋友圈 API ─────────────────────────────────────────────────────────

def handle_sns_timeline(params, config: AssistantConfig):
    """GET /api/sns/timeline — Get Moments timeline"""
    reader = _get_wcdb_sns_reader()
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        limit = int(params.get("limit", [20])[0]) if params.get("limit") else 20
        offset = int(params.get("offset", [0])[0]) if params.get("offset") else 0
        username = params.get("username", [None])[0]
        keyword = params.get("keyword", [None])[0]
        start_time = int(params.get("start_time", [0])[0]) if params.get("start_time") else 0
        end_time = int(params.get("end_time", [0])[0]) if params.get("end_time") else 0

        # Build usernames filter if provided
        usernames = [username] if username else None

        posts = reader.get_timeline(
            limit=limit, offset=offset, usernames=usernames,
            keyword=keyword or "", start_time=start_time, end_time=end_time
        )

        # Map to frontend-expected fields
        mapped_posts = []
        # Collect all usernames for batch avatar lookup
        all_usernames = list(set(p.get("username", "") for p in posts if p.get("username")))
        avatar_map = {}
        if all_usernames:
            try:
                client = get_wcdb_client()
                if client:
                    avatar_map = client.get_avatar_urls(all_usernames)
            except Exception:
                pass

        for post in posts:
            # Process media to add type and thumb_url for frontend
            media_list = post.get("media", [])
            processed_media = []

            # Extract video encryption key from rawXml (WeFlow pattern)
            video_key = ""
            raw_xml = post.get("rawXml", "")
            if raw_xml and "<enc" in raw_xml:
                import re as _re_enc
                enc_match = _re_enc.search(r'<enc\s+key="(\d+)"', raw_xml)
                if enc_match:
                    video_key = enc_match.group(1)

            for m in media_list:
                url = m.get("url", "")
                thumb_url = m.get("thumb", "")
                # Determine media type from URL (same logic as WeFlow)
                mtype = m.get("type", "")
                if not mtype:
                    if ("snsvideodownload" in url.lower() or
                        (".mp4" in url.lower()) or
                        ("video" in url.lower() and "vweixinthumb" not in url.lower())):
                        mtype = "video"
                    elif url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                        mtype = "image"
                    else:
                        mtype = "image"  # default to image for moments
                # For videos: use enc key from XML if media key is 0
                media_key = m.get("key", "")
                if mtype == "video" and (not media_key or str(media_key) == "0") and video_key:
                    media_key = video_key
                processed_media.append({
                    "type": mtype,
                    "thumb_url": thumb_url or url,
                    "url": url,
                    "key": str(media_key),
                    "token": m.get("token", ""),
                })

            mapped_posts.append({
                "id": post.get("id", ""),
                "username": post.get("username", ""),
                "nickname": post.get("nickname", ""),
                "user_head_url": avatar_map.get(post.get("username", ""), ""),
                "create_time": post.get("createTime", 0),
                "content": post.get("contentDesc", ""),
                "like_count": len(post.get("likes", [])),
                "comment_count": len(post.get("comments", [])),
                "likes": post.get("likes", []),
                "comments": post.get("comments", []),
                "media_list": processed_media,  # Frontend expects media_list
                "location": post.get("location", ""),
                "rawXml": post.get("rawXml", ""),
            })

        return {
            "ok": True,
            "data": mapped_posts,
            "total": len(mapped_posts),
        }
    except Exception as e:
        logger.error(f"Failed to get timeline: {e}")
        return {"ok": False, "error": str(e)}


def handle_sns_search(params, config: AssistantConfig):
    """GET /api/sns/search — Search Moments"""
    reader = _get_wcdb_sns_reader()
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        keyword = params.get("q", [""])[0] or params.get("keyword", [""])[0]
        if not keyword:
            return {"ok": False, "error": "Missing keyword"}

        # Use timeline with keyword filter
        posts = reader.get_timeline(limit=50, offset=0, keyword=keyword)

        # Map fields (search results are simpler, just basic info)
        mapped_posts = []
        for post in posts:
            mapped_posts.append({
                "id": post.get("id", ""),
                "username": post.get("username", ""),
                "nickname": post.get("nickname", ""),
                "create_time": post.get("createTime", 0),
                "content": post.get("contentDesc", ""),
                "like_count": len(post.get("likes", [])),
                "comment_count": len(post.get("comments", [])),
                "media_list": [],
                "location": "",
            })
        return {
            "ok": True,
            "data": mapped_posts,
            "total": len(mapped_posts),
        }
    except Exception as e:
        logger.error(f"Failed to search: {e}")
        return {"ok": False, "error": str(e)}


def handle_sns_protect_install(params, config: AssistantConfig):
    """POST /api/sns/protect/install — Install protection"""
    if not _is_restricted_enabled():
        return {"ok": False, "error": "Restricted features are disabled"}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        from src.wechat.sns_client import SnsClient
        sns = SnsClient(client)
        result = sns.install_protection()
        # Normalize: ensure 'installed' key exists for frontend
        if result.get("success"):
            result["installed"] = True
        return result
    except Exception as e:
        logger.error(f"Failed to install protection: {e}")
        return {"ok": False, "error": str(e)}


def handle_sns_protect_uninstall(params, config: AssistantConfig):
    """POST /api/sns/protect/uninstall — Uninstall protection"""
    if not _is_restricted_enabled():
        return {"ok": False, "error": "Restricted features are disabled"}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        from src.wechat.sns_client import SnsClient
        sns = SnsClient(client)
        result = sns.uninstall_protection()
        # Normalize: ensure 'installed' key exists for frontend
        result["installed"] = False
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_sns_protect_status(params, config: AssistantConfig):
    """GET /api/sns/protect/status — Check protection status"""
    if not _is_restricted_enabled():
        return {"ok": True, "installed": False, "disabled": True}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        from src.wechat.sns_client import SnsClient
        sns = SnsClient(client)
        return sns.check_protection()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _anti_revoke_get_session_ids(client) -> list[str]:
    """Get all real session IDs for anti-revoke trigger operations."""
    sessions = client.get_sessions(limit=500)
    return [s.get("username", "") for s in sessions
            if s.get("username") and not s.get("username", "").startswith("@placeholder")]


def _is_restricted_enabled() -> bool:
    """Check if restricted features are enabled (from env var, not config object)."""
    return os.getenv("ENABLE_RESTRICTED_FEATURES", "false").strip().lower() == "true"


def cleanup_restricted_triggers():
    """If ENABLE_RESTRICTED_FEATURES=false, uninstall any lingering sensitive triggers.

    Called once at bot startup to ensure no anti-revoke or SNS block-delete
    triggers remain active when the config switch is off.  Triggers persist
    in the WCDB file across restarts, so merely disabling the API is not
    enough — the triggers would continue intercepting revocations/deletes.
    """
    if _is_restricted_enabled():
        logger.info("Restricted features enabled — skipping trigger cleanup")
        return

    client = get_wcdb_client()
    if not client:
        return

    # ── Anti-revoke triggers (per-session) ──
    try:
        session_ids = _anti_revoke_get_session_ids(client)
        cleaned = 0
        for sid in session_ids:
            try:
                result = client.check_message_anti_revoke_trigger(sid)
                if result.get("installed"):
                    client.uninstall_message_anti_revoke_trigger(sid)
                    cleaned += 1
            except Exception:
                pass
        if cleaned > 0:
            logger.info("Cleanup: uninstalled anti-revoke triggers from %d sessions (restricted features disabled)", cleaned)
        else:
            logger.debug("Cleanup: no anti-revoke triggers found")
    except Exception as e:
        logger.warning("Cleanup: failed to check/uninstall anti-revoke triggers: %s", e)

    # ── SNS block-delete trigger (global) ──
    try:
        from src.wechat.sns_client import SnsClient
        sns = SnsClient(client)
        result = sns.check_protection()
        if result.get("installed"):
            sns.uninstall_protection()
            logger.info("Cleanup: uninstalled SNS block-delete trigger (restricted features disabled)")
        else:
            logger.debug("Cleanup: no SNS block-delete trigger found")
    except Exception as e:
        logger.warning("Cleanup: failed to check/uninstall SNS block-delete trigger: %s", e)


def handle_chat_anti_revoke_install(params, config: AssistantConfig):
    """POST /api/chat/anti-revoke/install — Install message anti-revoke triggers"""
    if not _is_restricted_enabled():
        return {"ok": False, "error": "Restricted features are disabled"}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        session_ids = _anti_revoke_get_session_ids(client)
        rows = []
        ok_count = 0
        for sid in session_ids:
            result = client.install_message_anti_revoke_trigger(sid)
            rows.append({"sessionId": sid, **result})
            if result.get("success"):
                ok_count += 1
        logger.info("Anti-revoke install: %d/%d sessions succeeded", ok_count, len(session_ids))
        return {"ok": True, "installed": True, "total": len(session_ids), "succeeded": ok_count}
    except Exception as e:
        logger.error(f"Failed to install anti-revoke triggers: {e}")
        return {"ok": False, "error": str(e)}


def handle_chat_anti_revoke_uninstall(params, config: AssistantConfig):
    """POST /api/chat/anti-revoke/uninstall — Uninstall message anti-revoke triggers"""
    if not _is_restricted_enabled():
        return {"ok": False, "error": "Restricted features are disabled"}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        session_ids = _anti_revoke_get_session_ids(client)
        ok_count = 0
        for sid in session_ids:
            result = client.uninstall_message_anti_revoke_trigger(sid)
            if result.get("success"):
                ok_count += 1
        logger.info("Anti-revoke uninstall: %d/%d sessions succeeded", ok_count, len(session_ids))
        return {"ok": True, "installed": False, "total": len(session_ids), "succeeded": ok_count}
    except Exception as e:
        logger.error(f"Failed to uninstall anti-revoke triggers: {e}")
        return {"ok": False, "error": str(e)}


def handle_chat_anti_revoke_status(params, config: AssistantConfig):
    """GET /api/chat/anti-revoke/status — Check message anti-revoke trigger status"""
    if not _is_restricted_enabled():
        return {"ok": True, "installed": False, "disabled": True}
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}
    try:
        session_ids = _anti_revoke_get_session_ids(client)
        # Sample up to 5 sessions to check status (avoid scanning all)
        sample = session_ids[:5]
        installed_count = 0
        for sid in sample:
            result = client.check_message_anti_revoke_trigger(sid)
            if result.get("installed"):
                installed_count += 1
        installed = installed_count > 0
        return {"ok": True, "installed": installed, "sampled": len(sample),
                "installed_in_sample": installed_count}
    except Exception as e:
        logger.error(f"Failed to check anti-revoke triggers: {e}")
        return {"ok": False, "error": str(e)}


def _download_sns_images(posts: list, images_dir: str) -> dict:
    """Download and decrypt all images/videos for SNS posts. Mutates posts to set media_list[].local_path."""
    from src.wechat.image_decrypt import download_and_decrypt

    MAX_DOWNLOAD_ITEMS = 1000
    MAX_DOWNLOAD_SECONDS = 300

    def _is_video_url(url: str) -> bool:
        if not url:
            return False
        lower = url.lower()
        return ("snsvideodownload" in lower or
                ".mp4" in lower or
                ("video" in lower and "vweixinthumb" not in lower))

    def _fix_sns_url(url: str, token: str = "", is_video: bool = False) -> str:
        """Build complete CDN URL with token (WeFlow's fixSnsUrl logic)."""
        if not url:
            return url
        # Convert http to https
        fixed = url.replace("http://", "https://")
        # For images: remove size suffix (e.g., /150, /200, /480) and replace with /0
        if not is_video:
            import re
            fixed = re.sub(r"/(150|200|480)($|\?)", r"/0\2", fixed)
        # Append token if available
        if token:
            # Remove existing token/idx params
            import re
            fixed = re.sub(r"[?&]token=[^&]*", "", fixed)
            fixed = re.sub(r"[?&]idx=[^&]*", "", fixed)
            sep = "&" if "?" in fixed else "?"
            fixed = f"{fixed}{sep}token={token}&idx=1"
        return fixed

    os.makedirs(images_dir, exist_ok=True)
    stats = {"total": 0, "downloaded": 0, "errors": 0, "posts_with_images": 0, "skipped": 0}
    start_time = time.monotonic()

    for post in posts:
        # Safety: check download limits
        if stats["downloaded"] >= MAX_DOWNLOAD_ITEMS:
            remaining = sum(1 for p in posts[posts.index(post):] if p.get("media_list"))
            stats["skipped"] = remaining
            logger.warning("SNS export media: hit MAX_DOWNLOAD_ITEMS=%d, skipping %d posts",
                           MAX_DOWNLOAD_ITEMS, remaining)
            break
        if time.monotonic() - start_time > MAX_DOWNLOAD_SECONDS:
            remaining = sum(1 for p in posts[posts.index(post):] if p.get("media_list"))
            stats["skipped"] = remaining
            logger.warning("SNS export media: hit %ds timeout, skipping %d posts",
                           MAX_DOWNLOAD_SECONDS, remaining)
            break

        media = post.get("media_list") or []
        if not media:
            continue
        stats["posts_with_images"] += 1
        for idx, m in enumerate(media):
            url = m.get("url") or m.get("imgUrl") or ""
            key = m.get("key", 0) or 0
            token = m.get("token") or m.get("thumbToken") or ""
            if not url:
                continue
            is_video = m.get("type") == "video" or _is_video_url(url)
            stats["total"] += 1
            try:
                # Build complete URL with token
                full_url = _fix_sns_url(url, token, is_video)
                timeout = 60 if is_video else 15
                data = download_and_decrypt(full_url, key if key else None, timeout=timeout)
                if not data:
                    stats["errors"] += 1
                    continue
                # Detect format
                if is_video or data[4:8] == b"ftyp":
                    ext = "mp4"
                elif data[:8] == b"\x89PNG\r\n\x1a\n":
                    ext = "png"
                elif data[:2] == b"\xff\xd8":
                    ext = "jpg"
                elif data[:4] == b"GIF8":
                    ext = "gif"
                elif data[:4] == b"RIFF":
                    ext = "webp"
                else:
                    ext = "mp4" if is_video else "jpg"
                # Use post id (or create_time) for filename
                post_id = post.get("id") or post.get("tid") or int(post.get("create_time", 0))
                filename = f"sns_{post_id}_{idx}.{ext}"
                out_path = os.path.join(images_dir, filename)
                with open(out_path, "wb") as f:
                    f.write(data)
                m["local_path"] = f"images/{filename}"
                stats["downloaded"] += 1
            except Exception as e:
                logger.warning(f"Failed to download sns media {url}: {e}")
                stats["errors"] += 1

    return stats


def _download_sns_avatars(posts: list, images_dir: str):
    """Download avatar images for SNS posts. Mutates posts to set user_head_local_path."""
    import urllib.request
    import ssl
    avatars_dir = os.path.join(images_dir, "avatars")
    os.makedirs(avatars_dir, exist_ok=True)
    seen = set()
    for post in posts:
        url = post.get("user_head_url", "")
        username = post.get("username", "")
        if not url or username in seen:
            continue
        seen.add(username)
        try:
            filename = f"avatar_{username}.jpg"
            out_path = os.path.join(avatars_dir, filename)
            if os.path.exists(out_path):
                post["user_head_local_path"] = f"images/avatars/{filename}"
                continue
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 MicroMessenger/3.9.12.29",
                "Referer": "https://wx.qq.com/"
            })
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = resp.read()
            if len(data) > 100:
                with open(out_path, "wb") as f:
                    f.write(data)
                post["user_head_local_path"] = f"images/avatars/{filename}"
        except Exception:
            pass


def _escape_html(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("\n", "<br>")
    )


def _format_sns_time(ts: int) -> str:
    if not ts:
        return ""
    try:
        from datetime import datetime as _dt
        d = _dt.fromtimestamp(int(ts))
        now = _dt.now()
        pad = lambda n: str(n).zfill(2)
        time_str = f"{pad(d.hour)}:{pad(d.minute)}"
        if d.year == now.year:
            return f"{d.month}月{d.day}日 {time_str}"
        return f"{d.year}年{d.month}月{d.day}日 {time_str}"
    except Exception:
        return ""


def _build_sns_html(posts: list, output_path: str) -> int:
    """Render WeFlow-style HTML for SNS posts with avatars and comments."""
    posts_html_parts = []
    for post in posts:
        media = post.get("media_list") or []
        media_count = len(media)
        if media_count == 1:
            grid_class = "grid-1"
        elif media_count in (2, 4):
            grid_class = "grid-2"
        else:
            grid_class = "grid-3"

        media_html = ""
        if media:
            media_html = f'<div class="mg {grid_class}">'
            for m in media:
                local = m.get("local_path") or ""
                mtype = m.get("type", "")
                if local:
                    if mtype == "video" or (local.lower().endswith((".mp4", ".mov"))):
                        media_html += f'<div class="mi"><video src="{_escape_html(local)}" controls preload="metadata"></video></div>'
                    else:
                        media_html += f'<div class="mi"><img src="{_escape_html(local)}" loading="lazy" onclick="openLb(this.src)" alt=""></div>'
                else:
                    label = "🎬 视频" if mtype == "video" else "图片未下载"
                    media_html += f'<div class="mi mi-placeholder"><span>{label}</span></div>'
            media_html += "</div>"

        content_html = ""
        if post.get("content"):
            content_html = f'<div class="txt">{_escape_html(post["content"])}</div>'

        location_html = ""
        if post.get("location"):
            location_html = f'<div class="loc"><span class="loc-i">📍</span><span class="loc-t">{_escape_html(post["location"])}</span></div>'

        # Avatar: use local image if available, otherwise letter
        nickname = _escape_html(post.get("nickname") or post.get("username") or "?")
        avatar_letter = _escape_html((nickname or "?")[0] or "?")
        avatar_local = post.get("user_head_local_path", "")
        if avatar_local:
            avatar_html = f'<div class="avatar"><img src="{_escape_html(avatar_local)}" alt=""></div>'
        else:
            avatar_html = f'<div class="avatar">{avatar_letter}</div>'

        # Comments and likes
        likes_list = post.get("likes", [])
        comments_list = post.get("comments", [])
        interaction_html = ""
        if likes_list or comments_list:
            interaction_parts = []
            if likes_list:
                like_names = []
                for l in likes_list:
                    if isinstance(l, str):
                        like_names.append(_escape_html(l))
                    elif isinstance(l, dict):
                        like_names.append(_escape_html(l.get("nickname", "")))
                interaction_parts.append(f'<div class="lk"><span class="lk-i">♥</span><span class="lk-n">{", ".join(like_names)}</span></div>')
            if comments_list:
                comment_lines = []
                for c in comments_list:
                    if isinstance(c, dict):
                        c_nick = _escape_html(c.get("nickname", ""))
                        c_content = _escape_html(c.get("content", ""))
                        c_ref = _escape_html(c.get("refNickname", ""))
                        if c_ref:
                            comment_lines.append(f'<div class="cm"><span class="cm-n">{c_nick}</span><span class="cm-r">回复</span><span class="cm-n">{c_ref}</span><span class="cm-t">：{c_content}</span></div>')
                        else:
                            comment_lines.append(f'<div class="cm"><span class="cm-n">{c_nick}</span><span class="cm-t">：{c_content}</span></div>')
                interaction_parts.append(f'<div class="cms">{"".join(comment_lines)}</div>')
            interaction_html = f'<div class="interactions">{"".join(interaction_parts)}</div>'

        time_str = _format_sns_time(post.get("create_time", 0))

        posts_html_parts.append(
            f'<div class="post">'
            f'{avatar_html}'
            f'<div class="body">'
            f'<div class="hd"><span class="nick">{nickname}</span><span class="tm">{time_str}</span></div>'
            f'{content_html}'
            f'{location_html}'
            f'{media_html}'
            f'{interaction_html}'
            f'</div></div>'
        )

    posts_html = "\n".join(posts_html_parts)

    html = (
        '<!DOCTYPE html>\n'
        '<html lang="zh-CN">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '<title>朋友圈导出</title>\n'
        '<style>\n'
        '*{margin:0;padding:0;box-sizing:border-box}\n'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;background:#F0EEE9;color:#2d2d2d;line-height:1.6;-webkit-font-smoothing:antialiased}\n'
        ':root{--bg:#F0EEE9;--card:#fff;--t1:#2d2d2d;--t2:#555;--t3:#888;--accent:#8B7355;--border:rgba(0,0,0,.06);--bg3:rgba(0,0,0,.03)}\n'
        '@media(prefers-color-scheme:dark){:root{--bg:#111;--card:#1e1e1e;--t1:#f5f5f5;--t2:#ccc;--t3:#999;--accent:#d4b896;--border:rgba(255,255,255,.12);--bg3:rgba(255,255,255,.08)}}\n'
        '.container{max-width:800px;margin:0 auto;padding:20px 24px 60px}\n'
        '.feed-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;padding:0 4px}\n'
        '.feed-hd h2{font-size:20px;font-weight:700}\n'
        '.feed-hd .info{font-size:12px;color:var(--t3)}\n'
        '.post{background:var(--card);border-radius:16px;border:1px solid var(--border);padding:20px;margin-bottom:24px;display:flex;gap:16px;box-shadow:0 2px 8px rgba(0,0,0,.02);transition:transform .2s,box-shadow .2s}\n'
        '.post:hover{transform:translateY(-2px);box-shadow:0 8px 16px rgba(0,0,0,.06)}\n'
        '.avatar{width:48px;height:48px;border-radius:12px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:600;flex-shrink:0;overflow:hidden}\n'
        '.avatar img{width:100%;height:100%;object-fit:cover}\n'
        '.body{flex:1;min-width:0}\n'
        '.hd{display:flex;flex-direction:column;margin-bottom:8px}\n'
        '.nick{font-size:15px;font-weight:700;color:var(--accent);margin-bottom:2px}\n'
        '.tm{font-size:12px;color:var(--t3)}\n'
        '.txt{font-size:15px;line-height:1.6;white-space:pre-wrap;word-break:break-word;margin-bottom:12px}\n'
        '.loc{display:flex;align-items:flex-start;gap:6px;font-size:13px;color:var(--t2);margin:-4px 0 12px}\n'
        '.loc-i{line-height:1.3}\n'
        '.loc-t{line-height:1.45;word-break:break-word}\n'
        '.mg{display:grid;gap:6px;margin-bottom:12px;max-width:320px}\n'
        '.grid-1{max-width:300px}\n'
        '.grid-1 .mi{border-radius:12px}\n'
        '.grid-1 .mi img{aspect-ratio:auto;max-height:480px;object-fit:contain;background:var(--bg3)}\n'
        '.grid-2{grid-template-columns:1fr 1fr}\n'
        '.grid-3{grid-template-columns:1fr 1fr 1fr}\n'
        '.mi{overflow:hidden;border-radius:12px;background:var(--bg3);position:relative;aspect-ratio:1}\n'
        '.mi img{width:100%;height:100%;object-fit:cover;display:block;cursor:zoom-in;transition:opacity .2s}\n'
        '.mi img:hover{opacity:.9}\n'
        '.mi video{width:100%;height:100%;object-fit:cover;display:block;background:#000}\n'
        '.mi-placeholder{display:flex;align-items:center;justify-content:center;color:var(--t3);font-size:12px;background:var(--bg3);border:1px dashed var(--border)}\n'
        '.interactions{margin-top:12px;padding:12px 16px;border-radius:10px;background:var(--bg3);font-size:14px}\n'
        '.lk{display:flex;align-items:center;gap:4px;margin-bottom:4px}\n'
        '.lk-i{color:#e74c3c;font-size:14px}\n'
        '.lk-n{color:var(--accent);font-weight:500}\n'
        '.cms{line-height:1.8}\n'
        '.cm{margin-bottom:2px}\n'
        '.cm-n{color:var(--accent);font-weight:500}\n'
        '.cm-r{color:var(--t3);font-size:12px;margin:0 2px}\n'
        '.cm-t{color:var(--t1)}\n'
        '.lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9999;align-items:center;justify-content:center;cursor:zoom-out}\n'
        '.lb.on{display:flex}\n'
        '.lb img{max-width:92vw;max-height:92vh;object-fit:contain;border-radius:4px}\n'
        '.btt{position:fixed;right:24px;bottom:32px;width:44px;height:44px;border-radius:50%;background:var(--card);box-shadow:0 2px 12px rgba(0,0,0,.12);border:1px solid var(--border);cursor:pointer;font-size:18px;display:none;align-items:center;justify-content:center;z-index:100;color:var(--t2)}\n'
        '.btt:hover{transform:scale(1.1)}\n'
        '.btt.show{display:flex}\n'
        '.ft{text-align:center;padding:32px 0 24px;font-size:12px;color:var(--t3)}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="container">\n'
        f'    <div class="feed-hd"><h2>朋友圈</h2><span class="info">共 {len(posts)} 条</span></div>\n'
        f'    {posts_html}\n'
        '    <div class="ft">由 微信助手 导出</div>\n'
        '</div>\n'
        '<div class="lb" id="lb" onclick="closeLb()"><img id="lbi" src=""></div>\n'
        '<button class="btt" id="btt" onclick="scrollTo({top:0,behavior:\'smooth\'})">↑</button>\n'
        '<script>\n'
        'function openLb(s){document.getElementById("lbi").src=s;document.getElementById("lb").classList.add("on");document.body.style.overflow="hidden"}\n'
        'function closeLb(){document.getElementById("lb").classList.remove("on");document.body.style.overflow=""}\n'
        'document.addEventListener("keydown",function(e){if(e.key==="Escape")closeLb()})\n'
        'window.addEventListener("scroll",function(){document.getElementById("btt").classList.toggle("show",window.scrollY>600)})\n'
        '</script>\n'
        '</body>\n'
        '</html>'
    )

    try:
        Path(output_path).write_text(html, encoding="utf-8")
        return 1
    except Exception as e:
        logger.error(f"Failed to write SNS HTML: {e}")
        return 0


def handle_sns_export(params, config: AssistantConfig):
    """POST /api/sns/export — Export timeline to WeFlow-style HTML with local decrypted images.

    Query params:
        dry_run: if "true", only estimate size without actually exporting.
    """
    reader = _get_wcdb_sns_reader()
    if not reader:
        return {"ok": False, "error": "WCDB not available"}

    try:
        # Parse filter params from POST body
        body = params.get("_body", {}) or {}
        username = body.get("username", "")
        start_time = int(body.get("start_time", 0) or 0)
        end_time = int(body.get("end_time", 0) or 0)

        # Output directory and HTML path
        export_dir = os.path.abspath("data/sns_export")
        html_path = os.path.join(export_dir, "index.html")
        images_dir = os.path.join(export_dir, "images")

        # 1. Fetch posts — apply filters
        usernames = [username] if username else None
        limit = 5000
        raw_posts = reader.get_timeline(
            limit=limit, offset=0,
            usernames=usernames,
            start_time=start_time, end_time=end_time,
        )
        truncated = len(raw_posts) >= limit

        # 2. Map to frontend-expected fields (same as handle_sns_timeline)
        # Also collect avatars for HTML export — batch to avoid huge DLL calls
        posts = []
        all_usernames = list(set(p.get("username", "") for p in raw_posts if p.get("username")))
        avatar_map = {}
        if all_usernames:
            try:
                client = get_wcdb_client()
                if client:
                    # Batch avatar queries (100 per call) to avoid DLL timeout
                    for batch_start in range(0, len(all_usernames), 100):
                        batch_ids = all_usernames[batch_start:batch_start + 100]
                        batch_map = client.get_avatar_urls(batch_ids)
                        if batch_map:
                            avatar_map.update(batch_map)
            except Exception:
                pass

        for post in raw_posts:
            media_list = post.get("media", [])
            processed_media = []

            # Extract video encryption key from rawXml (WeFlow pattern)
            video_key = ""
            raw_xml = post.get("rawXml", "")
            if raw_xml and "<enc" in raw_xml:
                import re as _re_enc2
                enc_match = _re_enc2.search(r'<enc\s+key="(\d+)"', raw_xml)
                if enc_match:
                    video_key = enc_match.group(1)

            for m in media_list:
                url = m.get("url", "")
                mtype = m.get("type", "")
                if not mtype:
                    if ("snsvideodownload" in url.lower() or
                        (".mp4" in url.lower()) or
                        ("video" in url.lower() and "vweixinthumb" not in url.lower())):
                        mtype = "video"
                    elif url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                        mtype = "image"
                    else:
                        mtype = "image"
                # For videos: use enc key from XML if media key is 0
                media_key = m.get("key", 0)
                if mtype == "video" and (not media_key or str(media_key) == "0") and video_key:
                    media_key = int(video_key)
                processed_media.append({
                    "type": mtype,
                    "thumb_url": m.get("thumb", url),
                    "url": url,
                    "key": media_key,
                    "token": m.get("token", ""),
                    "thumbToken": m.get("thumbToken") or m.get("thumb_token", ""),
                    "encIdx": m.get("encIdx") or m.get("enc_idx", ""),
                })
            posts.append({
                "id": post.get("id", ""),
                "username": post.get("username", ""),
                "nickname": post.get("nickname", ""),
                "user_head_url": avatar_map.get(post.get("username", ""), ""),
                "create_time": post.get("createTime", 0),
                "content": post.get("contentDesc", ""),
                "like_count": len(post.get("likes", [])),
                "comment_count": len(post.get("comments", [])),
                "likes": post.get("likes", []),
                "comments": post.get("comments", []),
                "media_list": processed_media,
                "location": post.get("location", ""),
            })

        # ── dry_run: estimate size only ──
        dry_run = (params.get("dry_run", [""])[0] or "").lower() == "true"
        if dry_run:
            image_count = sum(len(p.get("media_list") or []) for p in posts)
            result = {"ok": True, **_estimate_export_size(len(posts), image_count)}
            if truncated:
                result["truncated"] = True
                result["limit"] = limit
            return result

        broadcast_event("sns_export_progress", {"status": "started", "export_dir": export_dir})

        # 3. Download and decrypt images locally
        img_stats = _download_sns_images(posts, images_dir)
        broadcast_event("sns_export_progress", {"status": "exporting", "stage": "images", "stats": img_stats})

        # 3b. Download avatar images
        _download_sns_avatars(posts, images_dir)

        # 4. Build HTML
        ok = _build_sns_html(posts, html_path)
        if not ok:
            return {"ok": False, "error": "Failed to write HTML"}

        stats = {
            "total": len(posts),
            "exported": len(posts),
            "images": img_stats,
        }
        broadcast_event("sns_export_progress", {"status": "completed", "stats": stats, "path": html_path, "export_dir": export_dir})
        return {"ok": True, "stats": stats, "path": html_path, "export_dir": export_dir}
    except Exception as e:
        logger.error(f"Failed to export SNS: {e}")
        broadcast_event("sns_export_progress", {"status": "error", "error": str(e)})
        return {"ok": False, "error": str(e)}


# ── 公众号 API ─────────────────────────────────────────────────────────

def handle_oa_accounts(params, config: AssistantConfig):
    """GET /api/oa/accounts — List all OA accounts"""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        from src.assistant.oa_parser import get_oa_sessions
        sessions = get_oa_sessions(client)
        # Use resolve_nickname to get proper display names for gh_ accounts
        return {
            "ok": True,
            "data": [
                {
                    "username": s.get("username"),
                    "nickname": client.resolve_nickname(s.get("username", ""))
                }
                for s in sessions
            ],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_oa_groups_list(params, config: AssistantConfig):
    """GET /api/oa/groups — List OA groups"""
    manager = OAGroupManager(config)
    groups = manager.list_groups()

    # Filter service account IDs from group.accounts
    all_gh = list(set(gh for g in groups for gh in (g.accounts or []) if gh and gh.startswith("gh_")))
    service_ids = set()
    if all_gh:
        try:
            from src.assistant.oa_parser import _get_service_account_ids
            client = get_wcdb_client()
            if client:
                fake_sessions = [{"username": gh} for gh in all_gh]
                service_ids = _get_service_account_ids(client, fake_sessions)
        except Exception:
            pass

    return {
        "ok": True,
        "data": [
            {"id": g.id, "name": g.name,
             "accounts": [a for a in (g.accounts or []) if a not in service_ids],
             "schedule": g.schedule,
             "cron_expr": g.cron_expr,
             "digest_template": g.digest_template, "push_target": g.push_target,
             "enabled": g.enabled, "lookback_hours": g.lookback_hours,
             "lookback_mode": g.lookback_mode, "custom_prompt": g.custom_prompt}
            for g in groups
        ],
    }


def _filter_service_account_ids(accounts):
    """Filter out service account IDs (微信支付/信用卡还款 etc.) from a list."""
    if not accounts:
        return accounts
    try:
        from src.assistant.oa_parser import _get_service_account_ids
        client = get_wcdb_client()
        if not client:
            return accounts
        fake_sessions = [{"username": gh} for gh in accounts if isinstance(gh, str) and gh.startswith("gh_")]
        if not fake_sessions:
            return accounts
        service_ids = _get_service_account_ids(client, fake_sessions)
        if not service_ids:
            return accounts
        return [a for a in accounts if a not in service_ids]
    except Exception:
        return accounts


def _notify_assistant_scheduler(config: AssistantConfig):
    """Notify the DigestScheduler and OAMonitor of config changes after CRUD.

    The server creates a fresh config object from disk for each request,
    so the scheduler's in-memory config is a different object. We need
    to hot-reload it so new/changed cron schedules take effect immediately.
    """
    try:
        from src.web.server import _assistant_scheduler, _oa_monitor
        if _assistant_scheduler is not None:
            _assistant_scheduler.update_config(config)
            logger.debug("Config change: scheduler hot-reloaded")
        if _oa_monitor is not None:
            _oa_monitor.update_config(config)
            logger.debug("Config change: OA monitor hot-reloaded")
    except Exception as e:
        logger.debug("Config change: scheduler/monitor notify skipped (%s)", e)


def handle_oa_groups_create(params, config: AssistantConfig):
    """POST /api/oa/groups — Create OA group"""
    try:
        body = params.get("_body", {})
        name = body.get("name", "")
        accounts = body.get("accounts", [])
        cron_expr = body.get("cron_expr", "")
        digest_template = body.get("digest_template", "default")
        push_target = body.get("push_target", "")
        lookback_hours = body.get("lookback_hours", 24)
        lookback_mode = body.get("lookback_mode", "auto")
        custom_prompt = body.get("custom_prompt", "")

        # Filter out service account IDs (e.g. 微信支付/信用卡还款)
        accounts = _filter_service_account_ids(accounts)

        manager = OAGroupManager(config)
        group = manager.create_group(
            name, accounts, cron_expr=cron_expr,
            digest_template=digest_template, push_target=push_target,
            lookback_hours=lookback_hours, lookback_mode=lookback_mode,
            custom_prompt=custom_prompt,
        )

        _notify_assistant_scheduler(config)

        return {
            "ok": True,
            "data": {"id": group.id, "name": group.name},
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_oa_groups_update(params, config: AssistantConfig):
    """PUT /api/oa/groups/:id — Update OA group"""
    try:
        body = params.get("_body", {})
        group_id = params.get("id", [""])[0]

        # Filter out service account IDs before persisting
        if "accounts" in body and isinstance(body["accounts"], list):
            body["accounts"] = _filter_service_account_ids(body["accounts"])

        manager = OAGroupManager(config)
        group = manager.update_group(group_id, **body)

        _notify_assistant_scheduler(config)

        return {"ok": True, "data": {"id": group.id} if group else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_oa_groups_delete(params, config: AssistantConfig):
    """DELETE /api/oa/groups/:id — Delete OA group"""
    try:
        group_id = params.get("id", [""])[0]
        manager = OAGroupManager(config)
        success = manager.delete_group(group_id)

        _notify_assistant_scheduler(config)

        return {"ok": success}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_oa_digest_run(params, config: AssistantConfig):
    """POST /api/oa/digest/run/:groupId — Generate digest manually"""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        group_id = params.get("groupId", [""])[0]
        broadcast_event("oa_digest_progress", {"status": "started", "group_id": group_id})
        service = OADigestService(config, client)
        # Pass summarizer for unified LLM calls
        summarizer_ok = False
        try:
            from src.summarize import create_summarizer
            from src.config import load_config
            bot_cfg = load_config()
            service._summarizer = create_summarizer(bot_cfg)
            summarizer_ok = True
        except ValueError as e:
            # AI provider not configured
            logger.warning("[OA-DIGEST] AI not configured: %s", e)
            broadcast_event("oa_digest_progress", {"status": "error", "group_id": group_id, "error": "AI未配置"})
            return {"ok": False, "error": "AI未配置，请先在设置中配置AI提供商（API Key）"}
        except Exception as e:
            logger.warning("[OA-DIGEST] Summarizer creation failed: %s (will try fallback)", e)
        result = service.generate_digest(group_id, force=True)
        # Normalize response to use "ok" instead of "success"
        result["ok"] = result.pop("success", True)
        broadcast_event("oa_digest_progress", {
            "status": "completed",
            "group_id": group_id,
            "articles_count": result.get("articles_count", 0),
            "digest_text": result.get("digest_text", ""),
        })

        # Push to WeChat via iLink (if configured for this OA group)
        if result.get("ok") and result.get("digest_text"):
            # Write to Outbox for push history tracking
            oa_nid = None
            try:
                from src.assistant.outbox import Outbox
                import json as _json
                outbox = Outbox()
                oa_title = f"📰 {group.name} · 公众号摘要"
                oa_content = _json.dumps({
                    "group": group.name,
                    "articles_count": result.get("articles_count", 0),
                    "digest": result['digest_text'],
                    "display": f"公众号组: {group.name}\n文章数: {result.get('articles_count', 0)} 篇\n\n{result['digest_text']}",
                }, ensure_ascii=False)
                oa_nid = outbox.add(
                    notif_type="oa_digest",
                    chat_id=group_id,
                    group_name=group.name,
                    title=oa_title,
                    content=oa_content,
                    priority="normal",
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).debug("OA outbox write skipped: %s", e)

            try:
                from src.assistant.oa_groups import OAGroupManager
                manager = OAGroupManager(config)
                group = manager.get_group(group_id)
                if group and group.push_target == "ilink":
                    from src.wechat.ilink_push import get_ilink_push, format_for_wechat
                    ilink = get_ilink_push()
                    if ilink.is_available():
                        title = f"📰 {group.name} · 公众号摘要"
                        ac = result.get('articles_count', 0)
                        content = f"📄 {ac} 篇文章\n\n{result['digest_text']}"
                        msg = format_for_wechat(title, content)
                        push_result = ilink.send_message(msg)
                        push_ok = push_result.get("success", False)
                        push_err = push_result.get("error", "") if not push_ok else ""
                        result["ilink_push"] = "success" if push_ok else f"failed: {push_err}"
                        if oa_nid:
                            try:
                                from src.assistant.outbox import Outbox
                                outbox = Outbox()
                                outbox.update_push_result(
                                    oa_nid, "ilink",
                                    "success" if push_ok else "failed",
                                    push_err,
                                )
                            except Exception:
                                pass
                    else:
                        # iLink configured but not available
                        if oa_nid:
                            try:
                                from src.assistant.outbox import Outbox
                                outbox = Outbox()
                                outbox.update_push_result(
                                    oa_nid, "ilink", "failed", "iLink推送通道未绑定或已断开",
                                )
                            except Exception:
                                pass
                else:
                    # No iLink push target — still record in outbox for history tracking
                    if oa_nid:
                        try:
                            from src.assistant.outbox import Outbox
                            outbox = Outbox()
                            outbox.update_push_result(
                                oa_nid, "local", "skipped", "未配置微信推送通道",
                            )
                        except Exception:
                            pass
                        # Broadcast push result to WebSocket clients
                        try:
                            broadcast_event("oa_digest_push_result", {
                                "group_name": group.name,
                                "success": push_ok,
                                "error": push_err,
                            })
                        except Exception:
                            pass
                    else:
                        result["ilink_push"] = "skipped: not bound"
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("iLink push for OA digest failed: %s", e)
                result["ilink_push"] = f"error: {e}"

        return result
    except Exception as e:
        broadcast_event("oa_digest_progress", {"status": "error", "group_id": group_id, "error": str(e)})
        return {"ok": False, "error": str(e)}


def handle_oa_search(params, config: AssistantConfig):
    """GET /api/oa/search — Search OA articles across all accounts"""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        keyword = params.get("q", [""])[0] or params.get("keyword", [""])[0]
        if not keyword:
            return {"ok": False, "error": "Missing keyword"}

        from src.assistant.oa_parser import fetch_oa_articles, get_oa_sessions

        # Get all OA sessions
        oa_sessions = get_oa_sessions(client)
        all_articles = []
        kw_lower = keyword.lower()

        for session in oa_sessions:
            gh_id = session.get("username", "")
            try:
                articles = fetch_oa_articles(client, gh_id, limit=50)
                for art in articles:
                    # Case-insensitive search in title and digest
                    title_lower = (art.title or "").lower()
                    digest_lower = (art.digest or "").lower()
                    source_lower = (art.source_name or "").lower()
                    if kw_lower in title_lower or kw_lower in digest_lower or kw_lower in source_lower:
                        all_articles.append(art)
            except Exception as e:
                logger.debug(f"Search failed for {gh_id}: {e}")

        # Sort by pub_time desc
        all_articles.sort(key=lambda a: a.pub_time or a.timestamp or 0, reverse=True)

        return {
            "ok": True,
            "data": [
                {
                    "title": a.title,
                    "url": a.url,
                    "digest": a.digest,
                    "cover": a.cover,
                    "source_name": a.source_name,
                    "source_username": a.source_username,
                    "pub_time": a.pub_time,
                    "gh_id": a.gh_id,
                    "timestamp": a.timestamp,
                    "create_time": a.pub_time or a.timestamp,
                }
                for a in all_articles[:50]
            ],
            "total": len(all_articles),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_oa_articles(params, config: AssistantConfig):
    """GET /api/oa/articles — Get all articles from a specific OA"""
    client = get_wcdb_client()
    if not client:
        return {"ok": False, "error": "WCDB not available"}

    try:
        gh_id = params.get("gh_id", [""])[0]
        if not gh_id:
            return {"ok": False, "error": "Missing gh_id"}

        limit = int(params.get("limit", ["50"])[0]) if params.get("limit") else 50

        from src.assistant.oa_parser import fetch_oa_articles
        articles = fetch_oa_articles(client, gh_id, limit=limit)

        return {
            "ok": True,
            "data": [
                {
                    "title": a.title,
                    "url": a.url,
                    "digest": a.digest,
                    "cover": a.cover,
                    "source_name": a.source_name,
                    "source_username": a.source_username,
                    "pub_time": a.pub_time,
                    "gh_id": a.gh_id,
                    "timestamp": a.timestamp,
                    "create_time": a.pub_time or a.timestamp,
                }
                for a in articles
            ],
            "total": len(articles),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 定时任务概览 API (首页只读) ────────────────────────────────────────────

def handle_scheduled_tasks_overview(params, config: AssistantConfig):
    """GET /api/scheduled-tasks — Read-only overview for Dashboard.

    Returns a unified list of scheduled tasks from digest_groups + oa_groups,
    formatted for display (no cron expressions, human-readable schedule).
    """
    tasks = []

    # 群聊摘要
    for dg in config.digest_groups:
        schedule_label = _format_schedule(dg.schedule, dg.cron_expr)
        push_label = "推送微信" if dg.push_target == "ilink" else "不推送"
        mode_label = "仅未读" if dg.unread_only else "全部消息"
        tasks.append({
            "type": "group_digest",
            "type_label": "群聊摘要",
            "name": dg.group_name or dg.chat_id,
            "schedule": schedule_label,
            "lookback": f"{dg.lookback_hours}h",
            "mode": mode_label,
            "push": push_label,
            "enabled": dg.enabled,
        })

    # 公众号摘要
    for oa in config.oa_groups:
        schedule_label = _format_schedule([], oa.cron_expr)
        push_label = "推送微信" if oa.push_target == "ilink" else ("推送" + oa.push_target if oa.push_target else "不推送")
        account_count = len(oa.accounts) if oa.accounts else 0
        tasks.append({
            "type": "oa_digest",
            "type_label": "公众号摘要",
            "name": oa.name or oa.id,
            "schedule": schedule_label or "手动触发",
            "lookback": f"{oa.lookback_hours}h",
            "account_count": account_count,
            "push": push_label,
            "enabled": oa.enabled,
        })

    enabled_count = sum(1 for t in tasks if t["enabled"])
    return {
        "ok": True,
        "data": {
            "tasks": tasks,
            "total": len(tasks),
            "enabled": enabled_count,
        },
    }


def _format_schedule(schedule: list, cron_expr: str) -> str:
    """Format schedule list or cron expression to human-readable string."""
    if cron_expr:
        # Parse common cron patterns
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            minute, hour, day, month, dow = parts
            # Simple patterns
            if day == "*" and month == "*" and dow == "*":
                times = hour.split(",")
                times = [f"{h.zfill(2)}:{minute.zfill(2)}" for h in times]
                return f"每天 {'、'.join(times)}"
            if dow == "1-5":
                times = hour.split(",")
                times = [f"{h.zfill(2)}:{minute.zfill(2)}" for h in times]
                return f"工作日 {'、'.join(times)}"
        return cron_expr

    if schedule:
        formatted = [s if ":" in s else f"{s}:00" for s in schedule]
        return f"每天 {'、'.join(formatted)}"

    return ""


# ── 调度器 API ──────────────────────────────────────────────────────────

def handle_scheduler_list(params, config: AssistantConfig):
    """GET /api/scheduler/tasks — List all scheduled tasks"""
    from src.scheduler.task_scheduler import get_task_scheduler
    try:
        scheduler = get_task_scheduler()
        tasks = scheduler.list_tasks()
        return {
            "ok": True,
            "data": [
                {
                    "id": t.id,
                    "name": t.name,
                    "task_type": t.task_type,
                    "cron_expr": t.cron_expr,
                    "function_ref": t.function_ref,
                    "enabled": t.enabled,
                    "last_run_time": t.last_run_time,
                    "status": t.status,
                }
                for t in tasks
            ],
        }
    except Exception as e:
        logger.error(f"Failed to list scheduler tasks: {e}")
        return {"ok": False, "error": str(e)}


def handle_scheduler_create(params, config: AssistantConfig):
    """POST /api/scheduler/tasks — Create a scheduled task"""
    from src.scheduler.task_scheduler import get_task_scheduler, ScheduledTask
    try:
        body = params.get("_body", {})
        task = ScheduledTask(
            name=body.get("name", ""),
            task_type=body.get("task_type", ""),
            cron_expr=body.get("cron_expr", ""),
            function_ref=body.get("function_ref", ""),
            enabled=body.get("enabled", True),
        )
        scheduler = get_task_scheduler()
        task_id = scheduler.add_task(task)
        return {"ok": True, "data": {"id": task_id}}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Failed to create scheduler task: {e}")
        return {"ok": False, "error": str(e)}


def handle_scheduler_delete(params, config: AssistantConfig):
    """DELETE /api/scheduler/tasks/:id — Delete a scheduled task"""
    from src.scheduler.task_scheduler import get_task_scheduler
    try:
        task_id = params.get("id", [""])[0]
        scheduler = get_task_scheduler()
        success = scheduler.remove_task(task_id)
        return {"ok": success}
    except Exception as e:
        logger.error(f"Failed to delete scheduler task: {e}")
        return {"ok": False, "error": str(e)}


def handle_scheduler_update(params, config: AssistantConfig):
    """PUT /api/scheduler/tasks/:id — Update a scheduled task"""
    from src.scheduler.task_scheduler import get_task_scheduler
    try:
        task_id = params.get("id", [""])[0]
        body = params.get("_body", {})
        scheduler = get_task_scheduler()
        success = scheduler.update_task(task_id, **body)
        return {"ok": success}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Failed to update scheduler task: {e}")
        return {"ok": False, "error": str(e)}


# ── API Router ─────────────────────────────────────────────────────────

def handle_api_request(path: str, params: dict, config: AssistantConfig, body: dict = None):
    """Main API router for new endpoints."""
    logger.info("[API-ROUTER] handle_api_request called: path=%s thread=%s", path, threading.current_thread().name)
    # Add body to params for POST handlers
    if body:
        params["_body"] = body

    # ── 定时任务概览 (首页只读) ─────────────────────────────────────────────
    if path == "/api/scheduled-tasks":
        return handle_scheduled_tasks_overview(params, config)

    # ── 收藏 ─────────────────────────────────────────────────────────
    if path == "/api/fav/list":
        return handle_fav_list(params, config)
    if path == "/api/fav/tags":
        return handle_fav_tags(params, config)
    if path == "/api/fav/export":
        return handle_fav_export(params, config)

    # ── 会话管理 ─────────────────────────────────────────────────────────
    if path == "/api/chat/image":
        return handle_chat_image(params, config)
    if path == "/api/chat/sessions":
        return handle_chat_sessions(params, config)
    if path == "/api/chat/messages":
        return handle_chat_messages(params, config)
    if path == "/api/chat/export":
        return handle_chat_export(params, config)
    if path == "/api/chat/members":
        return handle_chat_group_members(params, config)
    if path == "/api/groups/member-counts":
        return handle_group_member_counts(params, config)
    if path == "/api/chat/common-groups":
        return handle_chat_common_groups(params, config)
    if path == "/api/chat/anti-revoke/install":
        return handle_chat_anti_revoke_install(params, config)
    if path == "/api/chat/anti-revoke/uninstall":
        return handle_chat_anti_revoke_uninstall(params, config)
    if path == "/api/chat/anti-revoke/status":
        return handle_chat_anti_revoke_status(params, config)

    # ── 朋友圈 ─────────────────────────────────────────────────────────
    if path == "/api/sns/timeline":
        return handle_sns_timeline(params, config)
    if path == "/api/sns/search":
        return handle_sns_search(params, config)
    if path == "/api/sns/protect/install":
        return handle_sns_protect_install(params, config)
    if path == "/api/sns/protect/uninstall":
        return handle_sns_protect_uninstall(params, config)
    if path == "/api/sns/protect/status":
        return handle_sns_protect_status(params, config)
    if path == "/api/sns/export":
        return handle_sns_export(params, config)

    # ── 公众号 ─────────────────────────────────────────────────────────
    if path == "/api/oa/accounts":
        return handle_oa_accounts(params, config)
    if path == "/api/oa/groups":
        return handle_oa_groups_list(params, config)
    if path == "/api/oa/groups/create":
        return handle_oa_groups_create(params, config)
    if path == "/api/oa/search":
        return handle_oa_search(params, config)
    if path == "/api/oa/articles":
        return handle_oa_articles(params, config)

    # Check for /api/oa/groups/:id pattern
    if path.startswith("/api/oa/groups/") and len(path.split("/")) == 5:
        # Disambiguate PUT (update) vs DELETE using HTTP method, not body presence
        # (empty body {} is falsy but still a valid PUT)
        parts = path.split("/")
        group_id = parts[4]
        params["id"] = [group_id]
        if params.get("_method") == "DELETE":
            return handle_oa_groups_delete(params, config)
        else:
            # PUT or any other method with body → update
            return handle_oa_groups_update(params, config)

    # Check for /api/oa/digest/run/:groupId
    if path.startswith("/api/oa/digest/run/"):
        group_id = path.split("/")[-1]
        params["groupId"] = [group_id]
        return handle_oa_digest_run(params, config)

    # ── 调度器 ─────────────────────────────────────────────────────────
    if path == "/api/scheduler/tasks" and not params.get("_body"):
        return handle_scheduler_list(params, config)
    if path == "/api/scheduler/tasks" and params.get("_body"):
        return handle_scheduler_create(params, config)

    # Check for /api/scheduler/tasks/:id
    if path.startswith("/api/scheduler/tasks/") and len(path.split("/")) == 5:
        task_id = path.split("/")[-1]
        params["id"] = [task_id]
        if params.get("_body") or body:
            return handle_scheduler_update(params, config)
        else:
            return handle_scheduler_delete(params, config)

    # ── 推送记录 ─────────────────────────────────────────────────────────
    if path == "/api/push/history":
        return handle_push_history(params, config)
    if path == "/api/push/stats":
        return handle_push_stats(params, config)

    # ── 打开文件夹 ──
    if path == "/api/export/open-folder":
        return handle_export_open_folder(params, config)

    return None  # Not handled by this module


# ── 推送记录 API ─────────────────────────────────────────────────────────

def handle_push_history(params, config: AssistantConfig):
    """GET /api/push/history — Push delivery history with filters."""
    try:
        from src.assistant.outbox import Outbox
        outbox = Outbox()
        notif_type = (params.get("type", [""]) or [""])[0]
        push_status = (params.get("push_status", [""]) or [""])[0]
        limit = int((params.get("limit", ["50"]) or ["50"])[0])
        offset = int((params.get("offset", ["0"]) or ["0"])[0])
        date_from = (params.get("date_from", [""]) or [""])[0]
        date_to = (params.get("date_to", [""]) or [""])[0]
        records = outbox.list_push_history(
            notif_type=notif_type,
            push_status=push_status,
            limit=limit,
            offset=offset,
            date_from=date_from,
            date_to=date_to,
        )
        return {"ok": True, "records": records}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_push_stats(params, config: AssistantConfig):
    """GET /api/push/stats — Push delivery statistics."""
    try:
        from src.assistant.outbox import Outbox
        outbox = Outbox()
        return {"ok": True, **outbox.get_push_stats()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 打开文件夹 ─────────────────────────────────────────────────────────


def _estimate_export_size(item_count: int, image_count: int, voice_count: int = 0,
                          video_count: int = 0,
                          avg_image_kb: int = 200, avg_voice_kb: int = 10,
                          avg_video_mb: float = 3.0,
                          warning_mb: int = 10) -> dict:
    """Estimate export size based on item/image/voice/video counts.

    Returns { estimated_mb, image_count, voice_count, video_count, item_count, size_warning }.
    size_warning is True when estimated_mb > warning_mb.
    """
    estimated_mb = round((image_count * avg_image_kb + voice_count * avg_voice_kb) / 1024 + video_count * avg_video_mb, 1)
    return {
        "item_count": item_count,
        "image_count": image_count,
        "voice_count": voice_count,
        "video_count": video_count,
        "estimated_mb": estimated_mb,
        "size_warning": estimated_mb > warning_mb,
    }


def _count_chat_record_images(records: list, depth: int = 0, max_depth: int = 5) -> int:
    """Recursively count images inside chat_records, including nested type=17 sub-records."""
    count = 0
    for r in records:
        r_type = int(r.get("type", 0) or 0)
        if r_type == 2:
            count += 1
        elif r_type == 17 and depth < max_depth:
            count += _count_chat_record_images(r.get("sub_records", []), depth + 1, max_depth)
    return count


def _count_fav_images(items: list) -> int:
    """Count images including those inside chat_records and nested records for favorites export."""
    count = 0
    for it in items:
        # Top-level images
        count += len(it.get("images") or it.get("media_list") or [])
        # Chat record images (type=14)
        if it.get("type") == 14:
            count += _count_chat_record_images(it.get("chat_records", []))
    return count


def handle_export_open_folder(params, config: AssistantConfig):
    """POST /api/export/open-folder?type=fav|sns|chat — Open the export directory in the OS file explorer."""
    import subprocess
    import sys as _sys

    # Support both query param and POST body
    body = params.get("_body", {}) or {}
    kind = (body.get("type") or params.get("type", ["fav"])[0] or "fav").lower()
    if kind == "fav":
        export_dir = os.path.abspath(config.fav_export.output_dir or "data/fav_export")
    elif kind == "sns":
        export_dir = os.path.abspath("data/sns_export")
    elif kind == "chat":
        # Open the most recent chat export directory
        chat_export_base = os.path.abspath("data/chat_export")
        if os.path.isdir(chat_export_base):
            # Find the most recently created subdirectory
            subdirs = sorted(
                [d for d in os.listdir(chat_export_base)
                 if os.path.isdir(os.path.join(chat_export_base, d))],
                key=lambda d: os.path.getmtime(os.path.join(chat_export_base, d)),
                reverse=True,
            )
            if subdirs:
                export_dir = os.path.join(chat_export_base, subdirs[0])
            else:
                export_dir = chat_export_base
        else:
            export_dir = chat_export_base
    else:
        return {"ok": False, "error": f"Unknown export type: {kind}"}

    if not os.path.isdir(export_dir):
        os.makedirs(export_dir, exist_ok=True)

    try:
        if _sys.platform == "win32":
            os.startfile(export_dir)  # noqa
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", export_dir])
        else:
            subprocess.Popen(["xdg-open", export_dir])
    except Exception as e:
        return {"ok": False, "error": f"Failed to open folder: {e}"}

    return {"ok": True, "path": export_dir}
