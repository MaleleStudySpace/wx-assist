from src.agent.engine import AgentEngine


class FakeRegistry:
    def get_all_schemas(self):
        return []

    def get_descriptions(self):
        return ""


class FakeTools:
    registry = FakeRegistry()


class FakeLLM:
    def agent_chat(self, **kwargs):
        return "ok", None, ""

    def chat(self, **kwargs):
        return "无"


def test_agent_histories_are_keyed_by_conversation():
    engine = AgentEngine(FakeLLM(), FakeTools())
    engine._react_loop = lambda *args, **kwargs: "reply"
    engine.run("a", conversation_key="qqbot:a")
    engine.run("b", conversation_key="qqbot:b")
    assert engine._histories["qqbot:a"] == [("a", "reply")]
    assert engine._histories["qqbot:b"] == [("b", "reply")]


def test_agent_pending_confirmation_is_keyed_by_conversation():
    engine = AgentEngine(FakeLLM(), FakeTools())
    engine._pending_confirms["qqbot:a"] = {
        "question": "confirm A", "messages": [],
        "system": "", "confirm_tool_call_id": "id-a", "action_tcs": [],
    }
    result = engine.run("确定", conversation_key="qqbot:b")
    assert result == "ok"
    assert "qqbot:a" in engine._pending_confirms
    assert "qqbot:b" not in engine._pending_confirms
