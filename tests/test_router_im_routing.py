from types import SimpleNamespace

from src.router import MessageRouter


class FakeStore:
    def __init__(self):
        self.items = []

    def insert_message(self, msg):
        self.items.append(msg)
        return True


class FakeAgent:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return "reply"


class FakeConfig:
    memory_consolidation_enabled = False


def test_router_qq_dm_has_no_undefined_chat_id_and_routes_agent():
    agent = FakeAgent()
    router = MessageRouter(FakeStore(), None, FakeConfig(), agent)
    result = router.handle({
        "message_id": "m1", "chat_id": "qqbot:user-1", "sender_id": "qqbot:user-1",
        "sender_name": "user", "content": "hello", "msg_type": 1,
        "timestamp": 1, "is_group": False, "group_name": "qqbot:user-1",
    })
    assert result == "reply"
    assert agent.calls[0]["source_platform"] == "qqbot"
    assert agent.calls[0]["source_target"] == "qqbot:user-1"
    assert agent.calls[0]["conversation_key"] == "qqbot:user-1"


def test_router_feishu_dm_routes_agent():
    agent = FakeAgent()
    router = MessageRouter(FakeStore(), None, FakeConfig(), agent)
    result = router.handle({
        "message_id": "m2", "chat_id": "feishu:oc-1", "sender_id": "feishu:ou-1",
        "sender_name": "user", "content": "hello", "msg_type": 1,
        "timestamp": 1, "is_group": False, "group_name": "oc-1",
        "chat_type": "dm",
    })
    assert result == "reply"
    assert agent.calls[0]["source_platform"] == "feishu"
