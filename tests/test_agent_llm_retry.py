from types import SimpleNamespace

from src.summarize.claude_backend import ClaudeSummarizer
from src.summarize.deepseek_backend import OpenAICompatSummarizer


class _TransientError(Exception):
    pass


def test_deepseek_agent_chat_retries_and_updates_last_call_time(monkeypatch):
    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _TransientError("temporary")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content="answer", tool_calls=None, reasoning_content="",
                ))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    completions = FakeCompletions()
    backend = OpenAICompatSummarizer.__new__(OpenAICompatSummarizer)
    backend.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    backend.model = "test-model"
    backend.extra_body = {}
    backend.max_retries = 2
    backend.retry_exceptions = (_TransientError,)
    backend.last_api_call_time = 0.0

    monkeypatch.setattr("src.summarize.base.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("src.summarize.deepseek_backend.log_llm_interaction", lambda **_kwargs: None)

    content, tool_calls, reasoning = backend.agent_chat(
        "system", [{"role": "user", "content": "hello"}], [],
    )

    assert (content, tool_calls, reasoning) == ("answer", None, "")
    assert completions.calls == 2
    assert backend.last_api_call_time > 0


def test_claude_agent_chat_retries_and_updates_last_call_time(monkeypatch):
    class FakeMessages:
        def __init__(self):
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise _TransientError("temporary")
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="answer")],
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

    messages = FakeMessages()
    backend = ClaudeSummarizer.__new__(ClaudeSummarizer)
    backend.client = SimpleNamespace(messages=messages)
    backend.model = "test-model"
    backend.max_retries = 2
    backend.retry_exceptions = (_TransientError,)
    backend.last_api_call_time = 0.0

    monkeypatch.setattr("src.summarize.base.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("src.summarize.claude_backend.log_llm_interaction", lambda **_kwargs: None)

    content, tool_calls, reasoning = backend.agent_chat(
        "system", [{"role": "user", "content": "hello"}], [],
    )

    assert (content, tool_calls, reasoning) == ("answer", None, "")
    assert messages.calls == 2
    assert backend.last_api_call_time > 0
