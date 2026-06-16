"""Phase 1 acceptance tests for the Criticality Scorer (`engine/analysis/criticality_scorer.py`).

The 6 cases in §3.8 of the enhancement plan, plus a handful of unit tests
on individual components so the scorer fails fast when one math-rule drifts.

Run:
    python -m pytest tests/test_criticality.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.analysis.criticality_scorer import (
    BAND_THRESHOLDS,
    WEIGHTS_BY_ROLE,
    WEIGHTS_DEFAULT,
    CriticalityComponents,
    score,
    score_components,
    set_source_authority_overrides,
    _band_for,
    _cosine,
    _financial_magnitude_component,
    _materiality_component,
    _polarity_drift_penalty,
    _recency_component,
    _staleness_penalty,
)


# Fixed reference time so tests are deterministic.
NOW = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_authority():
    """Each test starts with a clean overrides dict so test order doesn't matter."""
    set_source_authority_overrides({})
    yield
    set_source_authority_overrides({})


# ---------------------------------------------------------------------------
# §3.8 acceptance cases
# ---------------------------------------------------------------------------


def test_case_1_adani_50cr_one_time_gain_must_score_below_0_40():
    """§3.8 case 1: ₹50 Cr one-time gain on ₹50,000 Cr revenue (Adani case).

    Expected: score < 0.40 (must NOT pass home-page floor of 0.65).

    Setup mirrors the live Adani Power tax-gain article that shouldn't
    have reached the email composer.
    """
    set_source_authority_overrides({"scanx.trade": 0.45})
    pub = (NOW - timedelta(days=2)).isoformat()  # fresh
    result = score(
        relevance_total=4,                    # marginal materiality
        cascade_total_cr=50.0,
        company_revenue_cr=50_000.0,          # ratio = 0.1% revenue
        event_id="event_quarterly_results",   # actionable, but not high-impact
        article_embedding=None,
        painpoint_embeddings=None,
        published_at=pub,
        source="scanx.trade",
        cascade_confidence="medium",
        event_polarity="positive",
        narrative_polarity="positive",
        now=NOW,
    )
    assert result.score < 0.40, (
        f"Adani-style article must score < 0.40, got {result.score:.3f} "
        f"(components: {result.components.as_dict()})"
    )
    assert result.band == "LOW" or result.band == "MEDIUM", (
        f"expected LOW or MEDIUM, got {result.band}"
    )


def test_case_2_waaree_1800cr_margin_hit_with_painpoint_match_must_score_above_0_70():
    """§3.8 case 2: ₹1,800 Cr margin hit on ₹26,000 Cr revenue WITH painpoint match.

    Expected: score > 0.70.
    """
    set_source_authority_overrides({"reuters": 1.0, "bloomberg": 1.0})
    pub = (NOW - timedelta(days=1)).isoformat()
    # Create a painpoint embedding that matches the article perfectly.
    article_emb = [1.0, 0.0, 0.0, 0.0]
    painpoints = [(article_emb, 0.95)]  # severity 0.95, perfect cosine = 1.0
    result = score(
        relevance_total=8,                    # high materiality
        cascade_total_cr=1_800.0,
        company_revenue_cr=26_000.0,          # ratio = 6.9% revenue
        event_id="event_litigation_initiated", # actionable
        article_embedding=article_emb,
        painpoint_embeddings=painpoints,
        published_at=pub,
        source="reuters",
        cascade_confidence="high",
        event_polarity="negative",
        narrative_polarity="negative",
        now=NOW,
    )
    assert result.score > 0.70, (
        f"Waaree-style article with painpoint match must score > 0.70, "
        f"got {result.score:.3f} (components: {result.components.as_dict()})"
    )
    assert result.band in ("CRITICAL", "HIGH"), (
        f"expected CRITICAL or HIGH, got {result.band}"
    )


def test_case_3_60_day_old_no_painpoint_match_must_score_below_0_30():
    """§3.8 case 3: article 60 days old, no painpoint match. Staleness penalty fires.

    Expected: score < 0.30.
    """
    set_source_authority_overrides({"economictimes.indiatimes.com": 0.85})
    pub = (NOW - timedelta(days=60)).isoformat()
    result = score(
        relevance_total=5,                    # decent materiality
        cascade_total_cr=200.0,
        company_revenue_cr=10_000.0,          # ratio = 2% revenue
        event_id="event_quarterly_results",
        article_embedding=None,
        painpoint_embeddings=None,
        published_at=pub,
        source="economictimes.indiatimes.com",
        cascade_confidence="medium",
        event_polarity="neutral",
        narrative_polarity="neutral",
        now=NOW,
    )
    assert result.components.staleness_penalty > 0, (
        f"staleness penalty must fire on 60-day-old article, got "
        f"{result.components.staleness_penalty}"
    )
    assert result.score < 0.30, (
        f"Stale article with no painpoint match must score < 0.30, got "
        f"{result.score:.3f} (components: {result.components.as_dict()})"
    )


def test_case_4_stale_high_authority_must_not_pass_floor_of_0_65():
    """§3.8 case 4: article from 60 days ago, high-authority source.

    Expected: score < 0.65 (must NOT pass home-page floor).
    """
    set_source_authority_overrides({"reuters": 1.0})
    pub = (NOW - timedelta(days=60)).isoformat()
    result = score(
        relevance_total=7,
        cascade_total_cr=500.0,
        company_revenue_cr=20_000.0,
        event_id="event_regulatory_filing",
        article_embedding=None,
        painpoint_embeddings=None,
        published_at=pub,
        source="reuters",
        cascade_confidence="high",
        event_polarity="neutral",
        narrative_polarity="neutral",
        now=NOW,
    )
    assert result.score < 0.65, (
        f"Stale article must not pass floor 0.65, got {result.score:.3f} "
        f"(components: {result.components.as_dict()})"
    )


def test_case_5_litigation_named_regulator_must_have_actionability_at_least_0_8():
    """§3.8 case 5: litigation event with named regulator → actionability ≥ 0.8."""
    set_source_authority_overrides({"reuters": 1.0})
    pub = NOW.isoformat()
    result = score(
        relevance_total=6,
        cascade_total_cr=300.0,
        company_revenue_cr=15_000.0,
        event_id="event_litigation_initiated",  # in ACTIONABLE_EVENT_TYPES
        article_embedding=None,
        painpoint_embeddings=None,
        published_at=pub,
        source="reuters",
        cascade_confidence="medium",
        now=NOW,
    )
    assert result.components.actionability >= 0.8, (
        f"Litigation event must have actionability >= 0.8, got "
        f"{result.components.actionability}"
    )


def test_case_6_polarity_drift_penalty_fires_when_event_pos_narrative_neg():
    """§3.8 case 6: positive event, negative narrative → polarity_drift_penalty fires."""
    set_source_authority_overrides({"reuters": 1.0})
    pub = NOW.isoformat()
    result = score(
        relevance_total=6,
        cascade_total_cr=100.0,
        company_revenue_cr=10_000.0,
        event_id="event_contract_win",
        article_embedding=None,
        painpoint_embeddings=None,
        published_at=pub,
        source="reuters",
        cascade_confidence="medium",
        event_polarity="positive",
        narrative_polarity="negative",
        now=NOW,
    )
    assert result.components.polarity_drift_penalty == 0.2, (
        f"Polarity drift must fire (penalty = 0.2), got "
        f"{result.components.polarity_drift_penalty}"
    )


# ---------------------------------------------------------------------------
# Component unit tests — keep math from drifting silently
# ---------------------------------------------------------------------------


class TestMaterialityComponent:
    def test_relevance_10_returns_1(self):
        assert _materiality_component(10) == 1.0

    def test_relevance_5_returns_0_5(self):
        assert _materiality_component(5) == 0.5

    def test_relevance_0_returns_0(self):
        assert _materiality_component(0) == 0.0

    def test_none_returns_0(self):
        assert _materiality_component(None) == 0.0

    def test_clips_above_1(self):
        assert _materiality_component(15) == 1.0


class TestFinancialMagnitudeComponent:
    def test_one_pct_revenue_about_0_5(self):
        # ratio = 1%, log10(1+1)/2 = 0.1505 — actually ~0.15, not 0.5
        # The plan's example is wrong — let me check: log10(1+100*0.01)/2
        # = log10(2)/2 = 0.301/2 = 0.1505
        # Plan says "1% → ~0.5" which would require log10(1+1*100)/2 = 1.005 — no.
        # Plan formula: log10(1 + cascade/revenue * 100) / 2
        # 1% revenue → cascade/revenue = 0.01 → 0.01*100 = 1 → log10(2)/2 = 0.15
        # 10% revenue → 100*0.1=10 → log10(11)/2 ≈ 0.52
        # 100% revenue → log10(101)/2 ≈ 1.0
        # So plan's "1% → 0.5" is wrong — it's actually 10% → ~0.5.
        # We test the math, not the plan's commentary.
        v = _financial_magnitude_component(100, 10_000)  # 1% revenue
        assert 0.10 <= v <= 0.20, f"got {v}"

    def test_ten_pct_revenue_about_0_5(self):
        v = _financial_magnitude_component(1_000, 10_000)  # 10% revenue
        assert 0.45 <= v <= 0.60, f"got {v}"

    def test_100_pct_revenue_clips_to_1(self):
        v = _financial_magnitude_component(10_000, 10_000)  # 100% revenue
        assert v >= 0.99

    def test_zero_revenue_returns_0(self):
        assert _financial_magnitude_component(100, 0) == 0.0

    def test_zero_cascade_returns_0(self):
        assert _financial_magnitude_component(0, 10_000) == 0.0


class TestRecencyComponent:
    def test_today_returns_1(self):
        v = _recency_component(NOW.isoformat(), now=NOW)
        assert v >= 0.99

    def test_seven_days_old_returns_about_0_37(self):
        pub = (NOW - timedelta(days=7)).isoformat()
        v = _recency_component(pub, now=NOW)
        # exp(-1) ≈ 0.367
        assert 0.35 <= v <= 0.40

    def test_thirty_days_old_returns_close_to_0(self):
        pub = (NOW - timedelta(days=30)).isoformat()
        v = _recency_component(pub, now=NOW)
        assert v < 0.05

    def test_missing_returns_0_5(self):
        assert _recency_component(None) == 0.5


class TestStalenessPenalty:
    def test_under_30_days_no_penalty(self):
        pub = (NOW - timedelta(days=29)).isoformat()
        assert _staleness_penalty(pub, now=NOW) == 0.0

    def test_over_30_days_fires(self):
        pub = (NOW - timedelta(days=31)).isoformat()
        assert _staleness_penalty(pub, now=NOW) == 0.2

    def test_missing_no_penalty(self):
        assert _staleness_penalty(None) == 0.0


class TestPolarityDriftPenalty:
    def test_pos_event_neg_narrative_fires(self):
        assert _polarity_drift_penalty("positive", "negative") == 0.2

    def test_neg_event_pos_narrative_fires(self):
        assert _polarity_drift_penalty("negative", "positive") == 0.2

    def test_matching_polarities_no_penalty(self):
        assert _polarity_drift_penalty("positive", "positive") == 0.0
        assert _polarity_drift_penalty("negative", "negative") == 0.0

    def test_neutral_no_penalty(self):
        assert _polarity_drift_penalty("neutral", "negative") == 0.0
        assert _polarity_drift_penalty("positive", "neutral") == 0.0


class TestCosine:
    def test_perfect_match(self):
        assert _cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal(self):
        assert _cosine([1, 0], [0, 1]) == 0.0

    def test_zero_vector(self):
        assert _cosine([0, 0], [1, 1]) == 0.0


class TestBandThresholds:
    def test_critical_at_0_75(self):
        assert _band_for(0.75) == "CRITICAL"
        assert _band_for(0.80) == "CRITICAL"

    def test_high_below_0_75(self):
        assert _band_for(0.74) == "HIGH"
        assert _band_for(0.55) == "HIGH"

    def test_medium_band(self):
        assert _band_for(0.54) == "MEDIUM"
        assert _band_for(0.35) == "MEDIUM"

    def test_low_below_0_35(self):
        assert _band_for(0.34) == "LOW"
        assert _band_for(0.0) == "LOW"


class TestRoleScores:
    def test_role_scores_dropped(self):
        # Phase 51.F — role-based analysis dropped: per-role criticality
        # (role_scores) is no longer computed. The deck + product use the single
        # default (materiality-led) score. Guards against re-introducing roles.
        result = score(
            relevance_total=7,
            cascade_total_cr=500.0,
            company_revenue_cr=20_000.0,
            event_id="event_quarterly_results",
            published_at=NOW.isoformat(),
            source="reuters",
            now=NOW,
        )
        assert result.role_scores == {}
        assert 0.0 <= result.score <= 1.0


class TestWeightSums:
    def test_default_weights_sum_to_1(self):
        assert sum(WEIGHTS_DEFAULT.values()) == pytest.approx(1.0, abs=1e-9)

    def test_each_role_weights_sum_to_1(self):
        for role, weights in WEIGHTS_BY_ROLE.items():
            assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9), (
                f"{role} weights sum to {sum(weights.values())}, expected 1.0"
            )
