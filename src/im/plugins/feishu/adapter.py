"""Feishu platform adapter.

Inbound events are received by the existing WebUI HTTP server at
``/webhook/feishu`` and forwarded to this adapter's callback.  The adapter
owns credentials and outbound PushChannel, while the server only handles HTTP
transport and verification.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Callable, Optional

from ...base import BasePlatformAdapter, MessageCallback
from ...config_schema import PlatformConfig
from ...message import MessageType, NormalizedMessage, SessionSource
from ...plugins import register
from .events import verify_and_parse_event
from .openapi_client import FeishuOpenAPIClient
from .push import FeishuPushChannel

logger = logging.getLogger(__name__)


@register("feishu")
class FeishuAdapter(BasePlatformAdapter):
    platform_name = "feishu"
    display_name = "飞书"
    text_message_max_len = 4000

    def __init__(self, config: PlatformConfig,
                 api_client: Optional[FeishuOpenAPIClient] = None):
        extra = config.extra or {}
        self.verification_token = str(extra.get("verification_token", ""))
        self._client = api_client or FeishuOpenAPIClient(
            str(extra.get("app_id", "")),
            str(extra.get("app_secret", "")),
        )
        self._config_extra = extra
        self.push_channel = FeishuPushChannel(self._client)
        self._callback: Optional[MessageCallback] = None
        self._running = False
        self._ws_client = None
        self._ws_thread = None
        self._ws_thread_loop = None
        self._ws_stop = threading.Event()
        self._ws_error = ""
        self._ws_reconnect_delay = 5.0
        self._lock = threading.RLock()
        self._seen_events: dict[str, float] = {}
        self._seen_ws_message_ids: dict[str, float] = {}

    def start(self, callback: MessageCallback,
              groups: list[str] | None = None) -> bool:
        if not self._client.app_id or not self._client.app_secret:
            logger.error("[feishu] app_id/app_secret 未配置")
            return False
        self._callback = callback
        self._running = True
        self._ws_stop.clear()
        self._ws_error = ""
        self._ws_reconnect_delay = 5.0
        self._ws_thread = threading.Thread(
            target=self._run_websocket,
            name="feishu-gateway",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("[feishu] official websocket connection thread started")
        return True

    def _run_websocket(self) -> None:
        """Run the official SDK client in a dedicated event loop."""
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as ws_client_module
            from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
            from lark_oapi.ws import Client as FeishuWSClient
        except ImportError as exc:
            self._ws_error = "lark-oapi is not installed"
            logger.error("[feishu] websocket unavailable: %s", exc)
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ws_client_module.loop = loop
        self._ws_thread_loop = loop
        try:
            while not self._ws_stop.is_set():
                try:
                    handler = EventDispatcherHandler.builder(
                        str(self._config_value("encrypt_key")),
                        self.verification_token,
                    ).register_p2_im_message_receive_v1(self._on_ws_message).build()
                    self._ws_client = FeishuWSClient(
                        app_id=self._client.app_id,
                        app_secret=self._client.app_secret,
                        log_level=lark.LogLevel.INFO,
                        event_handler=handler,
                        extra_ua_tags=["channel"],
                    )
                    logger.info("[feishu] websocket client starting (event=im.message.receive_v1)")
                    self._ws_client.start()
                    if self._ws_stop.is_set():
                        break
                    logger.warning("[feishu] websocket client stopped; reconnecting in %.1fs", self._ws_reconnect_delay)
                except Exception as exc:
                    self._ws_error = str(exc)
                    if self._ws_stop.is_set():
                        break
                    logger.exception("[feishu] websocket connection failed; reconnecting in %.1fs", self._ws_reconnect_delay)
                finally:
                    self._ws_client = None
                if self._ws_stop.wait(self._ws_reconnect_delay):
                    break
                self._ws_reconnect_delay = min(self._ws_reconnect_delay * 2, 60.0)
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._ws_thread_loop = None
            logger.info("[feishu] websocket connection thread exited")

    def _config_value(self, key: str) -> str:
        return str(self._config_extra.get(key, ""))

    def _on_ws_message(self, data) -> None:
        """Convert an SDK event object to the existing normalized pipeline."""
        try:
            event = getattr(data, "event", None)
            message = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "")
            if not message or not getattr(message, "message_id", None) or not getattr(message, "chat_id", None):
                logger.warning("[feishu] websocket event missing message fields")
                return
            message_id = str(message.message_id)
            now = time.time()
            with self._lock:
                self._seen_ws_message_ids = {
                    key: stamp for key, stamp in self._seen_ws_message_ids.items()
                    if now - stamp < 86400
                }
                if message_id in self._seen_ws_message_ids:
                    logger.info("[feishu] duplicate websocket message ignored: message_id=%s", message_id)
                    return
                self._seen_ws_message_ids[message_id] = now
            raw_content = str(getattr(message, "content", "") or "")
            try:
                content_data = json.loads(raw_content)
                content = str(content_data.get("text", raw_content)) if isinstance(content_data, dict) else raw_content
            except (TypeError, ValueError):
                content = raw_content
            chat_type = "group" if getattr(message, "chat_type", "") == "group" else "dm"
            normalized = NormalizedMessage(
                platform="feishu",
                native_message_id=str(message.message_id),
                chat_id=f"feishu:{message.chat_id}",
                chat_type=chat_type,
                sender_id=f"feishu:{sender_id}",
                sender_name=str(sender_id),
                group_name=str(message.chat_id),
                content=content.strip(),
                message_type=MessageType.TEXT,
                timestamp=self._parse_timestamp(getattr(message, "create_time", 0)),
                raw={"source": "websocket"},
            )
            logger.info("[feishu] websocket message received: message_id=%s chat=%s", normalized.native_message_id, normalized.chat_id)
            self._deliver(normalized)
        except Exception:
            logger.exception("[feishu] websocket event conversion failed")

    @staticmethod
    def _parse_timestamp(value) -> int:
        try:
            number = int(value or 0)
            return number // 1000 if number > 10_000_000_000 else number
        except (TypeError, ValueError):
            return int(time.time())

    def _deliver(self, message: NormalizedMessage) -> None:
        callback = self._callback
        if not callback:
            logger.warning("[feishu] websocket message dropped: callback is not configured")
            return
        try:
            reply = callback(message)
            logger.info("[feishu] websocket callback completed: message_id=%s reply=%s", message.native_message_id, bool(reply))
            if reply and message.chat_type == "dm":
                from ...delivery import DeliveryRequest, get_delivery_service
                result = get_delivery_service().send_text(DeliveryRequest(
                    platform="feishu", text=reply, target=message.chat_id,
                    source_type="agent_reply", source_id=message.native_message_id,
                    inbound_message_id=message.native_message_id,
                    conversation_key=message.chat_id,
                ))
                logger.info("[feishu] websocket agent reply delivery: message_id=%s success=%s", message.native_message_id, result.get("success"))
        except Exception:
            logger.exception("[feishu] websocket message callback failed: message_id=%s", message.native_message_id)

    def stop(self) -> None:
        self._running = False
        self._ws_stop.set()
        ws_client = self._ws_client
        if ws_client is not None:
            try:
                close = getattr(ws_client, "_disconnect", None)
                loop = self._ws_thread_loop
                if close and loop and not loop.is_closed():
                    import asyncio
                    asyncio.run_coroutine_threadsafe(close(), loop)
            except Exception as exc:
                logger.debug("[feishu] websocket close request failed: %s", exc)
        thread = self._ws_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)
        self._ws_thread = None
        self._client.close()

    def send_text(self, chat_id: str, content: str,
                  *, reply_to: Optional[str] = None) -> bool:
        result = self.push_channel.send_message(content, target=chat_id)
        return bool(result.get("success"))

    def health_status(self) -> dict:
        if not self._running:
            return {"ok": False, "detail": "未启动"}
        if self._ws_error:
            return {"ok": False, "detail": self._ws_error}
        return {"ok": True, "detail": "长连接运行中"}

    def list_chats(self) -> list[SessionSource]:
        return []

    def handle_webhook(self, payload: dict):
        """Return challenge immediately and process events asynchronously."""
        header = payload.get("header") or {}
        logger.info(
            "[feishu] webhook dispatch: event_type=%s event_id=%s callback=%s",
            header.get("event_type", payload.get("type", "")),
            header.get("event_id", ""),
            "set" if self._callback else "NONE",
        )
        parsed = verify_and_parse_event(payload, self.verification_token)
        if isinstance(parsed, dict):
            logger.info("[feishu] webhook challenge acknowledged")
            return parsed
        if not isinstance(parsed, NormalizedMessage):
            logger.info("[feishu] webhook produced no message")
            return {"code": 0}
        if not self._callback:
            logger.warning("[feishu] message received but callback is not configured")
            return {"code": 0}
        logger.info(
            "[feishu] message accepted: message_id=%s chat=%s chat_type=%s",
            parsed.native_message_id, parsed.chat_id, parsed.chat_type,
        )
        event_id = str((payload.get("header") or {}).get("event_id", "")) or parsed.native_message_id
        now = time.time()
        with self._lock:
            self._seen_events = {
                key: stamp for key, stamp in self._seen_events.items()
                if now - stamp < 300
            }
            if event_id and event_id in self._seen_events:
                logger.info("[feishu] duplicate event ignored: event_id=%s", event_id)
                return {"code": 0}
            if event_id:
                self._seen_events[event_id] = now
        threading.Thread(
            target=self._process_event,
            args=(parsed,),
            name="feishu-event",
            daemon=True,
        ).start()
        return {"code": 0}

    def _process_event(self, parsed: NormalizedMessage) -> None:
        try:
            logger.info("[feishu] processing message_id=%s", parsed.native_message_id)
            reply = self._callback(parsed) if self._callback else None
            logger.info(
                "[feishu] callback completed: message_id=%s reply=%s len=%d",
                parsed.native_message_id, "yes" if reply else "no", len(reply or ""),
            )
            if reply and parsed.chat_type == "dm":
                from ...delivery import DeliveryRequest, get_delivery_service
                result = get_delivery_service().send_text(DeliveryRequest(
                    platform="feishu", text=reply, target=parsed.chat_id,
                    source_type="agent_reply", source_id=parsed.native_message_id,
                    inbound_message_id=parsed.native_message_id,
                    conversation_key=parsed.chat_id, reply_to=parsed.native_message_id,
                ))
                logger.info("[feishu] agent reply delivery: message_id=%s success=%s result=%s",
                            parsed.native_message_id, result.get("success"), str(result)[:300])
            elif reply:
                logger.info("[feishu] reply suppressed for non-DM chat_type=%s", parsed.chat_type)
        except Exception:
            logger.exception("[feishu] event processing failed: message_id=%s", parsed.native_message_id)
