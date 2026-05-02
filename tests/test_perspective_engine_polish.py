"""Phase 22.5 — Deep-insight panel polish (regression).

Two UX-critical fixes shipped together in this phase that ride on
``engine/analysis/perspective_engine.py``:

1. ``full_insight`` is now populated for ALL three perspectives
   (esg-analyst, cfo, ceo). Pre-fix only esg-analyst included it, so the
   in-app drill-down panel rendered empty for the CFO + CEO tabs and
   downstream renderers had to make an extra round trip to fetch the
   canonical ``DeepInsight`` sections.

2. Bullets in ``what_matters`` that lead with a negative finding
   (``No supply chain transmission``, ``No direct revenue at risk``…)
   are now stable-sorted to the BOTTOM of the list. They are still
   surfaced (we demote, never drop) but the top of the list is always
   positive/material content — executives shouldn't open a summary with
   a non-finding.

Both behaviours are pure post-processing on a ``DeepInsight``; we stub
the ontology SPARQL helpers so the tests stay deterministic and
hermetic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.analysis import perspective_engine
from engine.analysis.insight_generator import DeepInsight
from engine.analysis.perspective_engine import (
    _NEGATIVE_LEAD_PATTERNS,
    _extract_what_matters,
    _is_negative_lead,
    transform_for_perspective,
)


# ---------------------------------------------------------------------------
# Hermetic ontology stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_ontology(monkeypatch):
    """Replace the ontology SPARQL helpers with deterministic stubs.

    The polish behaviours under test live entirely in the post-processing
    layer; the SPARQL graph is irrelevant to them and slow to spin up,
    so we substitute fixed mappings that exercise both positive and
    negative-leading bullets across all six insight keys.
    """
    monkeypatch.setattr(
        perspective_engine,
        "query_perspective_impacts",
        lambda topic, perspective: ["financial", "regulatory", "strategic"],
    )
    monkeypatch.setattr(
        perspective_engine,
        "query_grid_column_map",
        lambda: {
            "financial": "financial",
            "regulatory": "regulatory",
            "strategic": "strategic",
        },
    )
    monkeypatch.setattr(
        perspective_engine,
        "query_dim_to_insight_keys",
        lambda: {
            "financial": ["valuation_cashflow", "capital_allocation"],
            "regulatory": ["compliance_regulatory"],
            "strategic": [
                "esg_positioning",
                "supply_chain_transmission",
                "people_demand",
            ],
        },
    )
    monkeypatch.setattr(
        perspective_engine,
        "get_perspective_config",
        lambda perspective: None,  # forces JSON-config fallback
    )
    monkeypatch.setattr(
        perspective_engine,
        "query_headline_rules",
        lambda perspective: [],
    )
    monkeypatch.setattr(
        perspective_engine,
        "load_perspectives",
        lambda: {
            "esg-analyst": {},
            "cfo": {"max_words": 240},
            "ceo": {"max_words": 240},
        },
    )


def _insight(impact_analysis: dict[str, str] | None = None) -> DeepInsight:
    """Minimal DeepInsight fixture with all post-processing inputs filled."""
    return DeepInsight(
        headline="SEBI penalty pressures FY26 margins",
        impact_score=7.4,
        core_mechanism="Regulatory enforcement narrows working-capital headroom.",
        profitability_connection="₹275 Cr direct hit + 35 bps spread widening.",
        translation="A regulator penalty creates near-term cash and rating pressure.",
        impact_analysis=impact_analysis
        if impact_analysis is not None
        else {
            "valuation_cashflow": "₹275 Cr revenue at risk over FY26.",
            "capital_allocation": "FII outflows widen bond spreads ~35 bps.",
            "compliance_regulatory": "BRSR P5 deadline 2026-05-30; ₹50 Cr penalty risk.",
            "esg_positioning": "MSCI ESG downgrade to BB likely next review.",
            "supply_chain_transmission": "No supply chain transmission expected.",
            "people_demand": "No people-demand impact for this event.",
        },
        decision_summary={
            "materiality": "HIGH",
            "action": "ACT",
            "verdict": "Engage with regulator within 30 days.",
            "financial_exposure": "₹275 Cr",
            "key_risk": "SEBI enforcement escalation",
            "top_opportunity": "Issue ₹500 Cr green bond",
            "timeline": "next quarterly review",
        },
        financial_timeline={
            "immediate": {"revenue_at_risk": "₹275 Cr"},
            "structural": {"competitive_position": "neutral vs peers"},
        },
        esg_relevance_score={
            "financial_materiality": {"score": 8},
            "regulatory_exposure": {"score": 7},
            "environment": {"score": 5},
            "social": {"score": 4},
            "governance": {"score": 6},
            "stakeholder_impact": {"score": 5},
        },
    )


def _result() -> MagicMock:
    """Stub PipelineResult — only ``themes.primary_theme`` is read."""
    result = MagicMock()
    result.themes = MagicMock(primary_theme="governance_failure")
    return result


# ---------------------------------------------------------------------------
# Phase 22.5 fix #1 — full_insight populated for ALL perspectives
# ---------------------------------------------------------------------------


class TestFullInsightOnAllPerspectives:
    """Pre-fix only esg-analyst included ``full_insight``. CFO + CEO panels
    in the deep-insight drill-down rendered empty as a result."""

    @pytest.mark.parametrize("perspective", ["esg-analyst", "cfo", "ceo"])
    def test_full_insight_is_populated(self, stub_ontology, perspective):
        out = transform_for_perspective(_insight(), _result(), perspective)
        assert out.full_insight is not None, (
            f"{perspective}: full_insight must be populated for the drill-down"
        )
        assert isinstance(out.full_insight, dict)
        assert out.full_insight, f"{perspective}: full_insight is empty"

    @pytest.mark.parametrize("perspective", ["esg-analyst", "cfo", "ceo"])
    def test_full_insight_contains_canonical_sections(
        self, stub_ontology, perspective
    ):
        # The drill-down panel renders impact_analysis, decision_summary,
        # financial_timeline + esg_relevance_score directly — they must
        # round-trip through transform_for_perspective for every lens.
        out = transform_for_perspective(_insight(), _result(), perspective)
        for key in (
            "headline",
            "impact_analysis",
            "decision_summary",
            "financial_timeline",
            "esg_relevance_score",
        ):
            assert key in out.full_insight, (
                f"{perspective}: full_insight missing canonical key '{key}'"
            )

    def test_full_insight_round_trips_decision_summary(self, stub_ontology):
        out = transform_for_perspective(_insight(), _result(), "cfo")
        ds = out.full_insight["decision_summary"]
        assert ds["financial_exposure"] == "₹275 Cr"
        assert ds["materiality"] == "HIGH"


# ---------------------------------------------------------------------------
# Phase 22.5 fix #2 — negative-leading bullets demoted to the bottom
# ---------------------------------------------------------------------------


class TestNegativeLeadDemotion:
    """Bullets matching ``_NEGATIVE_LEAD_PATTERNS`` (e.g.
    "No supply chain transmission…") must appear AFTER positive bullets,
    never first, so executive summaries don't open with a non-finding."""

    def test_is_negative_lead_recognises_each_pattern(self):
        for pattern in _NEGATIVE_LEAD_PATTERNS:
            sample = f"{pattern} for this event."
            assert _is_negative_lead(sample), (
                f"Expected '{sample}' to be flagged as negative-leading"
            )

    def test_is_negative_lead_is_case_insensitive(self):
        assert _is_negative_lead("NO SUPPLY CHAIN transmission expected.")
        assert _is_negative_lead("No Direct revenue at risk.")

    def test_positive_bullet_not_flagged(self):
        assert not _is_negative_lead("₹275 Cr revenue at risk over FY26.")
        assert not _is_negative_lead("MSCI ESG downgrade to BB likely.")
        assert not _is_negative_lead("BRSR P5 deadline 2026-05-30.")

    def test_extract_what_matters_demotes_first_position_negative(
        self, stub_ontology
    ):
        # Construct an insight where the FIRST priority key (valuation_cashflow,
        # the head of the financial dim) leads negative. Without the demotion
        # fix that bullet would surface as #1; with the fix it sinks to the
        # bottom and the positive bullet rises.
        insight = _insight(
            impact_analysis={
                "valuation_cashflow": "No direct revenue at risk for this event.",
                "capital_allocation": "₹275 Cr bond spread widening over FY26.",
            }
        )
        bullets = _extract_what_matters(
            insight, active_dims={"financial"}, max_items=2,
        )
        assert len(bullets) == 2
        assert "₹275 Cr" in bullets[0], (
            f"Positive bullet must lead, got: {bullets!r}"
        )
        assert _is_negative_lead(bullets[1]), (
            f"Negative bullet must be demoted to the end, got: {bullets!r}"
        )

    def test_extract_what_matters_keeps_negatives_when_no_positives(
        self, stub_ontology
    ):
        # We DEMOTE, never DROP — if every candidate bullet is
        # negative-leading they should still be returned in priority order.
        only_negative = {
            "valuation_cashflow": "No material valuation impact.",
            "compliance_regulatory": "No regulatory exposure for this event.",
            "esg_positioning": "No impact on ESG positioning.",
        }
        bullets = _extract_what_matters(
            _insight(impact_analysis=only_negative),
            active_dims={"financial", "regulatory", "strategic"},
            max_items=3,
        )
        assert len(bullets) == 3
        assert all(_is_negative_lead(b) for b in bullets)

    def test_extract_what_matters_no_positive_appears_after_negative(
        self, stub_ontology
    ):
        # Stronger contract: across the full priority list, once we hit
        # a negative bullet every subsequent bullet must also be negative.
        bullets = _extract_what_matters(
            _insight(),
            active_dims={"financial", "regulatory", "strategic"},
            max_items=6,
        )
        assert bullets, "Expected at least one bullet"
        seen_negative = False
        for bullet in bullets:
            is_neg = _is_negative_lead(bullet)
            if seen_negative:
                assert is_neg, (
                    f"Positive bullet '{bullet}' followed a negative one in {bullets!r}"
                )
            seen_negative = seen_negative or is_neg

    def test_transform_for_perspective_lead_bullet_is_positive(
        self, stub_ontology
    ):
        # End-to-end: the rendered CrispOutput for the CFO lens must lead
        # with a positive/material bullet when one exists.
        insight = _insight(
            impact_analysis={
                "valuation_cashflow": "No direct revenue at risk for this event.",
                "capital_allocation": "₹275 Cr bond spread widening over FY26.",
                "compliance_regulatory": "BRSR P5 ₹50 Cr penalty risk.",
            }
        )
        out = transform_for_perspective(insight, _result(), "cfo")
        assert out.what_matters, "Expected non-empty what_matters"
        assert not _is_negative_lead(out.what_matters[0]), (
            f"First what_matters bullet must be positive, got: {out.what_matters!r}"
        )
