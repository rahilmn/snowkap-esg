"""Phase 53 (M) — LLM event classification on the theme-fallback path.

The rule-based classifier theme-fell-back NOISE to actionable events (a stock
blip -> event_board_change, a macro note -> event_credit_rating, a scam advisory
-> event_cyber_incident), which inflated the noise into the critical tier — the
live demo audit's #1 failure. When keyword matching is inconclusive, an LLM now
picks the real event OR declares it a non-event (event_default). Gated by
SNOWKAP_LLM_EVENT_FALLBACK=1 so the deterministic path is unchanged by default.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import engine.nlp.event_classifier as ec


def test_gated_off_by_default():
    # No env flag → no LLM call, returns None (caller keeps the theme-default).
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("SNOWKAP_LLM_EVENT_FALLBACK", None)
        assert ec._llm_classify_event("title", "body", []) is None


def _fake_client(event_id: str):
    """Build a fake LLM client whose .sync.chat.completions.create returns event_id."""
    content = json.dumps({"event_id": event_id, "confidence": 0.9})
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    create = lambda **kw: resp  # noqa: E731
    completions = SimpleNamespace(create=create)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(sync=SimpleNamespace(chat=chat))


def _rules():
    return ec._cached_rules()


def test_llm_marks_stock_blip_as_non_event(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_EVENT_FALLBACK", "1")
    rules = _rules()
    with patch("engine.llm.get_llm_client", return_value=_fake_client("event_default")):
        out = ec._llm_classify_event("SBI Stock Update: Shares Gain 0.94%", "intraday move", rules)
    assert out == ec._LLM_NON_EVENT


def test_llm_real_event_kept_and_not_theme_fallback(monkeypatch):
    monkeypatch.setenv("SNOWKAP_LLM_EVENT_FALLBACK", "1")
    rules = _rules()
    with patch("engine.llm.get_llm_client", return_value=_fake_client("event_criminal_indictment")):
        out = ec._llm_classify_event("Court denies bail in loan case", "body", rules)
    assert isinstance(out, ec.EventRule) and out.event_id == "event_criminal_indictment"


def test_classify_event_uses_llm_non_event_over_theme_default(monkeypatch):
    # A no-keyword-match stock blip + a theme that would default to a hard event:
    # with the LLM saying non-event, classify_event returns event_default (NOT a
    # theme-fallback actionable event).
    monkeypatch.setenv("SNOWKAP_LLM_EVENT_FALLBACK", "1")
    with patch("engine.llm.get_llm_client", return_value=_fake_client("event_default")):
        e = ec.classify_event(
            "State Bank of India Stock Update: Shares Gain 0.94% in Morning Trade",
            "shares rose in intraday trade",
            theme="Board & Leadership",
        )
    assert e.event_id == "event_default"
    assert e.matched_keywords != ["[theme_fallback]"]


def test_classify_event_llm_failure_degrades_to_theme_default(monkeypatch):
    # If the LLM errors, classify_event falls back to the theme-default path.
    monkeypatch.setenv("SNOWKAP_LLM_EVENT_FALLBACK", "1")
    def _boom(*a, **k):
        raise RuntimeError("llm down")
    with patch("engine.llm.get_llm_client", side_effect=_boom):
        e = ec.classify_event("Some ambiguous headline", "body", theme="Climate Change")
    # degrades gracefully — either a theme-default or event_default, never raises
    assert e.event_id  # non-empty, no exception
