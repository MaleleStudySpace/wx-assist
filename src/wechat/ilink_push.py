"""iLink Bot API push channel — send digest/notifications to WeChat.

Ported from wechat-claude-skill (TypeScript) to Python.
Implements the iLink Bot API for:
  - QR code login (get_qrcode + check_qrcode_status)
  - Sending text messages (send_message with rate limiting + retry)
  - Account management (save/load/unbind)

Key implementation details:
  - X-WECHAT-UIN must be fresh random base64 per request (cached → ret=-2)
  - context_token is NOT required for sending (verified by wechat-claude-skill)
  - Per-user rate limit: 2500ms between sends
  - Retry: 3 attempts, exponential backoff 3s→6s→12s on ret=-2 (rate limit)
  - Session expired: errcode=-14 → pause 1 hour
  - Message truncation: 4000 chars max
"""

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_status_change_callback = None


def set_status_change_callback(callback) -> None:
    """Register a callback called when push health changes.

    The callback receives (ok: bool, error: str). It is best-effort and
    failures are swallowed so push delivery is not affected by UI updates.
    """
    global _status_change_callback
    _status_change_callback = callback

# ── Constants ────────────────────────────────────────────────────────

ACCOUNT_PATH = Path("data/ilink_account.json")
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"

API_TIMEOUT_SEC = 15
MIN_SEND_INTERVAL_SEC = 2.5
SEND_MAX_RETRIES = 3
SEND_RETRY_DELAYS = [3.0, 6.0, 12.0]  # exponential backoff
MAX_MSG_LEN = 4000

SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_RET = -2

# iLink message types
MSG_TYPE_BOT = 2
MSG_STATE_FINISH = 2
ITEM_TEXT = 1


# ── Message splitting (ported from wechat-claude-code send.ts) ──────

def _find_safe_split(text: str, max_len: int) -> int:
    """Find a safe split point that won't break formatting.

    Priority: newline > sentence punctuation > space > hard cut.
    """
    # Try newline first (preserves list items, paragraphs)
    idx = text.rfind('\n', 0, max_len)
    if idx >= max_len * 0.3:
        return idx
    # Try sentence-ending punctuation
    for i in range(max_len, int(max_len * 0.5) - 1, -1):
        if i <= len(text) and text[i - 1] in '。！？.!?\n':
            return i
    # Try space (won't split mid-word)
    idx = text.rfind(' ', 0, max_len)
    if idx >= max_len * 0.3:
        return idx
    # Hard cut
    return max_len


def _split_by_newline(text: str, max_len: int) -> list[str]:
    """Split a single oversized block at safe boundaries."""
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split = _find_safe_split(remaining, max_len)
        chunks.append(remaining[:split])
        remaining = remaining[split:].lstrip('\n')
    return chunks


def split_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split long text into chunks at paragraph boundaries.

    Ported from wechat-claude-code's splitMessage().
    Splits at double-newlines to keep logical blocks intact,
    falls back to single-newline splitting for oversized blocks.
    """
    if len(text) <= max_len:
        return [text]

    blocks = text.split('\n\n')
    chunks = []
    current = ''

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if not current:
            if len(block) <= max_len:
                current = block
            else:
                # Single oversized block — split further
                chunks.extend(_split_by_newline(block, max_len))
        elif len(current) + 2 + len(block) <= max_len:
            current += '\n\n' + block
        else:
            chunks.append(current)
            if len(block) <= max_len:
                current = block
            else:
                chunks.extend(_split_by_newline(block, max_len))
                current = ''

    if current:
        chunks.append(current)

    return chunks


# ── Utilities ────────────────────────────────────────────────────────

def _generate_uin() -> str:
    """Generate a fresh random base64 UIN for X-WECHAT-UIN header."""
    return base64.b64encode(os.urandom(4)).decode("ascii")


def _generate_client_id() -> str:
    """Generate a unique client_id for message sending."""
    import random
    return f"wcc-{int(time.time() * 1000)}-{random.randint(0, 99999)}"


def _truncate(text: str, max_len: int = MAX_MSG_LEN) -> str:
    """Truncate text to max_len with ellipsis indicator."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 20] + "\n\n...(已截断)"


def _make_headers(bot_token: str) -> dict:
    """Build iLink API request headers."""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN",
        "Origin": DEFAULT_BASE_URL,
        "Referer": f"{DEFAULT_BASE_URL}/",
        "Authorization": f"Bearer {bot_token}",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _generate_uin(),
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": "131584",  # (2<<16)|(2<<8)|0
    }


# ── Account persistence ─────────────────────────────────────────────

def _load_account() -> Optional[dict]:
    """Load ilink account from data/ilink_account.json."""
    if not ACCOUNT_PATH.exists():
        return None
    try:
        data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        if data.get("bot_token") and data.get("account_id") and data.get("user_id"):
            return data
        return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load ilink account: %s", e)
        return None


def _save_account(bot_token: str, account_id: str, base_url: str, user_id: str) -> None:
    """Save ilink account to data/ilink_account.json."""
    ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "bot_token": bot_token,
        "account_id": account_id,
        "base_url": base_url or DEFAULT_BASE_URL,
        "user_id": user_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
    }
    tmp = ACCOUNT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ACCOUNT_PATH)
    logger.info("iLink account saved to %s", ACCOUNT_PATH)


def _delete_account() -> None:
    """Delete ilink account file (unbind)."""
    try:
        if ACCOUNT_PATH.exists():
            ACCOUNT_PATH.unlink()
            logger.info("iLink account deleted (unbound)")
    except OSError as e:
        logger.warning("Failed to delete ilink account: %s", e)


# ── Main class ──────────────────────────────────────────────────────

class ILinkPush:
    """iLink Bot API push channel — send notifications to WeChat."""

    def __init__(self):
        self._account = _load_account()
        self._last_send_time = 0.0  # rate limiter
        self._last_push_ok = True
        self._last_error = ""

    # ── Public: availability ──────────────────────────────────────

    def _mark_push_success(self) -> None:
        """Mark iLink channel healthy after a successful send."""
        self._last_push_ok = True
        self._last_error = ""
        if _status_change_callback:
            try:
                _status_change_callback(True, "")
            except Exception:
                pass

    def _mark_push_failure(self, error: str) -> None:
        """Mark iLink channel unhealthy after a failed send."""
        self._last_push_ok = False
        self._last_error = error or "推送失败"
        if _status_change_callback:
            try:
                _status_change_callback(False, self._last_error)
            except Exception:
                pass

    def is_available(self) -> bool:
        """Check if iLink account is bound (can attempt to push).

        This only checks account binding, NOT whether the last push succeeded.
        Callers should always try send_message() — the result tells success/failure.
        """
        return self._account is not None

    def is_healthy(self) -> bool:
        """Check if iLink channel is bound AND last push succeeded.

        Used by Dashboard to show '已连接/未连接' status.
        """
        return self._account is not None and self._last_push_ok

    def get_status(self) -> dict:
        """Return binding status info with push error."""
        if not self._account:
            return {"bound": False}
        return {
            "bound": True,
            "account_id": self._account.get("account_id", ""),
            "user_id": self._account.get("user_id", ""),
            "base_url": self._account.get("base_url", DEFAULT_BASE_URL),
            "created_at": self._account.get("created_at", ""),
            "push_ok": self._last_push_ok,
            "push_error": self._last_error,
        }

    # ── Public: QR login ──────────────────────────────────────────

    def get_qrcode(self) -> dict:
        """Request a QR code for login.

        Returns: {"ok": True, "qrcode_url": str, "qrcode_id": str}
                 or {"ok": False, "error": str}
        """
        url = f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3"
        try:
            import requests
            resp = requests.get(url, timeout=API_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()

            if data.get("ret") != 0 or not data.get("qrcode_img_content") or not data.get("qrcode"):
                return {"ok": False, "error": f"QR code request failed (ret={data.get('ret')})"}

            return {
                "ok": True,
                "qrcode_url": data["qrcode_img_content"],
                "qrcode_id": data["qrcode"],
            }
        except Exception as e:
            logger.error("get_qrcode failed: %s", e)
            # Convert raw network exceptions to user-friendly messages
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                return {"ok": False, "error": "iLink 服务连接超时，请稍后重试"}
            if "connection" in str(e).lower():
                return {"ok": False, "error": "无法连接 iLink 服务，请检查网络"}
            return {"ok": False, "error": f"获取二维码失败: {e}"}

    def check_qrcode_status(self, qrcode_id: str) -> dict:
        """Check QR code scan status.

        Returns: {"status": "wait"|"scaned"|"confirmed"|"expired"|"error",
                  "bot_token"?, "account_id"?, "base_url"?, "user_id"?}
        """
        url = f"{DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
        try:
            import requests
            resp = requests.get(url, timeout=API_TIMEOUT_SEC)
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "")

            if status == "confirmed":
                bot_token = data.get("bot_token", "")
                account_id = data.get("ilink_bot_id", "")
                base_url = data.get("baseurl", DEFAULT_BASE_URL)
                user_id = data.get("ilink_user_id", "")

                if not bot_token or not account_id or not user_id:
                    return {"status": "error", "error": "QR confirmed but missing fields"}

                # Auto-save the account
                _save_account(bot_token, account_id, base_url, user_id)
                self._account = {
                    "bot_token": bot_token,
                    "account_id": account_id,
                    "base_url": base_url,
                    "user_id": user_id,
                }

                return {
                    "status": "confirmed",
                    "bot_token": bot_token,
                    "account_id": account_id,
                    "base_url": base_url,
                    "user_id": user_id,
                }

            if status in ("wait", "scaned"):
                return {"status": status}

            if status == "expired":
                return {"status": "expired"}

            # Other statuses (not_support, forbid, reject)
            return {"status": "error", "error": data.get("retmsg", status), "code": status}

        except Exception as e:
            logger.error("check_qrcode_status failed: %s", e)
            # Convert raw network exceptions to user-friendly messages
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                return {"status": "error", "error": "iLink 服务连接超时，请稍后重试", "code": "timeout"}
            if "connection" in str(e).lower():
                return {"status": "error", "error": "无法连接 iLink 服务，请检查网络", "code": "connection"}
            return {"status": "error", "error": f"查询扫码状态失败", "code": "unknown"}

    # ── Public: account management ────────────────────────────────

    def bind(self, bot_token: str, account_id: str, base_url: str, user_id: str) -> None:
        """Save bound account info."""
        _save_account(bot_token, account_id, base_url, user_id)
        self._account = {
            "bot_token": bot_token,
            "account_id": account_id,
            "base_url": base_url,
            "user_id": user_id,
        }
        # New binding → channel should be healthy
        self._mark_push_success()

    def unbind(self) -> None:
        """Remove bound account."""
        _delete_account()
        self._account = None
        self._mark_push_failure("未绑定")

    # ── Public: send message ──────────────────────────────────────

    def send_message(self, text: str, progress_callback=None, max_retries: int = None) -> dict:
        """Send a text message to the bound WeChat user.

        Auto-splits long messages into multiple chunks (≤4000 chars each)
        at paragraph boundaries. Ported from wechat-claude-code's splitMessage.

        Args:
            text: Message content to send.
            progress_callback: Optional callback for progress updates.
            max_retries: Max retry attempts. None = use default SEND_MAX_RETRIES.
                         For test-push, pass 0 to disable retries.

        Returns:
            {"success": bool, "error": str|None}
        """
        if max_retries is None:
            max_retries = SEND_MAX_RETRIES

        if not self._account:
            return {"success": False, "error": "iLink not bound"}

        if not text or not text.strip():
            return {"success": False, "error": "text is empty"}

        chunks = split_message(text.strip())
        if len(chunks) > 1:
            logger.info("iLink message split into %d chunks (total %d chars)", len(chunks), len(text))

        for i, chunk in enumerate(chunks):
            # Add progress prefix for multi-chunk messages
            if len(chunks) > 1:
                chunk = f"[{i + 1}/{len(chunks)}]\n{chunk}"

            result = self._send_chunk(chunk, progress_callback, max_retries)
            if not result.get("success"):
                self._mark_push_failure(result.get("error", ""))
                return result

        self._mark_push_success()
        return {"success": True}

    def _send_chunk(self, text: str, progress_callback=None, max_retries: int = SEND_MAX_RETRIES) -> dict:
        """Send a single text chunk with rate limiting and retry."""

        # Rate limiting: ensure MIN_SEND_INTERVAL_SEC between sends
        now = time.monotonic()
        wait = self._last_send_time + MIN_SEND_INTERVAL_SEC - now
        if wait > 0:
            time.sleep(wait)

        # Build message payload
        message = {
            "from_user_id": self._account["account_id"],
            "to_user_id": self._account["user_id"],
            "client_id": _generate_client_id(),
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }

        payload = {
            "msg": message,
            "base_info": {"channel_version": "2.2.0"},
        }

        # Retry with exponential backoff on ret=-2 (rate limit)
        for attempt in range(max_retries + 1):
            try:
                import requests
                headers = _make_headers(self._account["bot_token"])
                base_url = self._account.get("base_url", DEFAULT_BASE_URL)
                url = f"{base_url}/ilink/bot/sendmessage"

                resp = requests.post(url, json=payload, headers=headers, timeout=API_TIMEOUT_SEC)
                resp.raise_for_status()
                # iLink sendmessage 成功返回空字符串，失败返回 JSON
                # 所以先检查空响应（成功情况）
                if not resp.text or not resp.text.strip():
                    self._last_send_time = time.monotonic()
                    logger.info("iLink message sent successfully (%d chars) [empty response]", len(text))
                    return {"success": True}

                data = resp.json()
                logger.info("iLink sendmessage response (non-empty): %s", data)

                ret = data.get("ret")
                errcode = data.get("errcode")
                errmsg = data.get("errmsg")

                # Success: ret=0 or empty body with no error code
                # iLink API 返回空 {} 也算成功（已通过实测确认）
                if (ret is None or ret == 0) and (errcode is None or errcode == 0):
                    self._last_send_time = time.monotonic()
                    logger.info("iLink message sent successfully (%d chars)", len(text))
                    return {"success": True}

                # Session expired (errcode=-14)
                if errcode == SESSION_EXPIRED_ERRCODE:
                    self._last_send_time = time.monotonic()
                    return {"success": False, "error": f"session_expired: errcode={errcode}"}

                # Rate limited: retry with backoff
                if ret == RATE_LIMIT_RET:
                    if attempt < max_retries:
                        delay = SEND_RETRY_DELAYS[attempt] if attempt < len(SEND_RETRY_DELAYS) else 12.0
                        logger.warning("iLink rate limited, retry %d/%d in %.1fs", attempt + 1, max_retries, delay)
                        if progress_callback:
                            progress_callback(attempt + 1, max_retries, delay, f"请求超时，{delay:.0f}秒后第{attempt+1}次重试")
                        time.sleep(delay)
                        continue
                    self._last_send_time = time.monotonic()
                    return {"success": False, "error": f"rate-limited after {max_retries} retries"}

                # Other error
                self._last_send_time = time.monotonic()
                return {"success": False, "error": f"ret={ret} errcode={errcode} errmsg={errmsg}"}

            except Exception as e:
                if attempt < max_retries:
                    delay = SEND_RETRY_DELAYS[attempt] if attempt < len(SEND_RETRY_DELAYS) else 12.0
                    logger.warning("iLink send error, retry %d/%d in %.1fs: %s", attempt + 1, max_retries, delay, e)
                    if progress_callback:
                        progress_callback(attempt + 1, max_retries, delay, str(e))
                    time.sleep(delay)
                    continue
                self._last_send_time = time.monotonic()
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "exhausted retries"}

    # ── Public: reload account ────────────────────────────────────

    def reload(self) -> None:
        """Reload account from disk (useful after external bind)."""
        self._account = _load_account()


# ── Singleton ───────────────────────────────────────────────────────

_ilink_instance: Optional[ILinkPush] = None


def get_ilink_push() -> ILinkPush:
    """Get the global ILinkPush singleton."""
    global _ilink_instance
    if _ilink_instance is None:
        _ilink_instance = ILinkPush()
    return _ilink_instance


def reset_ilink_push() -> None:
    """Reset the singleton (for testing or after unbind)."""
    global _ilink_instance
    _ilink_instance = None


# ── Formatting helper ───────────────────────────────────────────────

def _wrap_urls_for_wechat(text: str) -> str:
    """Post-process text to make URLs more clickable in WeChat Desktop.

    WeChat's URL parser often fails on long URLs with query parameters
    (e.g. mp.weixin.qq.com URLs with __biz, mid, sn, chksm, etc.),
    only making the base domain path clickable.

    Strategy:
    - Wrap URLs in angle brackets <URL> — WeChat recognises this as
      an explicit URL boundary
    - Put mp.weixin.qq.com URLs on their own line with blank lines
      above/below for maximum parser signal
    - Other URLs just get angle brackets without line breaks
    - Strip trailing Chinese punctuation that's not part of the URL
    """
    import re

    # Chinese punctuation that may trail a URL but isn't part of it
    _TRAILING_PUNCT = set('。，、；：！？）】》"\'…')

    def _clean_url(url: str) -> str:
        while url and url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
        return url

    # Pass 1: Wrap mp.weixin.qq.com URLs (most problematic) with line breaks
    def _replace_wx(m):
        url = _clean_url(m.group(1))
        return f'\n\n<{url}>\n\n'

    result = re.sub(r'(https?://mp\.weixin\.qq\.com/\S+)', _replace_wx, text)

    # Pass 2: Wrap remaining URLs with angle brackets only (no line breaks)
    # Skip URLs already wrapped in angle brackets
    def _replace_general(m):
        url = _clean_url(m.group(1))
        return f'<{url}>'

    result = re.sub(
        r'(?<!<)(https?://(?!mp\.weixin\.qq\.com)\S+)',
        _replace_general,
        result,
    )

    # Clean up excessive blank lines (max 2 consecutive newlines)
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


def format_for_wechat(title: str, content: str) -> str:
    """Format digest content for WeChat push (wrap URLs for clickability).

    Unlike _truncate or split_message, this does NOT truncate — the caller
    (send_message) handles chunking via split_message() at paragraph
    boundaries, with [1/N] prefixes for multi-chunk sends.  Modern WeChat
    / iLink can handle up to 4000 chars per chunk, and split_message
    ensures each chunk stays within that limit naturally.
    """
    msg = f"{title}\n\n{content}"
    msg = _wrap_urls_for_wechat(msg)
    return msg
