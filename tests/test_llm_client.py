"""LLMClient retry / error classification, with a fake OpenAI client."""
from types import SimpleNamespace

import pytest

from app.services.ai import llm_client as llm_module
from app.services.ai.llm_client import AIServiceError, LLMClient, parse_json_text


def _reply(content=None, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(outcomes):
    client = LLMClient()
    client._available = True
    client._model = "test-model"
    completions = FakeCompletions(outcomes)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda _s: None)


def test_retries_transient_failure_then_succeeds(app):
    client, completions = _client([RuntimeError("blip"), _reply('{"ok": true}')])
    result = client.generate_structured_json_from_text(system_prompt="s", user_prompt="u", max_retries=2)
    assert parse_json_text(result.text) == {"ok": True}
    assert len(completions.calls) == 2
    assert completions.calls[0]["response_format"] == {"type": "json_object"}


def test_gives_up_after_max_retries(app):
    client, completions = _client([RuntimeError("a"), RuntimeError("b")])
    with pytest.raises(AIServiceError) as exc:
        client.generate_structured_json_from_text(system_prompt="s", user_prompt="u", max_retries=1)
    assert exc.value.code == "AI_UNAVAILABLE"
    assert len(completions.calls) == 2


def test_empty_response_is_an_error(app):
    client, _ = _client([_reply("  ")])
    with pytest.raises(AIServiceError) as exc:
        client.generate_structured_json_from_text(system_prompt="s", user_prompt="u", max_retries=0)
    assert exc.value.code == "AI_EMPTY_RESPONSE"


def test_not_configured(app):
    with pytest.raises(AIServiceError) as exc:
        LLMClient().chat_completion(messages=[])
    assert exc.value.code == "AI_NOT_CONFIGURED"


def test_chat_completion_returns_tool_calls(app):
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_my_farms", arguments=""))
    client, completions = _client([_reply(None, [call])])
    result = client.chat_completion(messages=[{"role": "user", "content": "hi"}], tools=[{}], tool_choice="auto")
    assert result.content is None
    assert result.tool_calls == [{"id": "c1", "name": "get_my_farms", "arguments": "{}"}]
    assert completions.calls[0]["tool_choice"] == "auto"


def test_parse_json_text_strips_code_fence():
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
