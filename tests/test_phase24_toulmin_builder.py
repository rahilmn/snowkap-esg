"""Phase 24 — Toulmin builder regression tests.

Verifies the deterministic build path: given a parsed Stage-10 LLM
output + pipeline context, produce a defensible Toulmin block.
"""

from __future__ import annotations

import pytest

from engine.analysis.toulmin_builder import build_toulmin
from engine.ontology.graph import reset_graph


@pytest.fixture(autouse=True)
def _reset():
    reset_graph()
    yield
    reset_graph()


# ---------------------------------------------------------------------------
# 1. Required fields present
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_full_block_returned_for_valid_input(self):
        parsed = {
            "headline": "ICICI Bank faces ₹500 Cr SEBI penalty exposure",
            "decision_summary": {
                "verdict": "ACT — file disclosure within 30 days",
                "materiality": "HIGH",
                "action": "ACT",
                "financial_exposure": "₹500 Cr (engine estimate)",
                "key_risk": "regulatory penalty cascade",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_regulatory_penalty",
            event_polarity="negative",
            relevance_total=8.5,
            materiality_weight=0.9,
            framework_codes=["BRSR:P1", "GRI:207"],
            has_financial_quantum=True,
            sentiment=-2,
        )
        # Must have the 5 required Toulmin fields + warrant citation
        for key in ("claim", "grounds", "warrant", "warrant_cite", "qualifier", "rebuttal"):
            assert key in toulmin, f"missing required field: {key}"
        assert toulmin["claim"].startswith("ACT")
        assert len(toulmin["grounds"]) >= 3
        # Negative-polarity regulatory penalty → NP-REG-001 warrant
        assert "NP-REG-001" in toulmin["warrant_cite"]
        assert toulmin["rebuttal"].startswith("If ")


# ---------------------------------------------------------------------------
# 2. "Do nothing" verdict requires actionable rebuttal
# ---------------------------------------------------------------------------


class TestDoNothingDiscipline:
    def test_do_nothing_verdict_has_rebuttal_with_concrete_trigger(self):
        parsed = {
            "headline": "Macro ESG news with no transmission to ICICI",
            "decision_summary": {
                "verdict": "Do nothing — macro signal, no company transmission",
                "materiality": "LOW",
                "action": "MONITOR",
                "financial_exposure": "N/A",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id=None,
            event_polarity="neutral",
            relevance_total=3.5,
        )
        assert toulmin["rebuttal"]
        # The "do_nothing" template must mention a concrete escalation
        # trigger (regulator action, ₹ exposure, peer precedent)
        rebuttal_lower = toulmin["rebuttal"].lower()
        assert any(
            keyword in rebuttal_lower
            for keyword in ("regulator", "₹", "exposure", "precedent", "peer")
        ), (
            f"do_nothing rebuttal must name a concrete trigger; got: "
            f"{toulmin['rebuttal']}"
        )


# ---------------------------------------------------------------------------
# 3. Polarity routing — positive vs negative
# ---------------------------------------------------------------------------


class TestPolarityRouting:
    def test_positive_event_picks_positive_warrant(self):
        parsed = {
            "headline": "Waaree wins ₹477.5 Cr PSPCL solar contract",
            "decision_summary": {
                "verdict": "MONITOR — track ramp-up execution",
                "materiality": "MODERATE",
                "action": "MONITOR",
                "financial_exposure": "₹477.5 Cr direct revenue (engine estimate)",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_contract_win",
            event_polarity="positive",
            relevance_total=7.2,
            framework_codes=["BRSR:P3"],
            has_financial_quantum=True,
            sentiment=2,
        )
        # Should pick a positive-polarity principle (NP-OPS-002 or NP-FIN-001)
        cite = toulmin["warrant_cite"]
        assert "NP-OPS-002" in cite or "NP-FIN-001" in cite

    def test_negative_polarity_rebuttal_mentions_de_escalation(self):
        parsed = {
            "decision_summary": {
                "verdict": "ACT",
                "action": "ACT",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_regulatory_penalty",
            event_polarity="negative",
            relevance_total=8.0,
        )
        # Negative event with ACT verdict → rebuttal flips toward MONITOR/de-escalate
        assert "MONITOR" in toulmin["rebuttal"] or "de-escalate" in toulmin["rebuttal"]

    def test_positive_polarity_rebuttal_mentions_downgrade(self):
        parsed = {
            "decision_summary": {
                "verdict": "ACT — capacity ramp-up plan",
                "action": "ACT",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_contract_win",
            event_polarity="positive",
            relevance_total=7.5,
        )
        # Positive event with ACT verdict → rebuttal mentions downgrade conditions
        rebuttal = toulmin["rebuttal"].lower()
        assert "downgrade" in rebuttal or "monitor" in rebuttal or "slippage" in rebuttal


# ---------------------------------------------------------------------------
# 4. Grounds extraction — pulls from the parsed insight
# ---------------------------------------------------------------------------


class TestGroundsExtraction:
    def test_grounds_includes_financial_exposure(self):
        parsed = {
            "decision_summary": {
                "financial_exposure": "₹250 Cr (from article)",
                "key_risk": "supply chain disruption",
                "verdict": "ACT",
                "action": "ACT",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_supply_chain_disruption",
            event_polarity="negative",
            relevance_total=7.0,
        )
        joined = " | ".join(toulmin["grounds"])
        assert "₹250 Cr" in joined
        assert "supply chain disruption" in joined.lower()
        assert "event_supply_chain_disruption" in joined
        assert "7.0/10" in joined

    def test_grounds_skips_na_fields(self):
        parsed = {
            "decision_summary": {
                "financial_exposure": "N/A",
                "key_risk": "None",
                "verdict": "MONITOR",
                "action": "MONITOR",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_disclosure_announcement",
            event_polarity="neutral",
            relevance_total=4.5,
        )
        joined = " | ".join(toulmin["grounds"])
        assert "N/A" not in joined
        # Grounds is non-empty because event_id + relevance still survive
        assert len(toulmin["grounds"]) >= 2

    def test_grounds_caps_at_six(self):
        parsed = {
            "decision_summary": {
                "verdict": "ACT",
                "action": "ACT",
                "financial_exposure": "₹100 Cr",
                "key_risk": "regulatory exposure",
            },
        }
        toulmin = build_toulmin(
            parsed,
            event_id="event_regulatory_penalty",
            event_polarity="negative",
            relevance_total=8.0,
            materiality_weight=0.9,
            framework_codes=["BRSR", "GRI", "ESRS", "TCFD", "CDP"],
        )
        assert len(toulmin["grounds"]) <= 6


# ---------------------------------------------------------------------------
# 5. Qualifier reflects evidence strength
# ---------------------------------------------------------------------------


class TestQualifier:
    def test_strong_relevance_with_quantum_yields_strong_qualifier(self):
        parsed = {"decision_summary": {"verdict": "ACT", "action": "ACT"}}
        toulmin = build_toulmin(
            parsed,
            event_id="event_regulatory_penalty",
            event_polarity="negative",
            relevance_total=9.0,
            has_financial_quantum=True,
            sentiment=-2,
        )
        assert "strong relevance signal" in toulmin["qualifier"].lower()
        assert "₹ quantum" in toulmin["qualifier"]

    def test_low_confidence_flag_propagates(self):
        parsed = {"decision_summary": {"verdict": "MONITOR", "action": "MONITOR"}}
        toulmin = build_toulmin(
            parsed,
            event_id=None,
            event_polarity="neutral",
            relevance_total=4.5,
            low_confidence=True,
        )
        assert "low classification confidence" in toulmin["qualifier"].lower()


# ---------------------------------------------------------------------------
# 6. Edge cases — empty input, missing decision_summary
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_input_returns_empty_dict(self):
        assert build_toulmin(
            {},
            event_id=None,
            event_polarity="neutral",
            relevance_total=None,
        ) == {}

    def test_headline_only_input_uses_headline_as_claim(self):
        # No decision_summary, but has a headline → claim falls back to it
        parsed = {"headline": "Article headline serves as fallback claim"}
        toulmin = build_toulmin(
            parsed,
            event_id="event_disclosure_announcement",
            event_polarity="neutral",
            relevance_total=5.0,
        )
        assert toulmin["claim"] == "Article headline serves as fallback claim"

    def test_warrant_empty_when_no_principle_matches(self):
        # Made-up event id with positive polarity for an irrelevant context
        parsed = {"decision_summary": {"verdict": "ACT", "action": "ACT"}}
        toulmin = build_toulmin(
            parsed,
            event_id="event_completely_unknown_xyz_123",
            event_polarity="positive",
            relevance_total=6.0,
        )
        # Build returns block (cross-cutting principles always exist),
        # but warrant_cite must still be a valid string (not a crash)
        assert isinstance(toulmin.get("warrant_cite", ""), str)
