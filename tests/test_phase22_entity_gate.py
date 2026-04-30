"""Phase 22 — Cross-entity gate + proximity audit + polarity labels.

Surfaced from a live audit on 2026-04-29 of the Adani Energy Solutions
ESG-rating article viewed under the Adani Power dashboard. Three real
production bugs:

  22.1 Cross-entity attribution (article about Adani Energy Solutions
       got analyzed as Adani Power — confidently-wrong CEO board para)
  22.2 Hallucination audit accepted ₹500 Cr (from article) when only
       ₹503 Cr Q3 net profit was in body — different concepts
  22.3 Frontend "Margin Pressure" label shown on a positive event

Each test class corresponds to one of the bugs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.analysis.output_verifier import (
    audit_source_tags,
    _article_context_overlaps,
    _extract_claim_tokens,
)
from engine.analysis.pipeline import _detect_cross_entity
from engine.config import Company
from engine.nlp.extractor import NLPExtraction


def _adani_power() -> Company:
    return Company(
        slug="adani-power", name="Adani Power", domain="adanipower.com",
        industry="Power/Energy", sasb_category="Electric Utilities",
        market_cap="Large Cap", listing_exchange="NSE",
        headquarter_city="Ahmedabad", headquarter_country="India",
        headquarter_region="Asia-Pacific", news_queries=[],
    )


def _make_nlp(entities: list[str], sentiment: int = 0) -> NLPExtraction:
    return NLPExtraction(
        sentiment=sentiment, sentiment_confidence=0.85, tone=["neutral"],
        narrative_core_claim="", narrative_implied_causation="",
        narrative_stakeholder_framing="", entities=entities,
        entity_types={e: "company" for e in entities},
        financial_signal=None, regulatory_references=[], esg_pillar="G",
        esg_topics=[], content_type="news", urgency="medium",
        time_horizon="medium", source_credibility_tier=2, climate_events=[],
    )


# ---------------------------------------------------------------------------
# Issue 22.1 — Cross-entity gate
# ---------------------------------------------------------------------------


class TestCrossEntityGate:
    """Article about a sibling group company (e.g. Adani Energy Solutions)
    that lands on the wrong company's feed (e.g. Adani Power) must be
    rejected before downstream pipeline stages run."""

    def test_adani_energy_solutions_article_rejected_under_adani_power(self):
        # Live-fail: AES article in Adani Power feed
        nlp = _make_nlp(["Adani Energy Solutions", "CARE ESG Ratings Limited"])
        title = "Adani Energy Solutions Receives Inaugural ESG Rating Of 86.8/100"
        content = "Adani Energy Solutions has been awarded an ESG rating by CARE."
        is_cross, reason = _detect_cross_entity(nlp, title, content, _adani_power())
        assert is_cross is True
        assert "Adani Energy Solutions" in reason
        assert "Adani Power" in reason

    def test_legitimate_adani_power_article_passes(self):
        nlp = _make_nlp(["Adani Power", "Amnesty International", "Jharkhand"])
        title = "Adani Power child labor risks flagged"
        content = "Adani Power's coal supply chain in Jharkhand..."
        is_cross, reason = _detect_cross_entity(nlp, title, content, _adani_power())
        assert is_cross is False
        assert reason == ""

    def test_article_mentioning_both_entities_passes(self):
        nlp = _make_nlp(["Adani Energy Solutions", "Adani Power", "Adani Green"])
        title = "Adani Group ESG strategy update"
        content = "Adani Power, Adani Energy Solutions, and Adani Green are aligning..."
        is_cross, _ = _detect_cross_entity(nlp, title, content, _adani_power())
        assert is_cross is False  # target IS in entities AND body

    def test_jsw_steel_article_rejected_under_jsw_energy(self):
        # JSW family — "JSW Steel" article filed under JSW Energy is the
        # same kind of cross-entity error.
        jsw_energy = Company(
            slug="jsw-energy", name="JSW Energy", domain="jsw.in",
            industry="Power/Energy", sasb_category="Electric Utilities",
            market_cap="Large Cap", listing_exchange="NSE",
            headquarter_city="Mumbai", headquarter_country="India",
            headquarter_region="Asia-Pacific", news_queries=[],
        )
        nlp = _make_nlp(["JSW Steel", "Tata Steel"])
        is_cross, reason = _detect_cross_entity(
            nlp,
            "JSW Steel announces new mill",
            "JSW Steel commissions a new high-grade steel rolling mill.",
            jsw_energy,
        )
        assert is_cross is True
        assert "JSW Steel" in reason

    def test_unrelated_company_does_not_trigger_sibling_logic(self):
        # An article about a non-Adani company landing in Adani Power's
        # feed should NOT trigger the cross-entity gate (the gate only
        # fires when a sibling group company is mentioned). The relevance
        # scorer's off-topic filter handles unrelated cases separately.
        nlp = _make_nlp(["NTPC Limited", "Power Grid Corporation"])
        is_cross, reason = _detect_cross_entity(
            nlp,
            "NTPC announces 4 GW solar pipeline",
            "NTPC Limited has announced a major solar capacity addition.",
            _adani_power(),
        )
        assert is_cross is False  # not a sibling group → defer to relevance scorer

    def test_target_in_body_only_not_entities_passes(self):
        # NLP entity extraction sometimes misses the company even when
        # it's in the body. Title/body fallback should still let the
        # article through.
        nlp = _make_nlp(["SEBI", "Securities and Exchange Board of India"])
        title = "Adani Power gets ₹50 Cr SEBI demand"
        content = "Adani Power Limited has received a SEBI notice..."
        is_cross, _ = _detect_cross_entity(nlp, title, content, _adani_power())
        assert is_cross is False


# ---------------------------------------------------------------------------
# Issue 22.2 — Hallucination audit noun-phrase proximity
# ---------------------------------------------------------------------------


class TestProximityAudit:
    """audit_source_tags must require article-context to share at least
    one token with the LLM claim's descriptor — prevents the case where
    a numerical match (₹503 Cr in body, ₹500 Cr in claim) gets accepted
    even though the contexts are unrelated."""

    ARTICLE_BODY = [
        "IDFC First Bank reported Q3 net profit of Rs 503 crore, up 48% YoY. "
        "Net Interest Income grew 12% to Rs 5,492 crore. Provisions fell 12%."
    ]

    def test_hallucinated_capital_uplift_claim_downgrades(self):
        deep = {
            "headline": "X",
            "decision_summary": {
                "financial_exposure": "Rs 500 Cr (from article) capital and valuation uplift",
            },
        }
        out, n = audit_source_tags(deep, self.ARTICLE_BODY)
        assert n == 1
        assert "(engine estimate)" in out["decision_summary"]["financial_exposure"]

    def test_legitimate_q3_profit_citation_kept(self):
        deep = {
            "headline": "X",
            "decision_summary": {
                "financial_exposure": "Rs 503 Cr (from article) Q3 net profit signals momentum",
            },
        }
        out, n = audit_source_tags(deep, self.ARTICLE_BODY)
        assert n == 0
        assert "(from article)" in out["decision_summary"]["financial_exposure"]

    def test_bare_claim_without_descriptor_kept_on_numerical_match(self):
        # If the LLM emits "₹503 Cr (from article)" with no descriptor in
        # between, we have no semantic load to verify — fall back to
        # numerical match only.
        deep = {
            "headline": "X",
            "decision_summary": {"financial_exposure": "Rs 503 Cr (from article)"},
        }
        out, n = audit_source_tags(deep, self.ARTICLE_BODY)
        assert n == 0

    def test_figure_not_in_article_at_all_downgrades(self):
        deep = {
            "headline": "X",
            "decision_summary": {
                "financial_exposure": "Rs 999 Cr (from article) penalty",
            },
        }
        out, n = audit_source_tags(deep, self.ARTICLE_BODY)
        assert n == 1

    def test_extract_claim_tokens_strips_stopwords(self):
        tokens = _extract_claim_tokens("capital and valuation uplift")
        assert "capital" in tokens
        assert "valuation" in tokens
        assert "uplift" in tokens
        assert "and" not in tokens

    def test_overlap_passes_when_token_in_proximity(self):
        ok = _article_context_overlaps(
            cr_value=503,
            claim_tokens={"profit", "net", "q3"},
            article_excerpts=self.ARTICLE_BODY,
        )
        assert ok is True

    def test_overlap_fails_when_no_shared_tokens(self):
        ok = _article_context_overlaps(
            cr_value=503,
            claim_tokens={"penalty", "fine", "fraud"},
            article_excerpts=self.ARTICLE_BODY,
        )
        assert ok is False


# ---------------------------------------------------------------------------
# Issue 22.3 — Polarity-aware labels (backend emits the flag)
# ---------------------------------------------------------------------------


class TestPolarityFlag:
    """DeepInsight.event_polarity is set by insight_generator based on
    the event_id + sentiment. Frontend reads it to flip
    'Margin Pressure' → 'Margin Benefit'."""

    def test_deepinsight_dataclass_has_event_polarity_field(self):
        from engine.analysis.insight_generator import DeepInsight
        # Confirm field is present + defaults to "neutral"
        di = DeepInsight(
            headline="X", impact_score=5.0, core_mechanism="",
            profitability_connection="", translation="",
        )
        assert hasattr(di, "event_polarity")
        assert di.event_polarity == "neutral"

    def test_event_polarity_serialises_to_dict(self):
        from engine.analysis.insight_generator import DeepInsight
        di = DeepInsight(
            headline="X", impact_score=7.0, core_mechanism="",
            profitability_connection="", translation="",
            event_polarity="positive",
        )
        d = di.to_dict()
        assert d["event_polarity"] == "positive"

    def test_frontend_uses_event_polarity_for_label_flip(self):
        # Static check: the frontend ArticleDetailSheet.tsx must read
        # event_polarity AND switch the metricLabels for positive events.
        repo_root = Path(__file__).resolve().parent.parent
        sheet = repo_root / "client" / "src" / "components" / "panels" / "ArticleDetailSheet.tsx"
        text = sheet.read_text(encoding="utf-8")
        assert "event_polarity" in text, "Frontend must read event_polarity"
        assert "Margin Benefit" in text, "Frontend must use 'Margin Benefit' for positive"
        assert "Revenue Opportunity" in text, "Frontend must use 'Revenue Opportunity' for positive"
