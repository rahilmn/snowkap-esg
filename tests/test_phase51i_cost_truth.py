"""Phase 51.I — LLM cost-logging truth (production-readiness Wave 1).

The /metrics spend series + the per-tenant daily budget cap both read
`llm_calls.cost_usd`. It was blind two ways: the price table was OpenAI-only
(no Anthropic rows → every Opus call logged $0) AND `_estimate_cost` never
stripped the provider prefix, so even `openai/gpt-4.1` priced to $0 and
silently under-counted real spend. Fix: prefix-strip + Anthropic rows + prefer
the gateway-billed `cost_usd` when present.

Run: python -m pytest tests/test_phase51i_cost_truth.py -q
"""
from __future__ import annotations

import pytest

from engine.models.llm_calls import _estimate_cost


def test_provider_prefix_is_stripped():
    # the bug: a prefixed model id priced to $0 (provider prefix not stripped)
    assert _estimate_cost("openai/gpt-4.1", 1000, 1000) == pytest.approx(0.02)
    assert _estimate_cost("gpt-4.1", 1000, 1000) == pytest.approx(0.02)


def test_anthropic_opus_is_priced():
    # was $0 (no Anthropic rows) → Opus spend invisible to the budget cap
    assert _estimate_cost("anthropic/claude-opus-4.6", 1000, 1000) == pytest.approx(0.09)
    assert _estimate_cost("claude-opus-4.6", 1000, 1000) == pytest.approx(0.09)


def test_unknown_model_is_zero_not_overcharged():
    assert _estimate_cost("foo/bar-unknown", 1000, 1000) == 0.0
    assert _estimate_cost("", 1000, 1000) == 0.0


def test_billed_cost_overrides_estimate():
    """log_call(cost_usd=...) wins over the local estimate so the spend
    metric reflects actual provider billing."""
    import engine.models.llm_calls as m

    captured: dict = {}

    class _FakeConn:
        def execute(self, _sql, params):
            captured["cost"] = params[6]  # cost_usd column position
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import contextlib

    @contextlib.contextmanager
    def _fake_connect():
        yield _FakeConn()

    monkey_ready = m._SCHEMA_READY
    try:
        m._SCHEMA_READY = True  # skip ensure_schema DDL
        orig = m._connect
        m._connect = _fake_connect
        # billed value (0.123) must win over the gpt-4.1 estimate (0.02)
        m.log_call(model="openai/gpt-4.1", prompt_tokens=1000,
                   completion_tokens=1000, cost_usd=0.123)
        assert captured["cost"] == pytest.approx(0.123)
        # no billed value → falls back to the (now-correct) estimate
        m.log_call(model="openai/gpt-4.1", prompt_tokens=1000, completion_tokens=1000)
        assert captured["cost"] == pytest.approx(0.02)
    finally:
        m._connect = orig
        m._SCHEMA_READY = monkey_ready
