"""Phase 51.G — ontology accuracy levers (PR #10, stacked on #8 + #9).

Two fixes, each closing a "the ontology fires but returns nothing / the deck
ignores the ontology's own verdict" gap surfaced by the ontology-trigger audit:

  F1  Topic/theme exact-match SPARQL queries (frameworks, theme-risk maps, SDG,
      stakeholders) now normalise the free-text needle through
      ``materiality_aliases.canonical_topic`` — exactly as
      ``query_materiality_weight`` already did. Near-miss LLM labels
      ("GHG Emissions" vs "Emissions") stop silently returning [].

  F2  ``criticality_scorer`` (a) treats enforcement/harm events
      (criminal_indictment, heavy_penalty, social_violation, cyber_incident)
      as ACTIONABLE, and (b) floors the materiality component with the EVENT
      TYPE's ontology severity (``EventRule.score_floor`` / 10). It does NOT use
      ``RiskAssessment.aggregate_score`` — that blend is dominated by a non-ESG
      "Market & Uncertainty" category rated HIGH on routine earnings, so
      flooring on it would re-promote the market noise PR #8 was reverted to
      avoid. Together F2a+F2b de-invert the deck *on raw merit*: a genuine
      ESG-governance event (the IDFC ₹200cr fraud, event score_floor 8) now
      out-scores a positive earnings blurb without relying on the deck's
      negativity tiebreak.

Run:
    python -m pytest tests/test_phase51g_ontology_levers.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.analysis.criticality_scorer import (
    ACTIONABLE_EVENT_TYPES,
    _materiality_component,
    score,
)

NOW = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(days=2)).isoformat()  # no staleness, high recency


# ===========================================================================
# F1 — alias normalisation on the topic/theme-keyed ontology queries
# ===========================================================================


@pytest.mark.parametrize(
    "canonical, near_miss",
    [
        ("Emissions", "GHG Emissions"),
        ("Climate Change", "Climate"),
        ("Ethics & Compliance", "fraud"),
    ],
)
def test_framework_detail_resolves_near_miss_alias(canonical, near_miss):
    """A free-text near-miss label returns the SAME (non-empty) frameworks as
    the canonical ontology label. Before F1 the near-miss collapsed to []."""
    from engine.ontology.intelligence import query_frameworks_detail

    canon = [f.label for f in query_frameworks_detail(canonical)]
    if not canon:
        pytest.skip("ontology graph not loaded with framework triples")
    alias = [f.label for f in query_frameworks_detail(near_miss)]
    assert alias == canon
    assert len(alias) > 0


def test_frameworks_for_topic_resolves_alias():
    from engine.ontology.intelligence import query_frameworks_for_topic

    canon = query_frameworks_for_topic("Climate Change")
    if not canon:
        pytest.skip("ontology graph not loaded")
    assert query_frameworks_for_topic("Climate") == canon


@pytest.mark.parametrize(
    "canonical, near_miss",
    [("Emissions", "GHG Emissions"), ("Ethics & Compliance", "corruption")],
)
def test_theme_risk_map_resolves_alias(canonical, near_miss):
    from engine.ontology.intelligence import query_theme_risk_map

    canon = query_theme_risk_map(canonical)
    if not canon:
        pytest.skip("ontology graph not loaded with risk-map triples")
    assert query_theme_risk_map(near_miss) == canon


def test_sdg_and_stakeholder_queries_resolve_alias():
    """The same helper covers the sibling SDG + stakeholder queries (same bug)."""
    from engine.ontology.intelligence import (
        query_sdgs_for_topic,
        query_stakeholders_for_topic,
    )

    sdg_canon = query_sdgs_for_topic("Emissions")
    stk_canon = query_stakeholders_for_topic("Emissions")
    if not sdg_canon and not stk_canon:
        pytest.skip("ontology graph not loaded with SDG/stakeholder triples")
    assert query_sdgs_for_topic("GHG Emissions") == sdg_canon
    assert query_stakeholders_for_topic("GHG Emissions") == stk_canon


def test_canonical_topic_is_idempotent_for_known_labels():
    """Normalising an already-canonical label must not change the result —
    F1 can never REDUCE a match that worked before."""
    from engine.ontology.intelligence import query_frameworks_detail

    canon = [f.label for f in query_frameworks_detail("Emissions")]
    if not canon:
        pytest.skip("ontology graph not loaded")
    # exact label, lower-cased, and an alias all land on the same set
    assert [f.label for f in query_frameworks_detail("emissions")] == canon


# ===========================================================================
# F2a — enforcement / harm events are ACTIONABLE
# ===========================================================================


@pytest.mark.parametrize(
    "event_id",
    [
        "event_criminal_indictment",
        "event_heavy_penalty",
        "event_social_violation",
        "event_cyber_incident",
    ],
)
def test_enforcement_events_are_actionable(event_id):
    assert event_id in ACTIONABLE_EVENT_TYPES
    result = score(
        relevance_total=6,
        cascade_total_cr=0.0,
        company_revenue_cr=None,
        event_id=event_id,
        published_at=FRESH,
        now=NOW,
    )
    assert result.components.actionability == 0.8


# ===========================================================================
# F2b — EVENT-TYPE ontology severity floors materiality
#
# Design note: F2b deliberately floors on the EVENT's ontology score_floor,
# NOT on RiskAssessment.aggregate_score. The risk aggregate is dominated by a
# non-ESG "Market & Uncertainty" category the assessor rates HIGH/CRITICAL on
# routine earnings (waaree quarterly_results → aggregate 0.58-0.64), so flooring
# on it re-promotes the market noise PR #8 was reverted to avoid. The event
# floor is self-calibrating: routine events have intrinsically low floors.
# ===========================================================================


def test_materiality_component_event_floor_unit():
    # base only (back-compat — no event severity passed)
    assert _materiality_component(6.0) == pytest.approx(0.6)
    # event severity above the relevance-derived base lifts it
    # (criminal_indictment score_floor 8/10)
    assert _materiality_component(6.0, 0.8) == pytest.approx(0.8)
    # event severity below the base leaves it untouched (MAX, not sum)
    # (quarterly_results score_floor 3/10)
    assert _materiality_component(6.0, 0.3) == pytest.approx(0.6)
    # event severity can carry a zero-relevance article
    assert _materiality_component(None, 0.8) == pytest.approx(0.8)
    # both absent → 0
    assert _materiality_component(None, None) == 0.0
    # clipped to 1.0
    assert _materiality_component(6.0, 1.5) == 1.0


def test_event_severity_none_is_backcompat():
    """Existing callers that never pass event_severity must see the unchanged
    relevance/10 materiality (protects the 1.7k-test baseline)."""
    result = score(
        relevance_total=6,
        cascade_total_cr=0.0,
        company_revenue_cr=None,
        event_id="event_quarterly_results",
        published_at=FRESH,
        now=NOW,
    )
    assert result.components.materiality == pytest.approx(0.6)


def test_event_severity_floors_materiality_on_score():
    result = score(
        relevance_total=6,                 # relevance-derived materiality 0.6
        event_severity=0.8,                # criminal_indictment score_floor 8/10
        cascade_total_cr=0.0,
        company_revenue_cr=None,
        event_id="event_criminal_indictment",
        published_at=FRESH,
        now=NOW,
    )
    assert result.components.materiality == pytest.approx(0.8)


def test_integration_derives_event_severity_from_score_floor():
    """End-to-end: score_at_pipeline_end pulls the floor off result.event."""
    from types import SimpleNamespace

    from engine.analysis.criticality_integration import score_at_pipeline_end

    result = SimpleNamespace(
        relevance=SimpleNamespace(total=6.0),
        event=SimpleNamespace(
            event_id="event_criminal_indictment", score_floor=8, score_ceiling=10,
        ),
        nlp=SimpleNamespace(sentiment=-1),
        risk=None,
        title="CBI arrests in ₹200cr bank fraud", article_content="...",
        published_at=FRESH, source="reuters.com", url="https://e.com/a",
    )
    company = SimpleNamespace(revenue_cr=None, slug=None, primitive_calibration={})
    res = score_at_pipeline_end(result, company, embed_article=False)
    assert res is not None
    # floored by the event's ontology score_floor 8/10, NOT the relevance 0.6
    assert res.components.materiality == pytest.approx(0.8)


# ===========================================================================
# F2 integration — the de-inversion is now on RAW MERIT, not the negativity tiebreak
# ===========================================================================


def _score_like(relevance_total, event_severity, event_id, polarity):
    return score(
        relevance_total=relevance_total,
        event_severity=event_severity,
        cascade_total_cr=0.0,
        company_revenue_cr=None,
        event_id=event_id,
        published_at=FRESH,
        now=NOW,
        source="reuters.com",
        event_polarity=polarity,
        narrative_polarity=polarity,   # consistent → no drift penalty
    )


def test_fraud_outscores_earnings_on_raw_score():
    """A ₹200cr criminal indictment (event score_floor 8) beats a positive
    earnings blurb on RAW criticality even though the earnings article has
    HIGHER 5D relevance — because the event taxonomy floors the fraud's
    materiality. This is the de-inversion the deck's negativity tiebreak used
    to carry alone."""
    fraud = _score_like(6, 0.8, "event_criminal_indictment", "negative")
    earnings = _score_like(7, 0.3, "event_quarterly_results", "positive")
    assert fraud.components.materiality > earnings.components.materiality
    assert fraud.score > earnings.score


def test_low_severity_event_cannot_be_promoted_by_risk_REGRESSION():
    """Reviewer regression guard (the PR #8 noise-promotion failure mode):
    a NEGATIVE article whose ontology RISK aggregate is high (~0.6, e.g. driven
    by the non-ESG 'Market & Uncertainty' category on an earnings/market story)
    must NOT get its materiality lifted, because F2b floors on the EVENT type
    (here event_severity reflects a low score_floor), not on risk. A
    quarterly_results (floor 3 → 0.3) / analyst_outlook (floor 2 → 0.2) keeps
    its relevance-derived materiality regardless of how risky it looks."""
    # relevance 4 (survives the >=4 gate), negative, low event floor
    quarterly = _score_like(4, 0.3, "event_quarterly_results", "negative")
    analyst = _score_like(4, 0.2, "event_analyst_outlook", "negative")
    assert quarterly.components.materiality == pytest.approx(0.4)
    assert analyst.components.materiality == pytest.approx(0.4)
    # band must NOT be lifted into the critical range by the floor
    assert quarterly.band in ("LOW", "MEDIUM")
    assert analyst.band in ("LOW", "MEDIUM")
