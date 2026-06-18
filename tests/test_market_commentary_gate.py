"""Phase 51.K — market-commentary gate + materiality demotion + polish.

Covers the production-readiness fix for the "Adani Power vs NTPC? Which is a
better bet? 7 factors investors should watch" panel, which manufactured a
confident IR action from a generic markets listicle. The fix is deterministic
(model-independent):

  A. recommendation_engine._should_skip — honor MONITOR + suppress market
     commentary, routing to the monitor-only/do_nothing path.
  E. criticality_scorer.score(market_commentary=True) — hard-cap band at LOW.
  B. unified_analysis._truncate_prose — word/sentence-boundary truncation.
  F. routing.resolve_model — SNOWKAP_REASONING_MODEL / SNOWKAP_LLM_MODEL env.

The discriminator that protects genuine multi-company events: an actionable
event_id (event_contract_win, event_heavy_penalty, …) → never suppressed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.analysis.signal_classifiers import comparison_framing, is_market_commentary
from engine.analysis.recommendation_engine import _should_skip
from engine.analysis import criticality_scorer
from engine.analysis.unified_analysis import _truncate_prose
from engine.llm.routing import resolve_model


_LISTICLE = "Adani Power vs NTPC? Which is a better bet? 7 factors that investors should watch"
_REAL_EVENT = "Adani Power wins ₹5000 Cr solar tender from SECI"


def _result(title: str, event_id: str | None = "event_default"):
    event = SimpleNamespace(event_id=event_id) if event_id is not None else None
    return SimpleNamespace(title=title, event=event)


def _insight(materiality: str, action: str, impact: float = 5.0):
    return SimpleNamespace(
        decision_summary={"materiality": materiality, "action": action},
        impact_score=impact,
    )


# ---------------------------------------------------------------------------
# comparison_framing + is_market_commentary
# ---------------------------------------------------------------------------


class TestComparisonFraming:
    def test_vs_listicle_detected(self):
        assert comparison_framing(_LISTICLE) is True

    def test_factors_investors_detected(self):
        assert comparison_framing("Reliance: 5 factors that investors should watch") is True

    def test_real_event_not_framed(self):
        assert comparison_framing(_REAL_EVENT) is False
        assert comparison_framing("NTPC fined Rs 200 cr by CEA for emission breach") is False

    def test_empty_title(self):
        assert comparison_framing("") is False
        assert comparison_framing(None) is False


class TestIsMarketCommentary:
    def test_non_actionable_listicle_is_commentary(self):
        assert is_market_commentary(_result(_LISTICLE, "event_default")) is True

    def test_actionable_event_never_commentary(self):
        # Even with a "vs" headline, a real event (actionable event_id) is NOT
        # suppressed — the false-positive guard.
        assert is_market_commentary(_result(_LISTICLE, "event_contract_win")) is False

    def test_real_event_no_framing_not_commentary(self):
        assert is_market_commentary(_result(_REAL_EVENT, "event_default")) is False

    def test_missing_event_defaults_to_non_actionable(self):
        assert is_market_commentary(_result(_LISTICLE, None)) is True


# ---------------------------------------------------------------------------
# A. _should_skip action gate
# ---------------------------------------------------------------------------


class TestShouldSkipGate:
    def test_monitor_on_non_actionable_skips(self):
        skip, reason = _should_skip(_insight("MEDIUM", "MONITOR"), _result(_LISTICLE))
        assert skip is True
        assert "MONITOR" in reason

    def test_market_commentary_with_act_still_skips(self):
        # The degraded LLM said ACT, but it's a non-actionable listicle → skip.
        skip, reason = _should_skip(_insight("MEDIUM", "ACT"), _result(_LISTICLE))
        assert skip is True
        assert "commentary" in reason.lower()

    def test_real_event_acts(self):
        skip, _ = _should_skip(
            _insight("HIGH", "ACT"), _result(_REAL_EVENT, "event_contract_win")
        )
        assert skip is False

    def test_real_penalty_event_acts(self):
        skip, _ = _should_skip(
            _insight("CRITICAL", "ACT"),
            _result("NTPC fined Rs 200 cr for emission breach", "event_heavy_penalty"),
        )
        assert skip is False

    def test_critical_listicle_not_suppressed(self):
        # If the LLM strongly rates it CRITICAL, defer to it (don't auto-suppress).
        skip, _ = _should_skip(_insight("CRITICAL", "ACT"), _result(_LISTICLE))
        assert skip is False

    def test_existing_nonmaterial_ignore_regression(self):
        skip, reason = _should_skip(_insight("NON-MATERIAL", "IGNORE"), _result(_REAL_EVENT))
        assert skip is True
        assert "ignore" in reason.lower()

    def test_genuine_actionable_monitor_not_skipped_by_branch1(self):
        # MONITOR but actionable event → branch 1 must NOT fire (real event).
        skip, _ = _should_skip(
            _insight("HIGH", "MONITOR"), _result(_REAL_EVENT, "event_contract_win")
        )
        assert skip is False


# ---------------------------------------------------------------------------
# E. materiality demotion in the scorer
# ---------------------------------------------------------------------------


class TestMaterialityDemotion:
    def _score(self, market_commentary: bool):
        return criticality_scorer.score(
            relevance_total=9.0,
            event_severity=None,
            cascade_total_cr=0.0,
            company_revenue_cr=None,
            event_id=None,
            published_at="2026-06-17T10:00:00+00:00",
            market_commentary=market_commentary,
        )

    def test_commentary_capped_at_low(self):
        capped = self._score(market_commentary=True)
        uncapped = self._score(market_commentary=False)
        assert capped.band == "LOW"
        assert capped.score < 0.35
        assert capped.score <= uncapped.score
        # Prove the cap actually demoted something — same inputs, un-flagged,
        # land above the LOW band.
        assert uncapped.band != "LOW"


# ---------------------------------------------------------------------------
# B. word-boundary truncation
# ---------------------------------------------------------------------------


class TestTruncateProse:
    def test_no_midword_cut(self):
        text = (
            "Worth reviewing — Adani Power boosts earnings visibility with "
            "long-term power purchase agreements and a substantial capacity "
            "expansion programme that strengthens its multi-year generation "
            "pipeline across several Indian states and regional grids beyond."
        )
        out = _truncate_prose(text, 280)
        assert len(out) <= 281
        assert "expan " not in out and not out.endswith("expan")
        # ends on a sentence terminator or a deliberate ellipsis
        assert out.endswith(("…", ".", "!", "?"))
        # never a dangling partial word followed by nothing
        assert "  " not in out

    def test_short_text_unchanged(self):
        assert _truncate_prose("Short summary.", 280) == "Short summary."

    def test_empty(self):
        assert _truncate_prose("", 280) == ""


# ---------------------------------------------------------------------------
# F. model override env
# ---------------------------------------------------------------------------


class TestModelOverrideEnv:
    def test_reasoning_model_env_override(self, monkeypatch):
        monkeypatch.setenv("SNOWKAP_REASONING_MODEL", "anthropic/claude-opus-4.6")
        assert resolve_model("reasoning_heavy") == "anthropic/claude-opus-4.6"

    def test_reasoning_env_does_not_affect_other_classes(self, monkeypatch):
        monkeypatch.setenv("SNOWKAP_REASONING_MODEL", "anthropic/claude-opus-4.6")
        # extraction is unaffected by the reasoning-specific override
        assert resolve_model("extraction") != "anthropic/claude-opus-4.6"

    def test_llm_model_env_overrides_all(self, monkeypatch):
        monkeypatch.delenv("SNOWKAP_REASONING_MODEL", raising=False)
        monkeypatch.setenv("SNOWKAP_LLM_MODEL", "gpt-4.1")
        assert resolve_model("reasoning_heavy") == "gpt-4.1"
        assert resolve_model("composition") == "gpt-4.1"

    def test_explicit_override_beats_env(self, monkeypatch):
        monkeypatch.setenv("SNOWKAP_REASONING_MODEL", "anthropic/claude-opus-4.6")
        assert resolve_model("reasoning_heavy", override="gpt-4o") == "gpt-4o"

    def test_no_env_unchanged_default(self, monkeypatch):
        monkeypatch.delenv("SNOWKAP_REASONING_MODEL", raising=False)
        monkeypatch.delenv("SNOWKAP_LLM_MODEL", raising=False)
        # default path: reasoning_heavy resolves to a non-empty model string
        assert resolve_model("reasoning_heavy")


# ---------------------------------------------------------------------------
# Deterministic end-to-end: the REAL (keyword-based, no-LLM) event classifier
# must tag a markets listicle as a non-actionable event, so is_market_commentary
# flags it and the gate/cap fire. Validates the upstream assumption the whole
# fix rests on, without any LLM call.
# ---------------------------------------------------------------------------


class TestEndToEndClassification:
    def test_listicle_classifies_non_actionable_and_is_flagged(self):
        from engine.nlp.event_classifier import classify_event
        from engine.analysis.criticality_scorer import ACTIONABLE_EVENT_TYPES

        ev = classify_event(
            _LISTICLE,
            "Adani Power and NTPC are both among India's largest power "
            "producers. This piece weighs the two stocks across seven factors "
            "an investor should consider before deciding which is the better "
            "long-term holding for a portfolio.",
        )
        assert ev.event_id not in ACTIONABLE_EVENT_TYPES
        assert is_market_commentary(_result(_LISTICLE, ev.event_id)) is True

    def test_genuine_tender_not_flagged_as_commentary(self):
        from engine.nlp.event_classifier import classify_event

        ev = classify_event(
            _REAL_EVENT,
            "Adani Power has won a 5,000 crore solar tender awarded by SECI, "
            "securing a long-term contract to supply power.",
        )
        # No comparison framing in the headline + (likely) an actionable event →
        # never treated as market commentary.
        assert is_market_commentary(_result(_REAL_EVENT, ev.event_id)) is False
