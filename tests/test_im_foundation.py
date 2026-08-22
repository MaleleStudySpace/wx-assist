from pathlib import Path

from src.im.base import BasePlatformAdapter
from src.im.config_schema import PlatformConfig, load_platforms_config
from src.im.message import MessageType, NormalizedMessage
from src.im.progress_router import ProgressRouter
from src.im.push_channel import PushChannel
from src.im.registry import PlatformRegistry


class FakeChannel(PushChannel):
    channel_name = "fake"
    platform = "fake"

    def __init__(self):
        self.sent = []

    def is_available(self):
        return True

    def is_healthy(self):
        return True

    def send_message(self, text, *, progress_callback=None, target=None):
        self.sent.append((text, target))
        return {"success": True}

    def get_status(self):
        return {"ok": True}


class FakeAdapter(BasePlatformAdapter):
    platform_name = "fake"

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.started = False

    def start(self, callback, groups=None):
        if self.should_fail:
            raise RuntimeError("boom")
        self.started = True
        return True

    def stop(self):
        self.started = False

    def send_text(self, chat_id, content, *, reply_to=None):
        return True

    def health_status(self):
        return {"ok": self.started}

    def list_chats(self):
        return []


def test_message_legacy_roundtrip_preserves_semantics():
    msg = NormalizedMessage(
        platform="qqbot",
        native_message_id="m1",
        chat_id="qqbot:group-1",
        chat_type="group",
        sender_id="qqbot:user-1",
        sender_name="tester",
        content="hello",
        message_type=MessageType.TEXT,
        timestamp=123,
    )
    legacy = msg.to_legacy_dict()
    assert legacy["chat_id"] == "qqbot:group-1"
    assert legacy["is_group"] is True


def test_message_from_legacy_namespaces_once():
    msg = NormalizedMessage.from_legacy_dict(
        {"message_id": "m1", "chat_id": "openid", "sender_id": "user", "is_group": False},
        platform="qqbot",
    )
    assert msg.chat_id == "qqbot:openid"
    assert msg.sender_id == "qqbot:user"


def test_namespace_is_idempotent():
    assert BasePlatformAdapter.namespace("qqbot", "openid") == "qqbot:openid"
    assert BasePlatformAdapter.namespace("qqbot", "qqbot:openid") == "qqbot:openid"
    assert BasePlatformAdapter.parse("qqbot:openid") == ("qqbot", "openid")


def test_progress_router_isolates_channel():
    channel = FakeChannel()
    router = ProgressRouter()
    router.register("qqbot", channel)
    assert router.push("qqbot", "progress") is True
    assert router.push("wechat", "progress") is False
    assert channel.sent == [("progress", None)]


def test_registry_isolates_start_failure():
    created = {}

    def factory(config):
        adapter = FakeAdapter(config.name == "bad")
        created[config.name] = adapter
        return adapter

    registry = PlatformRegistry(factory=factory)
    result = registry.start_all(
        [PlatformConfig("good"), PlatformConfig("bad")],
        lambda message: None,
    )
    assert result["started"] == ["good"]
    assert "bad" in result["failed"]
    assert registry.get_health()["good"]["ok"] is True


def test_missing_platform_config_degrades_safely(tmp_path: Path):
    assert load_platforms_config(tmp_path / "missing.json") == []
