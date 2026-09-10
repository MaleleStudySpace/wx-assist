from pathlib import Path

from src.router import MessageRouter


class FakeStore:
    def insert_message(self, msg):
        return True


class FakeAgent:
    def __init__(self):
        self.calls = []

    def get_tool_descriptions(self):
        return "- filesystem__read_file :path str (必填) 读取文件"

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return "reply"


class FakeConfig:
    memory_consolidation_enabled = False


def _msg(chat_id, message_id="m1"):
    return {
        "message_id": message_id, "chat_id": chat_id, "sender_id": chat_id,
        "sender_name": "user", "content": "hello", "msg_type": 1,
        "timestamp": 1, "is_group": False, "group_name": chat_id,
        "chat_type": "dm",
    }


def test_router_welcome_is_short_and_does_not_expose_tool_schema(tmp_path, monkeypatch):
    welcome = tmp_path / "welcomed_users.json"
    monkeypatch.setattr("src.router.WELCOME_FILE", Path(welcome))
    agent = FakeAgent()
    router = MessageRouter(FakeStore(), None, FakeConfig(), agent)
    result = router.handle(_msg("qqbot:user-1"))
    assert "filesystem__read_file" not in result
    assert "path str" not in result
    assert "required" not in result
    assert "系统状态" in result
    assert len(agent.calls) == 1

    second = router.handle(_msg("qqbot:user-1", "m2"))
    assert second == "reply"
    assert "系统状态" not in second
    assert __import__("json").loads(welcome.read_text(encoding="utf-8")) == {
        "welcomed": ["qqbot:user-1"]
    }
