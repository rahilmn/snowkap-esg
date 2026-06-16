"""Phase 51.E — the default (deck) criticality weights are materiality-led.

The old financial-cascade-led default (financial_magnitude 0.30 > materiality
0.20) systematically buried genuine ESG-governance events (fraud, regulatory,
emissions — no cascade ₹, so financial_magnitude=0) beneath financial/business
news (results, turnover — big cascade ₹). For an ESG product the deck must rank
ESG materiality first.
"""
from __future__ import annotations

from datetime import datetime, timezone

from engine.analysis.criticality_scorer import (
    WEIGHTS_DEFAULT,
    _weighted_score,
    score_components,
)

_NOW = datetime(2026, 6, 13, tzinfo=timezone.utc)
_PUB = "2026-06-12T00:00:00Z"

_OLD_DEFAULT = {
    "materiality": 0.20, "financial_magnitude": 0.30, "actionability": 0.15,
    "painpoint_match": 0.20, "recency": 0.075, "source_authority": 0.025,
    "sentiment_trajectory": 0.05,
}


def _esg_event():
    # Genuine ESG-governance event: high ESG relevance, NO cascade ₹.
    return score_components(
        relevance_total=8.0, cascade_total_cr=None, company_revenue_cr=200000.0,
        event_id="event_litigation_initiated", published_at=_PUB, source="Mint", now=_NOW,
    )


def _financial_news():
    # Business/market news: lower ESG relevance, BIG cascade ₹ (10% of revenue).
    return score_components(
        relevance_total=5.0, cascade_total_cr=20000.0, company_revenue_cr=200000.0,
        event_id="event_quarterly_results", published_at=_PUB, source="Mint", now=_NOW,
    )


def test_default_weights_sum_to_one() -> None:
    assert abs(sum(WEIGHTS_DEFAULT.values()) - 1.0) < 1e-9


def test_materiality_now_leads_financial_magnitude() -> None:
    assert WEIGHTS_DEFAULT["materiality"] > WEIGHTS_DEFAULT["financial_magnitude"]


def test_esg_event_outranks_financial_news_under_new_default() -> None:
    esg, fin = _esg_event(), _financial_news()
    assert _weighted_score(esg, WEIGHTS_DEFAULT) > _weighted_score(fin, WEIGHTS_DEFAULT)


def test_old_default_inverted_it_regression_proof() -> None:
    """Documents the bug we fixed: the old financial-led default ranked the
    business article ABOVE the genuine ESG event."""
    esg, fin = _esg_event(), _financial_news()
    assert _weighted_score(fin, _OLD_DEFAULT) > _weighted_score(esg, _OLD_DEFAULT)
