"""Phase 51.C — OpenRouter 402 (out-of-credits) → direct-OpenAI gpt-4.1 fallback.

When OpenRouter is the active provider but runs out of credits mid-run, the
gateway transparently retries the same call against direct OpenAI using the
task class's bare model (reasoning_heavy → gpt-4.1), instead of failing the
pipeline. Fires ONLY when OpenRouter is active and an OPENAI_API_KEY exists.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from engine.llm.client import OpenRouterClient, _is_openrouter_out_of_credits

# An exception that carries an HTTP status_code, like the OpenAI SDK's errors.
_E402 = type("_E402", (Exception,), {"status_code": 402})


def test_detector_matches_only_credit_errors():
    assert _is_openrouter_out_of_credits(_E402("x")) is True
    assert _is_openrouter_out_of_credits(Exception("Insufficient credits")) is True
    assert _is_openrouter_out_of_credits(Exception("402 Payment Required")) is True
    assert _is_openrouter_out_of_credits(Exception("429 rate limit")) is False
    assert _is_openrouter_out_of_credits(Exception("401 unauthorized")) is False


def test_fallback_kwargs_gated_correctly(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="reasoning_heavy")
    orig = {"model": "anthropic/claude-opus-4.6", "messages": [], "extra_headers": {"X-Title": "x"}}

    fb = cl._maybe_fallback_kwargs(_E402("insufficient credits"), orig, None)
    assert fb is not None
    assert fb["model"] == "gpt-4.1"        # vendor-prefixed model → bare OpenAI model
    assert "extra_headers" not in fb        # OpenRouter routing headers dropped

    # non-credits error → no fallback
    assert cl._maybe_fallback_kwargs(Exception("429"), orig, None) is None
    # OpenRouter active but no OPENAI_API_KEY → no fallback
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cl._maybe_fallback_kwargs(_E402("x"), orig, None) is None


def test_no_fallback_when_already_direct_openai(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="reasoning_heavy")
    # legacy/direct mode already — nothing to fall back to
    assert cl._maybe_fallback_kwargs(_E402("x"), {"model": "gpt-4.1", "messages": []}, None) is None


def test_complete_falls_back_to_gpt41_on_402(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="reasoning_heavy")

    orc = MagicMock()
    orc.chat.completions.create.side_effect = _E402("insufficient credits")
    cl._sync = orc  # the OpenRouter client (raises 402)

    fake = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        model="gpt-4.1",
        usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 5, "completion_tokens": 3}),
    )
    oac = MagicMock()
    oac.chat.completions.create.return_value = fake
    cl._direct_openai_sync = lambda: oac  # the direct-OpenAI fallback client

    resp = cl.complete([{"role": "user", "content": "hi"}])
    assert resp.model_used == "gpt-4.1"
    call_kwargs = oac.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4.1"
    assert "extra_headers" not in call_kwargs


def test_complete_reraises_non_credit_errors_without_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="reasoning_heavy")

    orc = MagicMock()
    orc.chat.completions.create.side_effect = RuntimeError("429 rate limited")
    cl._sync = orc
    sentinel = MagicMock()
    cl._direct_openai_sync = lambda: sentinel

    with pytest.raises(RuntimeError):
        cl.complete([{"role": "user", "content": "hi"}])
    sentinel.chat.completions.create.assert_not_called()
