import json

from src.im.config_schema import PlatformConfig
from src.im.plugins.feishu.adapter import FeishuAdapter
from src.im.plugins.feishu.events import verify_and_parse_event
from src.im.plugins.feishu.openapi_client import FeishuOpenAPIClient


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self):
        return self.data


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if "tenant_access_token" in url:
            return Response({"code": 0, "msg": "ok", "tenant_access_token": "tenant-1", "expire": 7200})
        return Response({"code": 0, "data": {"message_id": "msg-1"}})

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response({"code": 0, "data": {"message_id": "msg-1"}})

    def close(self):
        pass


def test_feishu_url_verification_challenge():
    assert verify_and_parse_event({
        "type": "url_verification",
        "token": "verify",
        "challenge": "challenge-1",
    }, "verify") == {"challenge": "challenge-1"}


def test_feishu_event_normalization():
    msg = verify_and_parse_event({
        "header": {"event_type": "im.message.receive_v1", "token": "verify"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "create_time": "1710000000000",
                "content": json.dumps({"text": "你好"}, ensure_ascii=False),
            },
        },
    }, "verify")
    assert msg.platform == "feishu"
    assert msg.chat_id == "feishu:oc_chat"
    assert msg.chat_type == "group"
    assert msg.sender_id == "feishu:ou_user"
    assert msg.content == "你好"
    assert msg.timestamp == 1710000000


def test_feishu_token_and_send_contract():
    session = Session()
    client = FeishuOpenAPIClient("app", "secret", session=session)
    client.send_text("oc_chat", "hello")
    assert session.calls[0][1].endswith("tenant_access_token/internal")
    assert session.calls[0][2]["json"] == {"app_id": "app", "app_secret": "secret"}
    method, url, kwargs = session.calls[-1]
    assert method == "POST"
    assert "receive_id_type=chat_id" in url or kwargs.get("params", {}).get("receive_id_type") == "chat_id"
    assert kwargs["headers"]["Authorization"] == "Bearer tenant-1"
    assert kwargs["json"]["msg_type"] == "text"
    assert json.loads(kwargs["json"]["content"])["text"] == "hello"


def test_feishu_adapter_webhook_reply_uses_channel():
    adapter = FeishuAdapter(
        PlatformConfig("feishu", extra={
            "app_id": "app", "app_secret": "secret", "verification_token": "verify",
        }),
        api_client=FeishuOpenAPIClient("app", "secret", session=Session()),
    )
    assert adapter.start(lambda message: "收到") is True
    result = adapter.handle_webhook({
        "header": {"event_type": "im.message.receive_v1", "token": "verify"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {
                "message_id": "om_1", "chat_id": "oc_chat", "chat_type": "p2p",
                "content": json.dumps({"text": "你好"}),
            },
        },
    })
    assert result == {"code": 0}
