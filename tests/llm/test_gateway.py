"""OpenRouter gateway tests — keys / routing / cost / client."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from engine.llm.client import (
    LLMResponse,
    OpenRouterClient,
    TokenEvent,
    get_llm_client,
)
from engine.llm.cost import estimate_cost, parse_cost
from engine.llm.keys import (
    is_using_legacy_openai,
    resolve_base_url,
    resolve_openrouter_key,
)
from engine.llm.routing import TASK_CLASS_TO_MODEL, resolve_model


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def test_resolve_openrouter_key_prefers_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key-123")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key-xyz")
    assert resolve_openrouter_key() == "or-key-123"
    assert is_using_legacy_openai() is False
    assert resolve_base_url() == "https://openrouter.ai/api/v1"


def test_resolve_falls_back_to_openai_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key-xyz")
    assert resolve_openrouter_key() == "openai-key-xyz"
    assert is_using_legacy_openai() is True
    assert resolve_base_url() is None  # OpenAI default


def test_resolve_returns_placeholder_when_neither_set(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_openrouter_key().startswith("sk-placeholder")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_task_class_to_model_covers_core_classes():
    """Every Power-of-Now task class has a model mapping."""
    for tc in ("reasoning_default", "extraction", "composition",
               "classification", "chat", "embeddings"):
        assert tc in TASK_CLASS_TO_MODEL


def test_resolve_model_returns_openrouter_vendor_prefix(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert resolve_model("extraction") == "openai/gpt-4.1-mini"
    assert resolve_model("reasoning_heavy") == "anthropic/claude-opus-4.6"


def test_resolve_model_strips_prefix_in_legacy_mode(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert resolve_model("extraction") == "gpt-4.1-mini"
    assert resolve_model("classification") == "gpt-4o-mini"
    # An anthropic model in legacy mode falls back to gpt-4.1
    assert resolve_model("reasoning_heavy") == "gpt-4.1"


def test_resolve_model_override_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert resolve_model("extraction", override="anthropic/claude-haiku") == "anthropic/claude-haiku"


def test_resolve_model_default_when_unknown_class():
    val = resolve_model("not_a_class")
    # Should not raise; falls back to a real model
    assert isinstance(val, str) and val


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_parse_cost_reads_direct_cost_field():
    assert parse_cost({"cost": 0.0042}) == pytest.approx(0.0042)


def test_parse_cost_falls_back_to_cost_usd():
    assert parse_cost({"cost_usd": 0.0051}) == pytest.approx(0.0051)


def test_parse_cost_returns_none_when_missing():
    assert parse_cost(None) is None
    assert parse_cost({}) is None
    assert parse_cost({"prompt_tokens": 100}) is None


def test_parse_cost_handles_malformed_value():
    assert parse_cost({"cost": "not a number"}) is None


def test_estimate_cost_delegates_to_legacy_table():
    cost = estimate_cost("gpt-4.1-mini", 1000, 500)
    assert cost > 0


# ---------------------------------------------------------------------------
# OpenRouterClient (stubbed)
# ---------------------------------------------------------------------------


class _StubChatCompletions:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        self._last_kwargs = kwargs
        return self._response


class _StubSyncClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_StubChatCompletions(response))


def _make_completion(text="hello", model="gpt-4.1", usage=None):
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=20, total_tokens=30,
            cost=(usage or {}).get("cost"),
            cost_usd=(usage or {}).get("cost_usd"),
        ),
    )


def test_complete_returns_llm_response_with_text():
    stub_resp = _make_completion(text="Hi there", usage={"cost": 0.001})
    client = OpenRouterClient(task_class="chat", sync_client=_StubSyncClient(stub_resp))
    result = client.complete([{"role": "user", "content": "ping"}])
    assert isinstance(result, LLMResponse)
    assert result.text == "Hi there"
    assert result.finish_reason == "stop"
    assert result.usage.get("cost") == 0.001
    assert result.cost_usd == pytest.approx(0.001)
    assert result.latency_ms >= 0


def test_complete_passes_messages_and_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    stub_resp = _make_completion()
    stub_client = _StubSyncClient(stub_resp)
    client = OpenRouterClient(task_class="extraction", sync_client=stub_client)
    client.complete([{"role": "user", "content": "test"}])
    sent = stub_client.chat.completions._last_kwargs
    assert sent["messages"] == [{"role": "user", "content": "test"}]
    assert sent["model"] == "openai/gpt-4.1-mini"  # vendor-prefixed in OpenRouter mode
    assert "extra_headers" in sent
    assert sent["extra_headers"]["HTTP-Referer"]


def test_complete_uses_legacy_model_name_when_no_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    stub_resp = _make_completion(model="gpt-4.1-mini")
    stub_client = _StubSyncClient(stub_resp)
    client = OpenRouterClient(task_class="extraction", sync_client=stub_client)
    client.complete([{"role": "user", "content": "x"}])
    assert stub_client.chat.completions._last_kwargs["model"] == "gpt-4.1-mini"


def test_complete_handles_missing_usage():
    """A response without `usage` shouldn't raise — usage = {}."""
    msg = SimpleNamespace(content="ok")
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    stub_resp = SimpleNamespace(choices=[choice])  # no usage attr
    client = OpenRouterClient(sync_client=_StubSyncClient(stub_resp))
    result = client.complete([{"role": "user", "content": "x"}])
    assert result.usage == {}
    assert result.cost_usd is None


def test_get_llm_client_returns_configured_client():
    client = get_llm_client(task_class="chat")
    assert isinstance(client, OpenRouterClient)
    assert client.task_class == "chat"


def test_get_llm_client_with_model_override():
    client = get_llm_client(model="gpt-4o")
    assert client.model_for() == "gpt-4o"


def test_provider_route_reflects_mode(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    client = OpenRouterClient()
    assert client.provider_route == "openrouter"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client2 = OpenRouterClient()
    assert client2.provider_route == "openai-direct"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class _StreamingStub:
    """Iterator-style stub that yields chunked completions."""
    def __init__(self, deltas: list[str]):
        self._deltas = deltas

    def __iter__(self):
        for d in self._deltas:
            delta_obj = SimpleNamespace(content=d)
            choice = SimpleNamespace(delta=delta_obj, finish_reason=None)
            yield SimpleNamespace(choices=[choice], usage=None)
        # Final chunk with finish_reason
        final_delta = SimpleNamespace(content="")
        final_choice = SimpleNamespace(delta=final_delta, finish_reason="stop")
        yield SimpleNamespace(
            choices=[final_choice],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8,
                                  cost=0.0005, cost_usd=None),
        )


def test_stream_yields_token_events():
    deltas = ["Hello", " ", "world"]
    stream_stub = _StreamingStub(deltas)

    class _StreamSync:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: stream_stub))

    client = OpenRouterClient(task_class="chat", sync_client=_StreamSync())
    events = list(client.stream([{"role": "user", "content": "hi"}]))
    deltas_yielded = [e.delta for e in events]
    assert deltas_yielded[:3] == ["Hello", " ", "world"]
    assert isinstance(events[0], TokenEvent)
    # Final event carries finish_reason + usage
    last = events[-1]
    assert last.finish_reason == "stop"
    assert last.usage is not None
