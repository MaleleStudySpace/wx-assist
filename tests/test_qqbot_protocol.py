from unittest.mock import patch

from src.im.config_schema import PlatformConfig
from src.im.plugins.qqbot.adapter import QQBotAdapter


def _adapter():
    class FakeClient:
        app_id = "a"
        client_secret = "s"

        def request(self, method, path, body=None):
            return {"id": "reply-1"}

        def close(self):
            pass

    return QQBotAdapter(
        PlatformConfig("qqbot", extra={"app_id": "a", "client_secret": "s"}),
        api_client=FakeClient(),
    )


def test_qq_adapter_delivers_legacy_dict_and_replies():
    adapter = _adapter()
    received = []
    deliveries = []

    class FakeDelivery:
        def send_text(self, request):
            deliveries.append(request)
            return {"success": True}

    adapter._callback = lambda message: received.append(message) or "收到"
    with patch("src.im.delivery.get_delivery_service", return_value=FakeDelivery()):
        adapter._handle_c2c({
            "id": "in-1", "timestamp": "1710000000", "content": "你好",
            "author": {"user_openid": "user-1", "username": "用户"},
        })
    assert received[0].chat_id == "qqbot:user-1"
    assert received[0].chat_type == "dm"
    assert deliveries[0].platform == "qqbot"
    assert deliveries[0].target == "qqbot:user-1"


def test_qq_adapter_deduplicates_same_message_id():
    adapter = _adapter()
    received = []
    adapter._callback = lambda message: received.append(message) or "收到"
    data = {
        "id": "same-id", "timestamp": "1710000000", "content": "你好",
        "author": {"user_openid": "user-1", "username": "用户"},
    }
    with patch("src.im.delivery.get_delivery_service", return_value=type("D", (), {
        "send_text": lambda self, request: {"success": True}
    })()):
        adapter._handle_c2c(data)
        adapter._handle_c2c(data)
    assert len(received) == 1
