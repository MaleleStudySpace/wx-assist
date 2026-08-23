"""Feishu event webhook parsing and verification."""

import json
import logging
import time
from typing import Callable, Optional

from ...message import MessageType, NormalizedMessage

logger = logging.getLogger(__name__)


def verify_and_parse_event(payload: dict, verification_token: str = ""):
    """Return challenge response or NormalizedMessage.

    Feishu URL verification is handled before message processing.  A mismatch
    is rejected; no event is delivered to the application callback.
    """
    if payload.get("type") == "url_verification":
        if verification_token and payload.get("token") != verification_token:
            raise ValueError("Feishu verification token mismatch")
        return {"challenge": payload.get("challenge", "")}

    header = payload.get("header") or {}
    if verification_token and header.get("token") != verification_token:
        raise ValueError("Feishu event token mismatch")
    if header.get("event_type") != "im.message.receive_v1":
        return None

    event = payload.get("event") or {}
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = (sender.get("sender_id") or {})
    sender_open_id = str(sender_id.get("open_id", ""))
    chat_id = str(message.get("chat_id", ""))
    if not chat_id or not sender_open_id:
        return None
    content = str(message.get("content", ""))
    try:
        content_data = json.loads(content)
        text = str(content_data.get("text", content))
    except (TypeError, ValueError):
        text = content
    chat_type = "group" if message.get("chat_type") == "group" else "dm"
    return NormalizedMessage(
        platform="feishu",
        native_message_id=str(message.get("message_id", "")),
        chat_id=f"feishu:{chat_id}",
        chat_type=chat_type,
        sender_id=f"feishu:{sender_open_id}",
        sender_name=sender_open_id,
        group_name=chat_id,
        content=text,
        message_type=MessageType.TEXT,
        timestamp=_parse_timestamp(message.get("create_time")),
        raw=payload,
    )


def _parse_timestamp(value) -> int:
    try:
        return int(value) // 1000 if int(value) > 10_000_000_000 else int(value)
    except (TypeError, ValueError):
        return int(time.time())
