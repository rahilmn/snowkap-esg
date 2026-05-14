"""Phase 5 §7.5 — fetch_newsapi_ai → NewsRouter budget wiring tests.

Validates that every successful NewsAPI.ai fetch records its token cost
into the process-wide NewsRouter budget, so /metrics shows real spend
without changing the existing fetch behaviour.

We mock `requests.post` so no real HTTP traffic happens.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Reset router singleton + ensure API key is present so the fetcher
    doesn't short-circuit on the no-key path."""
    monkeypatch.setenv("NEWSAPI_AI_KEY", "test-key-not-used")

    from engine.ingestion.news_router import reset_router
    reset_router()
    yield
    reset_router()


def _stub_response(article_count: int):
    """Build a fake NewsAPI.ai HTTP response with N article results."""
    results = [
        {
            "url": f"https://example.com/news/{i}",
            "title": f"Article {i}",
            "body": f"Body of article {i}.",
            "source": {"title": "Stub Source"},
            "dateTime": "2026-05-10T00:00:00Z",
            "image": "",
            "concepts": [],
            "sentiment": 0.0,
        }
        for i in range(article_count)
    ]

    class _StubResp:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"articles": {"results": results}}

    return _StubResp()


def test_fetch_newsapi_ai_records_spend_into_router_budget():
    from engine.ingestion.news_fetcher import fetch_newsapi_ai
    from engine.ingestion.news_router import get_router

    router = get_router()
    before = router.budget.spent_this_month

    with patch(
        "engine.ingestion.news_fetcher.requests.post",
        return_value=_stub_response(article_count=3),
    ):
        out = fetch_newsapi_ai("ESG climate", max_results=5)

    assert len(out) == 3  # what the fetcher returns
    after = router.budget.spent_this_month
    # ASSUMPTION (plan §7.1): 1 token = 1 article in response → spent +3
    assert after == before + 3


def test_fetch_newsapi_ai_records_zero_spend_when_no_articles():
    """Empty result still completes cleanly and records 0 tokens."""
    from engine.ingestion.news_fetcher import fetch_newsapi_ai
    from engine.ingestion.news_router import get_router

    router = get_router()
    before = router.budget.spent_this_month

    with patch(
        "engine.ingestion.news_fetcher.requests.post",
        return_value=_stub_response(article_count=0),
    ):
        out = fetch_newsapi_ai("ESG climate", max_results=5)

    assert out == []
    assert router.budget.spent_this_month == before


def test_fetch_newsapi_ai_does_not_record_spend_on_http_error(monkeypatch):
    """If the fetch fails (RequestException → returns []), the budget is
    unchanged — we only spend when articles actually came back."""
    import requests
    from engine.ingestion.news_fetcher import fetch_newsapi_ai
    from engine.ingestion.news_router import get_router

    router = get_router()
    before = router.budget.spent_this_month

    def _raise(*a, **kw):
        raise requests.RequestException("simulated 500")

    monkeypatch.setattr(
        "engine.ingestion.news_fetcher.requests.post", _raise,
    )
    out = fetch_newsapi_ai("ESG climate", max_results=5)
    assert out == []
    assert router.budget.spent_this_month == before


def test_fetch_newsapi_ai_no_api_key_no_spend(monkeypatch):
    """When the env has no API key, fetch returns [] WITHOUT calling the
    HTTP path — and the budget stays untouched."""
    from engine.ingestion.news_fetcher import fetch_newsapi_ai
    from engine.ingestion.news_router import get_router

    monkeypatch.delenv("NEWSAPI_AI_KEY", raising=False)
    monkeypatch.delenv("NEWSAPI_AI_API_KEY", raising=False)
    monkeypatch.delenv("EVENT_REGISTRY_API_KEY", raising=False)

    router = get_router()
    before = router.budget.spent_this_month

    out = fetch_newsapi_ai("ESG climate", max_results=5)
    assert out == []
    assert router.budget.spent_this_month == before
