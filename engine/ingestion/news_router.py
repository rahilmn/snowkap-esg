"""Phase 5 — Two-tier news router.

The plan §7 wants a router that splits ingestion across two tiers:

  - **Tier 1 (NewsAPI.ai)** — premium, full-body articles (2,000-5,000+
    chars). Used for the *weekly critical hunt* (top-5 painpoint queries
    × ~30 companies × 4 weeks ≈ 960 tokens/mo) and *onboarding bursts*.

  - **Tier 2 (Google News RSS)** — free, headline-only. Used for the
    hourly background feed and as a fallback when Tier 1 budget is
    exhausted.

CRITICAL OPEN QUESTION (plan §7.1): the *exact* token semantics of
NewsAPI.ai (= Event Registry). The plan calls this out explicitly:

  > Before writing code, fetch newsapi.ai's docs and confirm what 1
  > token equals — 1 article fetch? 1 query? 1 result returned?
  > **Do not assume — verify, then implement.**

This module ships the router INTERFACE + a budget accountant with the
working assumption that **1 token ≈ 1 article in the response** (the
inference from existing usage in ``news_fetcher.fetch_newsapi_ai``
which caps ``articlesCount`` to "conserve free tier tokens"). The
accountant is overridable so the user can drop in the real rule once
verified without touching the rest of the call chain.

Search for ``ASSUMPTION`` in this file to find every guess that needs
confirmation.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal

logger = logging.getLogger(__name__)


FetchMode = Literal["weekly_critical", "hourly_feed", "burst"]


# ---------------------------------------------------------------------------
# Budget accounting
# ---------------------------------------------------------------------------
#
# ASSUMPTION (unverified): 1 NewsAPI.ai token = 1 article returned in the
# response. If verified later as "1 token = 1 query regardless of count",
# replace `_default_token_cost` with `lambda articles: 1` and re-tune
# the per-call article caps in fetch_newsapi_ai.
#
# Plan §7.3 monthly budget under that assumption (per the plan):
#   Tier 1 — weekly critical hunt: 30 companies × 8 articles × 4 weeks = 960
#   Tier 1 — onboarding burst:     5 new tenants × 20 articles       = 100
#   Tier 1 — breaking-news reserve:                                    500
#   Tier 1 — oh-shit reserve:                                          440
#   Total Tier 1 per month                                          ≈ 2000
#   Tier 2 (Google RSS): unlimited
# That fits under a $150 / 10K-token plan with comfortable headroom.


def _default_token_cost(articles: list[dict]) -> int:
    """Per-call token cost. ASSUMPTION: 1 token / article in response."""
    return len(articles)


@dataclass
class BudgetState:
    """Tracks tokens spent in the current calendar month per tier purpose.

    Persistence is the caller's responsibility — wire to SQLite or a
    JSON file once the production tier-cap policy is finalised. Today
    this is in-memory only; reset on process restart.
    """
    monthly_cap: int = 2000  # ASSUMPTION: $150/10K plan, ~2K reserved for app
    burst_reserve: int = 500
    spent_this_month: int = 0
    burst_spent: int = 0
    month_anchor: str = field(default_factory=lambda: _current_month())

    def maybe_roll_over(self) -> None:
        """At month boundary, reset counters."""
        now_month = _current_month()
        if now_month != self.month_anchor:
            self.spent_this_month = 0
            self.burst_spent = 0
            self.month_anchor = now_month

    def remaining(self) -> int:
        self.maybe_roll_over()
        return max(0, self.monthly_cap - self.spent_this_month)

    def burst_remaining(self) -> int:
        self.maybe_roll_over()
        return max(0, self.burst_reserve - self.burst_spent)

    def can_spend(self, tokens: int, *, from_burst: bool = False) -> bool:
        if tokens <= 0:
            return True
        return (
            self.burst_remaining() >= tokens
            if from_burst
            else self.remaining() >= tokens
        )

    def spend(self, tokens: int, *, from_burst: bool = False) -> None:
        if tokens <= 0:
            return
        self.maybe_roll_over()
        if from_burst:
            self.burst_spent += tokens
        else:
            self.spent_this_month += tokens

    def to_dict(self) -> dict[str, int | str]:
        self.maybe_roll_over()
        return {
            "monthly_cap": self.monthly_cap,
            "burst_reserve": self.burst_reserve,
            "spent_this_month": self.spent_this_month,
            "burst_spent": self.burst_spent,
            "remaining": self.remaining(),
            "burst_remaining": self.burst_remaining(),
            "month_anchor": self.month_anchor,
        }


def _current_month() -> str:
    """ISO YYYY-MM in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    """Articles returned by a router call, plus the tier that served them
    and the token cost incurred (Tier 1 only — Tier 2 is free)."""
    articles: list[dict]
    tier: Literal["tier1_newsapi_ai", "tier2_google_rss"]
    tokens_spent: int
    fallback_reason: str | None = None


class NewsRouter:
    """Two-tier news router (plan §7.2).

    Wire calls to fetch_for_company at orchestration sites (scheduler,
    onboarding background task, on-demand re-enrichment) instead of the
    raw fetchers.

    Args:
        budget: BudgetState instance (caller-owned for persistence).
                Default is a fresh in-memory BudgetState.
        token_cost_fn: Override the default 1-token-per-article rule once
                       verified against NewsAPI.ai docs.
    """

    def __init__(
        self,
        budget: BudgetState | None = None,
        token_cost_fn: Callable[[list[dict]], int] = _default_token_cost,
    ) -> None:
        self.budget = budget or BudgetState()
        self.token_cost_fn = token_cost_fn

    def fetch_for_company(
        self,
        slug: str,
        mode: FetchMode,
        queries: Iterable[str] | None = None,
        articles_per_query: int = 5,
    ) -> FetchResult:
        """Dispatch per mode (plan §7.2).

        - `weekly_critical`: Tier 1 if budget allows; else Tier 2 fallback
        - `hourly_feed`:     always Tier 2 (Tier 1 is too expensive for hourly)
        - `burst`:           Tier 1 from burst reserve; else Tier 2 fallback
        """
        queries_list = list(queries or [])

        if mode == "hourly_feed":
            return self._tier2(slug, queries_list, articles_per_query)

        from_burst = mode == "burst"

        # Estimate cost as N * articles_per_query under the working
        # assumption. This is an UPPER bound — actual cost is the count
        # of articles RETURNED (which can be < requested).
        est_cost = max(1, len(queries_list) * articles_per_query)

        if not self.budget.can_spend(est_cost, from_burst=from_burst):
            return self._tier2(
                slug,
                queries_list,
                articles_per_query,
                fallback_reason=(
                    f"tier1 budget exhausted "
                    f"(remaining={self.budget.remaining()}, "
                    f"burst_remaining={self.budget.burst_remaining()})"
                ),
            )

        return self._tier1(
            slug, queries_list, articles_per_query, from_burst=from_burst
        )

    # ----------------------------------------------------------------- Tiers

    def _tier1(
        self,
        slug: str,
        queries: list[str],
        articles_per_query: int,
        *,
        from_burst: bool,
    ) -> FetchResult:
        """Tier 1: NewsAPI.ai (Event Registry) — full body, paid."""
        from engine.ingestion.news_fetcher import fetch_newsapi_ai

        all_articles: list[dict] = []
        for query in queries:
            try:
                got = fetch_newsapi_ai(query, max_results=articles_per_query)
            except Exception as exc:  # noqa: BLE001 — never break ingest on one query
                logger.warning(
                    "tier1 fetch failed for %s / %s: %s", slug, query, exc
                )
                continue
            all_articles.extend(got)

        cost = self.token_cost_fn(all_articles)
        self.budget.spend(cost, from_burst=from_burst)
        logger.info(
            "tier1 fetch: slug=%s queries=%d articles=%d tokens=%d "
            "(remaining_monthly=%d, burst=%s)",
            slug, len(queries), len(all_articles), cost,
            self.budget.remaining(), from_burst,
        )
        return FetchResult(
            articles=all_articles,
            tier="tier1_newsapi_ai",
            tokens_spent=cost,
        )

    def _tier2(
        self,
        slug: str,
        queries: list[str],
        articles_per_query: int,
        *,
        fallback_reason: str | None = None,
    ) -> FetchResult:
        """Tier 2: Google News RSS — headline only, free."""
        from engine.ingestion.news_fetcher import fetch_google_news

        all_articles: list[dict] = []
        for query in queries:
            try:
                got = fetch_google_news(query, max_results=articles_per_query)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tier2 fetch failed for %s / %s: %s", slug, query, exc
                )
                continue
            all_articles.extend(got)

        return FetchResult(
            articles=all_articles,
            tier="tier2_google_rss",
            tokens_spent=0,
            fallback_reason=fallback_reason,
        )


# ---------------------------------------------------------------------------
# Singleton helper for orchestration code
# ---------------------------------------------------------------------------


_DEFAULT_ROUTER: NewsRouter | None = None


def get_router() -> NewsRouter:
    """Process-wide router singleton. Wire orchestration code to call
    ``get_router().fetch_for_company(...)`` so the budget state is
    shared across the scheduler / on-demand / onboarding paths.

    Override the cap via SNOWKAP_NEWSAPI_MONTHLY_CAP / _BURST_RESERVE
    env vars (defaults: 2000 + 500).
    """
    global _DEFAULT_ROUTER
    if _DEFAULT_ROUTER is None:
        cap = int(os.environ.get("SNOWKAP_NEWSAPI_MONTHLY_CAP", "2000"))
        burst = int(os.environ.get("SNOWKAP_NEWSAPI_BURST_RESERVE", "500"))
        _DEFAULT_ROUTER = NewsRouter(
            budget=BudgetState(monthly_cap=cap, burst_reserve=burst),
        )
    return _DEFAULT_ROUTER


def reset_router() -> None:
    """Test hook — drop the singleton so the next get_router() builds fresh."""
    global _DEFAULT_ROUTER
    _DEFAULT_ROUTER = None
