from src.im.config_schema import PlatformConfig
from src.im.plugins.qqbot.adapter import QQBotAdapter


def test_qq_adapter_delivers_legacy_dict_and_replies():
    class FakeClient:
        app_id = "a"
        client_secret = "s"

        def request(self, method, path, body=None):
            self.last = (method, path, body)
            return {"id": "reply-1"}

        def close(self):
            pass

    client = FakeClient()
    adapter = QQBotAdapter(
        PlatformConfig("qqbot", extra={"app_id": "a", "client_secret": "s"}),
        api_client=client,
    )
    received = []
    adapter._callback = lambda message: received.append(message) or "收到"
    adapter._handle_c2c({
        "id": "in-1",
        "timestamp": "1710000000",
        "content": "你好",
        "author": {"user_openid": "user-1", "username": "用户"},
    })
    assert received[0]["chat_id"] == "qqbot:user-1"
    assert received[0]["is_group"] is False
    assert client.last[0:2] == ("POST", "/v2/users/user-1/messages")
    assert client.last[2]["msg_id"] == "in-1"
