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

from engine.llm.client import (
    OpenRouterClient,
    _is_openrouter_billing_block,
    _is_openrouter_out_of_credits,
)

# Exceptions that carry an HTTP status_code, like the OpenAI SDK's errors.
_E402 = type("_E402", (Exception,), {"status_code": 402})
_E403 = type("_E403", (Exception,), {"status_code": 403})  # org budget cap


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
    # Phase 52 — reasoning_heavy's OpenAI fallback is the gpt-5-mini reasoning
    # model; the gateway translates its params (max_completion_tokens, no temp).
    assert fb["model"] == "gpt-5-mini"
    assert "extra_headers" not in fb        # OpenRouter routing headers dropped
    assert "max_tokens" not in fb           # translated for the reasoning model
    assert fb.get("max_completion_tokens", 0) > 0

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
        model="gpt-5-mini",
        usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 5, "completion_tokens": 3}),
    )
    oac = MagicMock()
    oac.chat.completions.create.return_value = fake
    cl._direct_openai_sync = lambda: oac  # the direct-OpenAI fallback client

    resp = cl.complete([{"role": "user", "content": "hi"}])
    # Phase 52 — reasoning_heavy falls back to the gpt-5-mini reasoning model,
    # with its params translated (max_completion_tokens, no max_tokens/temperature).
    assert resp.model_used == "gpt-5-mini"
    call_kwargs = oac.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-5-mini"
    assert "extra_headers" not in call_kwargs
    assert "max_tokens" not in call_kwargs
    assert call_kwargs.get("max_completion_tokens", 0) > 0
    assert "temperature" not in call_kwargs


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


# ---------------------------------------------------------------------------
# Phase 53.X — the org BUDGET CAP is a 403 (PermissionDeniedError), not 402,
# and the Ask uses stream() (which previously had NO fallback). Both gaps closed.
# ---------------------------------------------------------------------------
def test_billing_block_matches_402_and_403_not_429_or_401():
    assert _is_openrouter_billing_block(_E402("x")) is True
    assert _is_openrouter_billing_block(_E403("monthly budget cap")) is True
    assert _is_openrouter_billing_block(Exception("Insufficient credits")) is True
    assert _is_openrouter_billing_block(Exception("PermissionDeniedError: 403 budget")) is True
    assert _is_openrouter_billing_block(Exception("429 rate limit")) is False
    assert _is_openrouter_billing_block(Exception("401 unauthorized")) is False


def _chunk(text, finish=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)],
        usage=None,
    )


def test_stream_falls_back_to_openai_direct_on_403(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="chat")

    orc = MagicMock()
    orc.chat.completions.create.side_effect = _E403("org monthly budget cap")
    cl._sync = orc  # OpenRouter stream-open raises 403 before any chunk

    oac = MagicMock()
    oac.chat.completions.create.return_value = iter([_chunk("Hel"), _chunk("lo", finish="stop")])
    cl._direct_openai_sync = lambda: oac

    out = "".join(ev.delta for ev in cl.stream([{"role": "user", "content": "hi"}]))
    assert out == "Hello"  # streamed from the fallback, not the canned echo
    fb = oac.chat.completions.create.call_args.kwargs
    assert fb["model"] == "gpt-5-mini"   # chat's OpenAI-direct model
    assert fb.get("stream") is True
    assert "extra_headers" not in fb     # OpenRouter routing headers dropped


def test_stream_reraises_non_billing_errors(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    cl = OpenRouterClient(task_class="chat")
    orc = MagicMock()
    orc.chat.completions.create.side_effect = RuntimeError("429 rate limited")
    cl._sync = orc
    sentinel = MagicMock()
    cl._direct_openai_sync = lambda: sentinel

    with pytest.raises(RuntimeError):
        list(cl.stream([{"role": "user", "content": "hi"}]))
    sentinel.chat.completions.create.assert_not_called()
