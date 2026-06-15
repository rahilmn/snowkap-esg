"""Phase 5 — two-tier news router tests.

Validates router dispatch logic, budget accounting, and fallback paths.

NOTE: These tests use stubbed Tier 1 / Tier 2 fetchers via monkeypatch
so they don't make real HTTP calls. The actual fetchers
(``fetch_newsapi_ai`` / ``fetch_google_news``) are exercised in their
own test files.

The token-cost ASSUMPTION (1 token / article in response) is encoded in
``_default_token_cost`` and tested explicitly so a future swap to a
different rule (e.g. 1 token / query) is a one-line change with a
visible test failure flagging the contract change.
"""
from __future__ import annotations

import pytest

from engine.ingestion.news_router import (
    BudgetState,
    FetchResult,
    NewsRouter,
    _current_month,
    _default_token_cost,
    get_router,
    reset_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_articles(n: int, source: str = "stub") -> list[dict]:
    return [
        {
            "title": f"Article {i}",
            "url": f"https://example.com/{source}/{i}",
            "content": f"body {i}",
        }
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def reset_singleton():
    """Each test gets a fresh router singleton."""
    reset_router()
    yield
    reset_router()


@pytest.fixture
def stub_fetchers(monkeypatch):
    """Stub the actual HTTP fetchers so router tests stay offline."""
    captured: dict[str, list] = {"tier1_calls": [], "tier2_calls": []}

    def _stub_tier1(query: str, max_results: int = 5) -> list[dict]:
        captured["tier1_calls"].append((query, max_results))
        return _stub_articles(max_results, source="newsapi_ai")

    def _stub_tier2(*args, **kwargs) -> list[dict]:
        # Phase 48.A — Tier 2 now also uses fetch_newsapi_ai (Google removed).
        captured["tier2_calls"].append((args, kwargs))
        # Default to 5 articles
        n = kwargs.get("max_results") or (args[1] if len(args) > 1 else 5)
        return _stub_articles(n, source="newsapi_ai")

    # Tier 1 and Tier 2 both resolve to fetch_newsapi_ai now; route the
    # router's internal calls through a single stub that records both.
    monkeypatch.setattr(
        "engine.ingestion.news_fetcher.fetch_newsapi_ai", _stub_tier1
    )
    return captured


# ---------------------------------------------------------------------------
# Token cost
# ---------------------------------------------------------------------------


def test_default_token_cost_is_one_per_article():
    """ASSUMPTION (plan §7.1): 1 NewsAPI.ai token = 1 article in response.
    If verification later proves different (e.g. 1 token / query), this
    test will fail and flag the contract change."""
    assert _default_token_cost(_stub_articles(5)) == 5
    assert _default_token_cost([]) == 0


# ---------------------------------------------------------------------------
# Budget state
# ---------------------------------------------------------------------------


def test_budget_remaining_starts_at_cap():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    assert b.remaining() == 100
    assert b.burst_remaining() == 20


def test_budget_spend_decrements_remaining():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    b.spend(30)
    assert b.remaining() == 70
    b.spend(80)  # over-spend doesn't matter — clamped to 0
    assert b.remaining() == 0


def test_burst_spend_isolated_from_main():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    b.spend(15, from_burst=True)
    assert b.burst_remaining() == 5
    assert b.remaining() == 100  # unchanged
    b.spend(30)  # main pool
    assert b.remaining() == 70
    assert b.burst_remaining() == 5  # unchanged


def test_can_spend_respects_main_pool():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    assert b.can_spend(50)
    assert not b.can_spend(150)
    b.spend(80)
    assert b.can_spend(20)
    assert not b.can_spend(21)


def test_can_spend_respects_burst_pool():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    assert b.can_spend(20, from_burst=True)
    assert not b.can_spend(21, from_burst=True)


def test_budget_rolls_over_at_month_change():
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    b.spend(80)
    assert b.remaining() == 20
    # Force a different month_anchor → next remaining() resets
    b.month_anchor = "1999-01"
    assert b.remaining() == 100  # auto-rolled
    assert b.spent_this_month == 0


def test_to_dict_serialisable():
    import json
    b = BudgetState(monthly_cap=100, burst_reserve=20)
    b.spend(15)
    js = json.dumps(b.to_dict())
    parsed = json.loads(js)
    assert parsed["spent_this_month"] == 15
    assert parsed["remaining"] == 85
    assert "month_anchor" in parsed


# ---------------------------------------------------------------------------
# Router dispatch
# ---------------------------------------------------------------------------


def test_hourly_feed_always_uses_tier2(stub_fetchers):
    router = NewsRouter(budget=BudgetState(monthly_cap=10_000))
    res = router.fetch_for_company(
        "waaree-energies",
        mode="hourly_feed",
        queries=["Waaree solar", "Waaree ESG"],
    )
    assert isinstance(res, FetchResult)
    assert res.tier == "tier2_google_rss"
    assert res.tokens_spent == 0
    # Phase 48.A merged Tier-2 onto fetch_newsapi_ai, so the single stub
    # records both tiers' calls in tier1_calls.
    assert len(stub_fetchers["tier1_calls"]) == 2
    assert len(stub_fetchers["tier2_calls"]) == 0


def test_weekly_critical_uses_tier1_when_budget_allows(stub_fetchers):
    router = NewsRouter(budget=BudgetState(monthly_cap=100))
    res = router.fetch_for_company(
        "waaree-energies",
        mode="weekly_critical",
        queries=["q1", "q2"],
        articles_per_query=3,
    )
    assert res.tier == "tier1_newsapi_ai"
    # 2 queries × 3 articles each = 6 articles → 6 tokens (1/article)
    assert res.tokens_spent == 6
    assert len(stub_fetchers["tier1_calls"]) == 2
    assert len(stub_fetchers["tier2_calls"]) == 0
    assert router.budget.remaining() == 94


def test_weekly_critical_falls_back_when_budget_exhausted(stub_fetchers):
    # Tiny cap so estimated cost (2 queries × 5 articles = 10) exceeds 5
    router = NewsRouter(budget=BudgetState(monthly_cap=5))
    res = router.fetch_for_company(
        "waaree-energies",
        mode="weekly_critical",
        queries=["q1", "q2"],
        articles_per_query=5,
    )
    assert res.tier == "tier2_google_rss"
    assert res.tokens_spent == 0
    assert "tier1 budget exhausted" in (res.fallback_reason or "")
    # Phase 48.A — the Tier-2 fallback also routes through fetch_newsapi_ai.
    assert len(stub_fetchers["tier2_calls"]) == 0
    assert len(stub_fetchers["tier1_calls"]) == 2


def test_burst_uses_burst_reserve_not_main(stub_fetchers):
    router = NewsRouter(budget=BudgetState(monthly_cap=100, burst_reserve=20))
    res = router.fetch_for_company(
        "siemens", mode="burst", queries=["breaking"], articles_per_query=3,
    )
    assert res.tier == "tier1_newsapi_ai"
    assert res.tokens_spent == 3
    # Spent from burst, not main
    assert router.budget.burst_remaining() == 17
    assert router.budget.remaining() == 100


def test_burst_falls_back_when_burst_reserve_exhausted(stub_fetchers):
    router = NewsRouter(budget=BudgetState(monthly_cap=100, burst_reserve=2))
    res = router.fetch_for_company(
        "siemens", mode="burst", queries=["q1", "q2"], articles_per_query=3,
    )
    # Estimate 2 * 3 = 6 > burst_reserve 2 → fallback
    assert res.tier == "tier2_google_rss"
    assert "burst_remaining" in (res.fallback_reason or "")


def test_router_handles_empty_query_list(stub_fetchers):
    router = NewsRouter(budget=BudgetState(monthly_cap=100))
    res = router.fetch_for_company(
        "waaree-energies", mode="weekly_critical", queries=[],
    )
    # Empty → 0 articles, 0 tokens spent
    assert res.tokens_spent == 0
    assert res.articles == []


def test_router_continues_past_failed_query(stub_fetchers, monkeypatch):
    """A single query failure (HTTP error) must not abort the whole batch."""
    call_log: list[str] = []

    def _flaky_tier1(query: str, max_results: int = 5) -> list[dict]:
        call_log.append(query)
        if "fail" in query:
            raise RuntimeError("simulated 500")
        return _stub_articles(2, source="newsapi_ai")

    monkeypatch.setattr(
        "engine.ingestion.news_fetcher.fetch_newsapi_ai", _flaky_tier1
    )
    router = NewsRouter(budget=BudgetState(monthly_cap=100))
    res = router.fetch_for_company(
        "siemens",
        mode="weekly_critical",
        queries=["good1", "fail-here", "good2"],
    )
    # Both good queries returned 2 articles each = 4 total
    assert res.tier == "tier1_newsapi_ai"
    assert len(res.articles) == 4
    assert call_log == ["good1", "fail-here", "good2"]


# ---------------------------------------------------------------------------
# Singleton helper
# ---------------------------------------------------------------------------


def test_get_router_returns_singleton():
    a = get_router()
    b = get_router()
    assert a is b


def test_reset_router_clears_singleton():
    a = get_router()
    reset_router()
    b = get_router()
    assert a is not b


def test_get_router_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("SNOWKAP_NEWSAPI_MONTHLY_CAP", "5000")
    monkeypatch.setenv("SNOWKAP_NEWSAPI_BURST_RESERVE", "750")
    reset_router()
    r = get_router()
    assert r.budget.monthly_cap == 5000
    assert r.budget.burst_reserve == 750


def test_current_month_format():
    """Sanity: month anchor is YYYY-MM."""
    m = _current_month()
    assert len(m) == 7
    assert m[4] == "-"
    assert m[:4].isdigit()
    assert m[5:].isdigit()
