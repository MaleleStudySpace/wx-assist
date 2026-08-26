"""QQ official Bot WebSocket Gateway adapter (text C2C and group only)."""

import json
import logging
import re
import threading
import time
from typing import Optional

import websocket

from ...base import BasePlatformAdapter, MessageCallback
from ...config_schema import PlatformConfig
from ...message import MessageType, NormalizedMessage, SessionSource
from ...plugins import register
from .openapi_client import QQOpenAPIClient

logger = logging.getLogger(__name__)

DEDUP_SECONDS = 300
MAX_MESSAGE_LENGTH = 4000
RECONNECT_BACKOFF = (2, 5, 10, 30, 60)


@register("qqbot")
class QQBotAdapter(BasePlatformAdapter):
    platform_name = "qqbot"
    display_name = "QQ"
    text_message_max_len = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig,
                 api_client: Optional[QQOpenAPIClient] = None):
        extra = config.extra or {}
        self._client = api_client or QQOpenAPIClient(
            str(extra.get("app_id", "")),
            str(extra.get("client_secret", "")),
        )
        self._callback: Optional[MessageCallback] = None
        self._stop_event = threading.Event()
        self._ws = None
        self._ws_thread: Optional[threading.Thread] = None
        self._heartbeat_interval = 30.0
        self._last_seq: Optional[int] = None
        self._session_id: Optional[str] = None
        self._connected = False
        self._last_provider_response = {}
        self._chat_type_map: dict[str, str] = {}
        self._seen_messages: dict[str, float] = {}
        self._state_lock = threading.RLock()

    def start(self, callback: MessageCallback,
              groups: list[str] | None = None) -> bool:
        if not self._client.app_id or not self._client.client_secret:
            logger.error("[qqbot] app_id/client_secret 未配置")
            return False
        logger.info("[qqbot] starting adapter: app_id=%s", self._client.app_id)
        self._callback = callback
        self._stop_event.clear()
        self._ws_thread = threading.Thread(
            target=self._ws_loop, name="qqbot-gateway", daemon=True)
        self._ws_thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._client.close()

    def send_text(self, chat_id: str, content: str,
                  *, reply_to: Optional[str] = None) -> bool:
        platform, native_id = self.parse(chat_id)
        if platform and platform != "qqbot":
            return False
        target = native_id
        chat_type = self._chat_type_map.get(chat_id, self._chat_type_map.get(target, "c2c"))
        path = (
            f"/v2/users/{target}/messages"
            if chat_type == "c2c"
            else f"/v2/groups/{target}/messages"
        )
        body = {
            "content": str(content)[:MAX_MESSAGE_LENGTH],
            "msg_type": 0,
            "msg_seq": int(time.time() * 1000) % 2_000_000_000,
        }
        if reply_to:
            body["msg_id"] = reply_to
        try:
            logger.info("[qqbot] send_text -> %s path=%s len=%d", target, path, len(content))
            resp = self._client.request("POST", path, body)
            self._last_provider_response = resp
            logger.info("[qqbot] send_text OK: %s", str(resp)[:200])
            return True
        except Exception as exc:
            logger.warning("[qqbot] send_text failed: %s", exc)
            return False

    def health_status(self) -> dict:
        thread = self._ws_thread
        return {"ok": self._connected,
                "detail": "已连接" if self._connected else "未连接"}

    def list_chats(self) -> list[SessionSource]:
        return []

    def _ws_loop(self) -> None:
        backoff_index = 0
        while not self._stop_event.is_set():
            try:
                logger.info("[qqbot] connecting to gateway...")
                url = self._client.get_gateway_url()
                logger.info("[qqbot] gateway url obtained, connecting ws...")
                ws = websocket.create_connection(url, timeout=20)
                self._ws = ws
                logger.info("[qqbot] ws connected, starting reader")
                self._ws_reader(ws)
                logger.info("[qqbot] ws reader exited")
                backoff_index = 0
            except Exception as exc:
                logger.warning("[qqbot] gateway disconnected: %s", exc)
            finally:
                self._connected = False
                self._ws = None
            if self._stop_event.is_set():
                break
            delay = RECONNECT_BACKOFF[min(backoff_index, len(RECONNECT_BACKOFF) - 1)]
            logger.info("[qqbot] reconnecting in %ss...", delay)
            if self._stop_event.wait(delay):
                break
            backoff_index += 1

    def _ws_reader(self, ws) -> None:
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(ws,), daemon=True)
        heartbeat.start()
        while not self._stop_event.is_set():
            try:
                ws.settimeout(1.0)
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                return
            if not raw:
                return
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("[qqbot] invalid gateway payload")
                continue
            seq = payload.get("s")
            if isinstance(seq, int):
                self._last_seq = seq
            op = payload.get("op")
            event = payload.get("t")
            data = payload.get("d") or {}
            if op == 10:
                self._heartbeat_interval = float(data.get("heartbeat_interval", 30000)) / 1000 * 0.8
                logger.info("[qqbot] received HELLO, heartbeat_interval=%.1fs", self._heartbeat_interval)
                if self._session_id and self._last_seq is not None:
                    self._send_resume(ws)
                else:
                    self._send_identify(ws)
            elif op == 11:
                continue
            elif op == 0 and event == "READY":
                self._connected = True
                self._session_id = str(data.get("session_id", ""))
                logger.info("[qqbot] READY! session_id=%s", self._session_id)
            elif op == 0 and event == "C2C_MESSAGE_CREATE":
                self._handle_c2c(data)
            elif op == 0 and event == "GROUP_AT_MESSAGE_CREATE":
                self._handle_group(data)
            elif op == 7:
                return
            elif op == 9:  # INVALID_SESSION：旧 session 失效，清状态后重新 Identify
                self._session_id = None
                self._last_seq = None
                self._send_identify(ws)

    def _send_identify(self, ws) -> None:
        token = self._client.ensure_token()
        ws.send(json.dumps({
            "op": 2,
            "d": {
                "token": f"QQBot {token}",
                "intents": (1 << 25) | (1 << 30),
                "shard": [0, 1],
                "properties": {"$os": "wx-assist", "$browser": "wx-assist", "$device": "wx-assist"},
            },
        }))

    def _send_resume(self, ws) -> None:
        token = self._client.ensure_token()
        ws.send(json.dumps({
            "op": 6,
            "d": {
                "token": f"QQBot {token}",
                "session_id": self._session_id,
                "seq": self._last_seq,
            },
        }))

    def _heartbeat_loop(self, ws) -> None:
        while not self._stop_event.wait(self._heartbeat_interval):
            try:
                ws.send(json.dumps({"op": 1, "d": self._last_seq}))
            except Exception:
                return

    def _handle_c2c(self, data: dict) -> None:
        message_id = str(data.get("id", ""))
        author = data.get("author") or {}
        openid = str(author.get("user_openid", ""))
        if not message_id or not openid or self._duplicate(message_id):
            return
        chat_id = self.namespace("qqbot", openid)
        self._chat_type_map[chat_id] = "c2c"
        self._deliver(NormalizedMessage(
            platform="qqbot", native_message_id=message_id,
            chat_id=chat_id, chat_type="dm",
            sender_id=self.namespace("qqbot", openid),
            sender_name=str(author.get("username", "") or openid),
            group_name=chat_id,
            content=str(data.get("content", "")).strip(),
            message_type=MessageType.TEXT,
            timestamp=self._parse_timestamp(data.get("timestamp")), raw=data,
        ))

    def _handle_group(self, data: dict) -> None:
        message_id = str(data.get("id", ""))
        author = data.get("author") or {}
        group_id = str(data.get("group_openid", ""))
        member_id = str(author.get("member_openid", ""))
        if not message_id or not group_id or not member_id or self._duplicate(message_id):
            return
        chat_id = self.namespace("qqbot", group_id)
        self._chat_type_map[chat_id] = "group"
        text = re.sub(r"^@\S+\s*", "", str(data.get("content", "")).strip())
        self._deliver(NormalizedMessage(
            platform="qqbot", native_message_id=message_id,
            chat_id=chat_id, chat_type="group",
            sender_id=self.namespace("qqbot", member_id),
            sender_name=str(author.get("username", "") or member_id),
            group_name=chat_id,
            content=text, message_type=MessageType.TEXT,
            timestamp=self._parse_timestamp(data.get("timestamp")), raw=data,
        ))

    def _deliver(self, message: NormalizedMessage) -> None:
        logger.info("[qqbot] _deliver called: callback=%s chat_id=%s content=%s",
                     "set" if self._callback else "NONE",
                     message.chat_id, message.content[:50])
        if not self._callback:
            return
        try:
            reply = self._callback(message)
            if reply and reply.strip():
                from ...delivery import DeliveryRequest, get_delivery_service
                logger.info("[qqbot] _deliver: reply len=%d, sending to %s", len(reply), message.chat_id)
                get_delivery_service().send_text(DeliveryRequest(
                    platform="qqbot", text=reply, target=message.chat_id,
                    source_type="agent_reply", source_id=message.native_message_id,
                    inbound_message_id=message.native_message_id,
                    conversation_key=message.chat_id, reply_to=message.native_message_id,
                ))
        except Exception:
            logger.exception("[qqbot] message callback failed")

    def _duplicate(self, message_id: str) -> bool:
        now = time.time()
        with self._state_lock:
            self._seen_messages = {
                key: stamp for key, stamp in self._seen_messages.items()
                if now - stamp < DEDUP_SECONDS
            }
            if message_id in self._seen_messages:
                return True
            self._seen_messages[message_id] = now
            return False

    @staticmethod
    def _parse_timestamp(value) -> int:
        try:
            return int(value) if value else int(time.time())
        except (TypeError, ValueError):
            return int(time.time())
