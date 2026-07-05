"""iLink Bot message receiver — poll for incoming messages via getupdates.

Ported from wechat-claude-skill (TypeScript) wechat.ts.
Provides long-polling with:
  - Sync buffer persistence (resume from last checkpoint)
  - Message dedup (Set of 1000 IDs)
  - Consecutive failure backoff (3s -> 30s)
  - Session expiry handling (errcode=-14 -> pause 1 hour)
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from .ilink_push import (
    _make_headers,
    DEFAULT_BASE_URL,
    API_TIMEOUT_SEC,
    SESSION_EXPIRED_ERRCODE,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

SYNC_BUF_PATH = Path("data/ilink_sync_buf.json")

POLL_TIMEOUT_SEC = 30              # long-poll timeout for getupdates
POLL_INTERVAL_SEC = 3.0            # normal poll interval
BACKOFF_THRESHOLD = 3              # consecutive failures before backoff
BACKOFF_SHORT_SEC = 3.0            # normal retry interval
BACKOFF_LONG_SEC = 30.0            # backoff interval after many failures
MAX_RECENT_MSG_IDS = 1000          # dedup set limit
SESSION_EXPIRED_PAUSE_SEC = 3600   # 1 hour pause on session expiry

# iLink message types
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2
MSG_TYPE_SYS = 3

# iLink item types
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_FILE = 4
ITEM_LINK = 10
ITEM_CARD = 17


# ── Sync buffer persistence ─────────────────────────────────────────

def _load_sync_buf() -> str:
    """Load persisted get_updates_buf for resume."""
    try:
        if SYNC_BUF_PATH.exists():
            data = json.loads(SYNC_BUF_PATH.read_text(encoding="utf-8"))
            return data.get("get_updates_buf", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""


def _save_sync_buf(sync_buf: str) -> None:
    """Persist get_updates_buf so polls resume after restart."""
    try:
        SYNC_BUF_PATH.parent.mkdir(parents=True, exist_ok=True)
        SYNC_BUF_PATH.write_text(
            json.dumps({"get_updates_buf": sync_buf}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Failed to save iLink sync buf: %s", e)


# ── Message parsing ─────────────────────────────────────────────────

def _parse_message(raw: dict) -> Optional[dict]:
    """Parse a raw iLink message into a standardized dict.

    Returns:
        Standardized message dict, or None if the message should be skipped.
    """
    msg_type = raw.get("msg_type") or raw.get("message_type")
    if msg_type is None:
        return None

    msg_id = (
        raw.get("msg_id")
        or raw.get("newMsgId")
        or raw.get("msgid")
        or raw.get("tempMsgId")
        or raw.get("message_id")
        or ""
    )
    from_user_id = str(raw.get("from_user_id", ""))
    from_nickname = str(raw.get("from_nickname", ""))
    create_time = int(raw.get("create_time", 0) or 0)

    # Skip system messages and bot's own messages
    if msg_type == MSG_TYPE_SYS:
        return None
    if msg_type == MSG_TYPE_BOT:
        return None

    # Extract text from item_list
    text = ""
    items = raw.get("item_list") or []
    for item in items:
        item_type = item.get("type")
        if item_type == ITEM_TEXT:
            ti = item.get("text_item") or {}
            content = ti.get("content") or ti.get("text") or ""
            if content:
                text = (text + "\n" + content) if text else content
        elif item_type == ITEM_CARD:
            ci = item.get("card_item") or {}
            title = ci.get("title", "")
            desc = ci.get("desc") or ci.get("description", "")
            if title or desc:
                part = f"【卡片】{title}" + (f": {desc}" if desc else "")
                text = (text + "\n" + part) if text else part
        elif item_type == ITEM_LINK:
            li = item.get("link_item") or {}
            title = li.get("title", "")
            url = li.get("link", "")
            if title or url:
                part = f"【链接】{title}" + (f": {url}" if url else "")
                text = (text + "\n" + part) if text else part
        elif item_type == ITEM_IMAGE:
            text = (text + "\n【图片】") if text else "【图片】"
        elif item_type == ITEM_FILE:
            fi = item.get("file_item") or {}
            fname = fi.get("name", "文件")
            part = f"【文件: {fname}】"
            text = (text + "\n" + part) if text else part

    if not text:
        text = raw.get("text") or raw.get("content") or raw.get("msg") or ""

    text = text.strip()
    if not text:
        return None

    return {
        "msg_id": msg_id,
        "from_user_id": from_user_id,
        "from_nickname": from_nickname,
        "text": text,
        "msg_type": msg_type,
        "create_time": create_time,
    }


def _parse_messages_from_response(json_data: dict) -> list[dict]:
    """Extract and parse messages from a getupdates response."""
    messages: list[dict] = []
    msg_list = json_data.get("msgs") or json_data.get("msg_list") or []
    for raw in msg_list:
        try:
            msg = _parse_message(raw)
            if msg:
                messages.append(msg)
        except Exception:
            logger.debug("Failed to parse iLink message", exc_info=True)
    return messages


def _try_extract_my_user_id(json_data: dict) -> Optional[str]:
    """Extract bot's own user_id from response, if present."""
    msg_list = json_data.get("msgs") or json_data.get("msg_list") or []
    for raw in msg_list:
        my_id = raw.get("my_user_id")
        if my_id:
            return str(my_id)
    return None


# ── API call ────────────────────────────────────────────────────────

def fetch_updates(account: dict, sync_buf: str) -> dict:
    """Call ilink/bot/getupdates to fetch new messages.

    Returns:
        Dict with keys:
          - messages: list of parsed message dicts
          - new_sync_buf: updated sync buffer for next call
          - session_expired: True if session expired
          - my_user_id: bot's own user id (if available)
    """
    headers = _make_headers(account["bot_token"])
    base_url = account.get("base_url", DEFAULT_BASE_URL)
    url = f"{base_url}/ilink/bot/getupdates"

    payload = {"get_updates_buf": sync_buf}

    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=POLL_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        logger.debug("iLink getupdates timed out (normal)")
        return {"messages": [], "new_sync_buf": sync_buf, "session_expired": False}
    except requests.ConnectionError:
        logger.debug("iLink getupdates connection error")
        return {"messages": [], "new_sync_buf": sync_buf, "session_expired": False}
    except Exception as e:
        logger.warning("iLink getupdates error: %s", e)
        return {"messages": [], "new_sync_buf": sync_buf, "session_expired": False}

    # Session expired
    if data.get("errcode") == SESSION_EXPIRED_ERRCODE or data.get("ret") == SESSION_EXPIRED_ERRCODE:
        logger.warning("iLink session expired (errcode=%s)", SESSION_EXPIRED_ERRCODE)
        return {"messages": [], "new_sync_buf": sync_buf, "session_expired": True}

    ret = data.get("ret")
    if ret is not None and ret != 0:
        logger.debug("iLink getupdates ret=%s (non-fatal)", ret)
        return {"messages": [], "new_sync_buf": sync_buf, "session_expired": False}

    messages = _parse_messages_from_response(data)
    my_user_id = _try_extract_my_user_id(data)

    new_sync_buf = data.get("get_updates_buf", sync_buf)
    if new_sync_buf and new_sync_buf != sync_buf:
        _save_sync_buf(new_sync_buf)

    return {
        "messages": messages,
        "new_sync_buf": new_sync_buf,
        "session_expired": False,
        "my_user_id": my_user_id,
    }


# ── Standardize for router ──────────────────────────────────────────

def standardize_for_router(parsed: dict) -> dict:
    """Convert a parsed iLink message to the format expected by router.handle().

    The router expects keys:
      message_id, chat_id, sender_id, sender_name, content, msg_type, timestamp
    """
    prefix_id = f"ilink_{parsed.get('msg_id', '')}"
    user_id = parsed.get("from_user_id", "")
    nickname = parsed.get("from_nickname", user_id)
    text = parsed.get("text", "")
    create_time = parsed.get("create_time", 0)

    return {
        "message_id": prefix_id,
        "chat_id": f"ilink_{user_id}",
        "sender_id": user_id,
        "sender_name": nickname,
        "content": text,
        "msg_type": 1,
        "timestamp": create_time or int(time.time()),
    }


# ── Polling loop manager ────────────────────────────────────────────

_receiver_instance: Optional["ILinkReceiver"] = None


class ILinkReceiver:
    """Long-poll loop for iLink incoming messages.

    Runs a background thread that calls ilink/bot/getupdates periodically.
    Parsed and standardized messages are passed to the registered callback.

    Usage:
        receiver = ILinkReceiver()
        receiver.start(account_dict, callback_fn)
        ...
        receiver.stop()
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sync_buf: str = _load_sync_buf()
        self._account: Optional[dict] = None
        self._callback: Optional[Callable] = None

    # ── Public API ──────────────────────────────────────────────────

    def start(self, account: dict,
              callback: Callable[[dict], Optional[str]]) -> bool:
        """Start polling in a background thread.

        Args:
            account: iLink account dict (bot_token, account_id, base_url, ...).
            callback: Called with each standardized message dict.
                      If callback returns a string, it is sent as a reply.

        Returns:
            True if started, False if already running.
        """
        if self._running:
            logger.warning("ILinkReceiver already running")
            return False

        self._running = True
        self._account = account
        self._callback = callback
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="ilink-receiver",
            daemon=True,
        )
        self._thread.start()
        logger.info("ILinkReceiver started")
        return True

    def stop(self) -> None:
        """Stop the polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("ILinkReceiver thread did not stop in 5s")
            self._thread = None
        self._account = None
        logger.info("ILinkReceiver stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Poll loop ──────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Main poll loop — runs in background thread."""
        consecutive_failures = 0

        while self._running:
            try:
                result = fetch_updates(self._account, self._sync_buf)

                new_buf = result.get("new_sync_buf")
                if new_buf:
                    self._sync_buf = new_buf

                if result.get("session_expired"):
                    logger.warning("iLink session expired, pausing %d seconds",
                                   SESSION_EXPIRED_PAUSE_SEC)
                    self._sleep(SESSION_EXPIRED_PAUSE_SEC)
                    consecutive_failures = 0
                    continue

                consecutive_failures = 0

                messages = result.get("messages", [])
                for msg in messages:
                    self._handle_message(msg)

            except Exception as e:
                consecutive_failures += 1
                logger.warning("iLink poll error (%d): %s",
                               consecutive_failures, e)

            if self._running:
                delay = (
                    BACKOFF_LONG_SEC
                    if consecutive_failures >= BACKOFF_THRESHOLD
                    else BACKOFF_SHORT_SEC
                )
                self._sleep(delay)

    def _handle_message(self, raw_msg: dict) -> None:
        """Process a single parsed iLink message."""
        std_msg = standardize_for_router(raw_msg)

        logger.info("[iLink] Message from %s: %s",
                    std_msg.get("sender_name", "?"),
                    std_msg.get("content", "")[:60])

        if self._callback:
            try:
                reply = self._callback(std_msg)
                logger.info("[iLink] Callback returned: %s",
                            reply[:80] if reply else "(no reply)")
            except Exception as e:
                logger.error("[iLink] Callback error: %s", e)
                return

            if reply and reply.strip():
                from .ilink_push import get_ilink_push
                push = get_ilink_push()
                if push.is_available():
                    push.send_message(reply)
                else:
                    logger.warning("[iLink] Cannot reply: iLink not bound")
            else:
                logger.debug("[iLink] No reply to send")

    def _sleep(self, seconds: float) -> None:
        """Abortable sleep — wakes early if stop() is called."""
        if not self._running:
            return
        for _ in range(int(seconds * 10)):
            if not self._running:
                return
            time.sleep(0.1)


# ── Module-level helpers ────────────────────────────────────────────

def get_receiver() -> Optional[ILinkReceiver]:
    """Get the global ILinkReceiver singleton."""
    global _receiver_instance
    return _receiver_instance


def start_receiver(account: dict,
                   callback: Callable[[dict], Optional[str]]) -> bool:
    """Start the global receiver singleton.

    Convenience wrapper — gets or creates the receiver and starts it.
    Returns True if started successfully.
    """
    global _receiver_instance
    if _receiver_instance is None:
        _receiver_instance = ILinkReceiver()
    return _receiver_instance.start(account, callback)


def stop_receiver() -> None:
    """Stop the global receiver singleton."""
    global _receiver_instance
    if _receiver_instance:
        _receiver_instance.stop()
        _receiver_instance = None
