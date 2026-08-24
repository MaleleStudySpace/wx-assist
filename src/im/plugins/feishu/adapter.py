"""Feishu platform adapter.

Inbound events are received by the existing WebUI HTTP server at
``/webhook/feishu`` and forwarded to this adapter's callback.  The adapter
owns credentials and outbound PushChannel, while the server only handles HTTP
transport and verification.
"""

import logging
import threading
import time
from typing import Callable, Optional

from ...base import BasePlatformAdapter, MessageCallback
from ...config_schema import PlatformConfig
from ...message import NormalizedMessage, SessionSource
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
        self.push_channel = FeishuPushChannel(self._client)
        self._callback: Optional[MessageCallback] = None
        self._running = False
        self._lock = threading.RLock()
        self._seen_events: dict[str, float] = {}

    def start(self, callback: MessageCallback,
              groups: list[str] | None = None) -> bool:
        if not self._client.app_id or not self._client.app_secret:
            logger.error("[feishu] app_id/app_secret 未配置")
            return False
        self._callback = callback
        self._running = True
        # HTTP transport is owned by web.server; no extra listener is started.
        return True

    def stop(self) -> None:
        self._running = False
        self._client.close()

    def send_text(self, chat_id: str, content: str,
                  *, reply_to: Optional[str] = None) -> bool:
        result = self.push_channel.send_message(content, target=chat_id)
        return bool(result.get("success"))

    def health_status(self) -> dict:
        return self.push_channel.get_status() if self._running else {
            "ok": False, "detail": "未启动"
        }

    def list_chats(self) -> list[SessionSource]:
        return []

    def handle_webhook(self, payload: dict):
        """Return challenge immediately and process events asynchronously."""
        parsed = verify_and_parse_event(payload, self.verification_token)
        if isinstance(parsed, dict):
            return parsed
        if not isinstance(parsed, NormalizedMessage) or not self._callback:
            return {"code": 0}
        event_id = str((payload.get("header") or {}).get("event_id", "")) or parsed.native_message_id
        now = time.time()
        with self._lock:
            self._seen_events = {
                key: stamp for key, stamp in self._seen_events.items()
                if now - stamp < 300
            }
            if event_id and event_id in self._seen_events:
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
            reply = self._callback(parsed) if self._callback else None
            if reply and parsed.chat_type == "dm":
                from ...delivery import DeliveryRequest, get_delivery_service
                get_delivery_service().send_text(DeliveryRequest(
                    platform="feishu", text=reply, target=parsed.chat_id,
                    source_type="agent_reply", source_id=parsed.native_message_id,
                    inbound_message_id=parsed.native_message_id,
                    conversation_key=parsed.chat_id, reply_to=parsed.native_message_id,
                ))
        except Exception:
            logger.exception("[feishu] event processing failed")
