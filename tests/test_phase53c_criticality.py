"""Phase 53 (C) — pipeline + criticality gates for the industry-thematic lane.

Two scorer changes let genuinely-material SECTOR ESG news (company NOT named,
arriving via the Phase 53.B thematic lane) reach the deck for a company whose
only material ESG news is sector-wide, WITHOUT regressing the 7 tuned baseline
decks or re-promoting market noise:

  1. The criticality materiality component is FLOORED by the ontology SASB
     sector × theme weight (relevance.materiality_weight) — but ONLY for
     thematic articles (company-named articles pass weight=None and are
     unchanged). Self-gating: the SASB neutral default 0.5 means a non-material
     theme can never lift a relevance-based materiality.
  2. Sector/regulatory ESG events (climate norms, disclosure obligations,
     environmental violations, …) are now ACTIONABLE (0.8 not 0.2), sourced from
     the ontology (`snowkap:actionable true`) with a complete frozenset fallback.

The upstream market-commentary LOW-cap stays the guardrail against a noise
listicle that happens to tag a material theme.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.analysis import criticality_scorer as cs
from engine.analysis.criticality_integration import _industry_materiality_for
from engine.ontology.intelligence import query_actionable_event_types


# ---------------------------------------------------------------------------
# 1. Materiality floor — thematic only, self-gating
# ---------------------------------------------------------------------------

def test_materiality_floor_lifts_only_for_material_thematic():
    # Company-named (weight None) → unchanged: relevance 4 → 0.5 (event floor 0.5)
    assert cs._materiality_component(4, 0.5, None) == pytest.approx(0.5)
    # Thematic + material theme (Climate@bank 0.95) → floored to 0.95
    assert cs._materiality_component(4, 0.5, 0.95) == pytest.approx(0.95)
    # Thematic + NON-material theme (weight 0.40 ≤ relevance/event) → no lift
    assert cs._materiality_component(4, 0.5, 0.40) == pytest.approx(0.5)
    # A strong relevance still wins over a weaker industry weight
    assert cs._materiality_component(9, None, 0.65) == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 2. Actionable ESG/regulatory events — ontology + fallback
# ---------------------------------------------------------------------------

_NEW_ACTIONABLE = (
    "event_climate_event",
    "event_regulatory_policy",
    "event_environmental_violation",
    "event_show_cause_notice",
    "event_disclosure_announcement",
    "event_systemic_regulatory",
)


@pytest.mark.parametrize("ev", _NEW_ACTIONABLE)
def test_sector_regulatory_events_are_actionable(ev):
    # in the resolved set (ontology UNION frozenset fallback)
    assert ev in cs._actionable_event_types()
    # and the component returns the actionable 0.8
    assert cs._actionability_component(ev) == pytest.approx(0.8)


def test_routine_events_stay_non_actionable():
    for ev in ("event_analyst_outlook", "event_routine_capex", "event_award_recognition"):
        assert cs._actionability_component(ev) == pytest.approx(0.2)


def test_ontology_seeds_actionable_events():
    onto = query_actionable_event_types()
    assert "event_climate_event" in onto
    assert "event_regulatory_policy" in onto


def test_fallback_when_ontology_overridden_empty():
    # Even with the ontology pinned empty, the frozenset fallback is complete.
    cs.set_actionable_event_overrides(None)  # reset cache
    try:
        # Force the union path; the literal must still carry the new events.
        assert "event_climate_event" in cs.ACTIONABLE_EVENT_TYPES
        assert "event_regulatory_policy" in cs.ACTIONABLE_EVENT_TYPES
    finally:
        cs.set_actionable_event_overrides(None)


# ---------------------------------------------------------------------------
# 3. Full score — thematic bank-climate reaches the deck; guardrails hold
# ---------------------------------------------------------------------------

def _bank_climate(**over):
    base = dict(
        relevance_total=4, event_severity=0.5, cascade_total_cr=0.0,
        company_revenue_cr=None, event_id="event_climate_event",
        published_at="2026-06-18T00:00:00Z", source="Mint",
        inferred_painpoints=["climate risk disclosure", "financed emissions"],
        article_text="RBI tightens climate risk disclosure norms for banks financed emissions",
    )
    base.update(over)
    return cs.score(**base)


def test_thematic_bank_climate_reaches_critical_band():
    company_named = _bank_climate(industry_materiality_weight=None)
    thematic = _bank_climate(industry_materiality_weight=0.95)
    # Company-named already actionable (climate event now actionable) → HIGH.
    assert company_named.band in ("HIGH", "CRITICAL")
    # Thematic with the SASB floor strictly outranks and reaches CRITICAL.
    assert thematic.score > company_named.score
    assert thematic.band == "CRITICAL"
    assert thematic.components.materiality == pytest.approx(0.95)
    assert thematic.components.actionability == pytest.approx(0.8)


def test_market_commentary_capped_low_even_with_material_floor():
    noise = cs.score(
        industry_materiality_weight=0.95, market_commentary=True,
        relevance_total=4, event_severity=0.2, cascade_total_cr=0.0,
        company_revenue_cr=None, event_id="event_analyst_outlook",
        published_at="2026-06-18T00:00:00Z", source="Moneycontrol",
        article_text="Which bank stock is a better bet",
    )
    assert noise.band == "LOW"


# ---------------------------------------------------------------------------
# 4. Integration gate — weight passed only for thematic source_type
# ---------------------------------------------------------------------------

def _result(source_type, weight):
    return SimpleNamespace(
        source_type=source_type,
        relevance=SimpleNamespace(materiality_weight=weight),
    )


def test_industry_materiality_only_for_thematic():
    rel = _result("industry_thematic", 0.95).relevance
    assert _industry_materiality_for(_result("industry_thematic", 0.95), rel) == pytest.approx(0.95)
    # company-named (default source_type) → None, scorer keeps existing behaviour
    assert _industry_materiality_for(_result("", 0.95), rel) is None
    assert _industry_materiality_for(_result("newsapi_ai", 0.95), rel) is None
    # missing weight → None (never crashes the additive path)
    assert _industry_materiality_for(_result("industry_thematic", None),
                                     SimpleNamespace(materiality_weight=None)) is None
