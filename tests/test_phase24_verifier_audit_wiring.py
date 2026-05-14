"""Phase 24 — output_verifier → audit log integration regression.

Verifies that ``verify_and_correct`` mirrors fire-events to
``data/audit/decision_log.jsonl`` when an article context is provided,
and stays silent when it isn't (back-compat with legacy callers).

Each fire-point gets one assertion:
  * hallucination audit fired → `hallucination_audit_fired` entry
  * narrative coherence downgrade → `materiality_downgrade` +
    `coherence_warning_applied` entries
  * low-confidence classification → `low_confidence_classification` entry
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine import audit
from engine.analysis.output_verifier import verify_and_correct
from engine.ontology.graph import reset_graph


@pytest.fixture(autouse=True)
def _reset():
    reset_graph()
    yield
    reset_graph()


@pytest.fixture
def patched_audit_dir(monkeypatch, tmp_path):
    """Redirect engine.audit writers to a tmp dir so tests don't pollute
    the real data/audit/ on disk."""
    monkeypatch.setattr(
        audit, "_resolve_audit_dir",
        lambda base_data_dir=None: (tmp_path / "audit").resolve()
            and (tmp_path / "audit").mkdir(parents=True, exist_ok=True)
            or (tmp_path / "audit").resolve(),
    )
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Back-compat — no article_id/company_slug → no audit writes
# ---------------------------------------------------------------------------


class TestBackCompat:
    def test_legacy_caller_no_audit_writes(self, patched_audit_dir: Path):
        # Article that would trigger hallucination audit
        deep_insight = {
            "headline": "Test ₹500 Cr (from article)",
            "decision_summary": {
                "verdict": "ACT",
                "action": "ACT",
                "financial_exposure": "₹500 Cr (from article)",
                "key_risk": "regulatory",
            },
        }
        # No article_id / company_slug → no audit writes
        verify_and_correct(
            deep_insight,
            revenue_cr=50000,
            article_excerpts=["Plain article text with no rupee figures."],
        )
        # Audit dir should be empty (or not exist)
        log = patched_audit_dir / "audit" / "decision_log.jsonl"
        assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# 2. Hallucination audit fires → decision log entry written
# ---------------------------------------------------------------------------


class TestHallucinationAudit:
    def test_unsupported_from_article_emits_decision_entry(self, patched_audit_dir: Path):
        deep_insight = {
            "headline": "ICICI ₹500 Cr exposure",
            "decision_summary": {
                "verdict": "ACT — file disclosure",
                "materiality": "HIGH",
                "action": "ACT",
                "financial_exposure": "₹500 Cr (from article)",
                "key_risk": "regulatory cascade",
            },
        }
        # Article excerpts contain no ₹ figures → audit fires + downgrades
        verify_and_correct(
            deep_insight,
            revenue_cr=50000,
            article_excerpts=["Article body has no rupee figures at all."],
            article_id="art-h-1",
            company_slug="icici-bank",
        )
        entries = list(audit.read_decision_log(patched_audit_dir))
        types = {e["decision_type"] for e in entries}
        assert "hallucination_audit_fired" in types
        h_entry = next(e for e in entries if e["decision_type"] == "hallucination_audit_fired")
        assert h_entry["article_id"] == "art-h-1"
        assert h_entry["company_slug"] == "icici-bank"
        assert h_entry["extra"]["unsupported_claims_downgraded"] >= 1


# ---------------------------------------------------------------------------
# 3. Coherence downgrade → both materiality_downgrade + coherence entries
# ---------------------------------------------------------------------------


class TestCoherenceWiring:
    def test_positive_event_with_high_materiality_emits_downgrade(
        self, patched_audit_dir: Path
    ):
        # Positive event (contract_win) with HIGH materiality — Phase 12.4
        # narrative-coherence verifier will downgrade to MODERATE.
        deep_insight = {
            "headline": "Waaree wins ₹477 Cr contract",
            "decision_summary": {
                "verdict": "ACT",
                "materiality": "HIGH",
                "action": "ACT",
                "financial_exposure": "₹477 Cr (engine estimate)",
                # Need >20 char key_risk for the coherence verifier to mark
                # this as a negative-polarity insight (then mismatch with
                # the positive event_id and downgrade materiality).
                "key_risk": "₹50 Cr SEBI penalty risk if disclosure deadlines slip on the contract execution timeline",
            },
        }
        verify_and_correct(
            deep_insight,
            revenue_cr=5000,
            article_excerpts=["Waaree wins ₹477 Cr PSPCL solar contract."],
            event_id="event_contract_win",
            nlp_sentiment=2,
            article_id="art-c-1",
            company_slug="waaree-energies",
        )
        entries = list(audit.read_decision_log(patched_audit_dir))
        types = [e["decision_type"] for e in entries]
        # Both should be present (one materiality_downgrade, one coherence_warning)
        assert "materiality_downgrade" in types
        assert "coherence_warning_applied" in types
        m_entry = next(e for e in entries if e["decision_type"] == "materiality_downgrade")
        assert m_entry["before"]["materiality"] == "HIGH"
        assert m_entry["after"]["materiality"] in {"MODERATE", "LOW"}
        c_entry = next(e for e in entries if e["decision_type"] == "coherence_warning_applied")
        assert c_entry["extra"]["event_id"] == "event_contract_win"


# ---------------------------------------------------------------------------
# 4. Low-confidence flag → low_confidence_classification entry
# ---------------------------------------------------------------------------


class TestLowConfidenceWiring:
    def test_no_keywords_neutral_sentiment_emits_lc_entry(self, patched_audit_dir: Path):
        # Theme-fallback event (no keywords matched) + neutral sentiment
        # + no financial quantum → Phase 13 S4 sets low_confidence flag.
        deep_insight = {
            "headline": "Generic ESG news",
            "decision_summary": {
                "verdict": "MONITOR",
                "materiality": "MODERATE",
                "action": "MONITOR",
                "financial_exposure": "N/A",
                "key_risk": "unspecified",
            },
        }
        verify_and_correct(
            deep_insight,
            revenue_cr=50000,
            article_excerpts=["Generic ESG news with no specific figures."],
            event_id="event_disclosure_announcement",
            nlp_sentiment=0,
            event_matched_keywords=[],  # no keyword matches → theme fallback
            has_financial_quantum=False,
            article_id="art-lc-1",
            company_slug="icici-bank",
        )
        # Low-confidence MAY downgrade materiality too — both decision types
        # are valid expected emissions. Assert at least the lc entry fired.
        entries = list(audit.read_decision_log(patched_audit_dir))
        types = {e["decision_type"] for e in entries}
        assert "low_confidence_classification" in types, (
            f"expected low_confidence_classification, got {types}"
        )
