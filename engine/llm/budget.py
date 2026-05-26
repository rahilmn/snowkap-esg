"""Phase 30 — Per-tenant LLM daily cost cap.

Single biggest preventable prod risk: one stuck retry loop or one prospect
onboarding 50 domains can burn $200+ of LLM spend before anyone notices.
This module fails closed at $5/tenant/day by default. Overridable via
``SNOWKAP_PER_TENANT_DAILY_CAP_USD`` env var.

Two integration points:

  1. **Onboarding worker** — before running the article pipeline for a
     tenant, ``assert_under_cap(tenant_slug)``. If exceeded, mark the
     onboard as failed with a clean error message (the user sees
     "Daily limit reached — try again tomorrow") rather than a half-
     finished onboard.

  2. **On-demand enrichment** — when a user clicks a SECONDARY-tier
     article, run the same gate before firing Stage 10. Same fail-mode.

The cap is checked against ``llm_calls`` rows joined with
``article_index`` to resolve the company slug — no schema changes
needed. Costs come from ``llm_calls.cost_usd`` which Phase C / Phase 11D
already populates per call.

Failure mode is **fail closed**: a tracking failure (DB hiccup) returns
"0 spent" so the gate stays open — better to let the work through than
block a paying tenant. Combined with /metrics + Sentry alerts on
``snowkap_llm_24h_usd`` this gives belt-and-suspenders coverage.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DEFAULT_DAILY_CAP_USD = 5.0
"""Conservative starting value. At ~$0.06/article × 3 critical/onboard,
a single tenant could realistically spend ~$0.20/day. The $5 cap leaves
~25× headroom for active users while bounding worst-case retry-storm
spend per tenant per day."""

ENV_OVERRIDE = "SNOWKAP_PER_TENANT_DAILY_CAP_USD"


class TenantBudgetExceeded(Exception):
    """Raised when a tenant has hit their daily LLM cost cap.

    Caller decides whether to:
      * Fail closed (worker/background) — mark the job as failed.
      * Fail open (interactive UI) — return a friendly 429 to the user.

    Carries the actual spend + cap so the caller can render an honest
    error message.
    """
    def __init__(self, tenant_slug: str, spent: float, cap: float):
        self.tenant_slug = tenant_slug
        self.spent = spent
        self.cap = cap
        super().__init__(
            f"Tenant {tenant_slug!r} spent ${spent:.2f} today; daily cap is ${cap:.2f}"
        )


def get_daily_cap_usd() -> float:
    """Read the cap from the env var with a sensible default + bounds.

    Negative or unparseable values fall back to the default. A cap of
    0 is treated as "disabled" (always under cap) — ops can override
    for a panic-disable scenario.
    """
    raw = os.environ.get(ENV_OVERRIDE)
    if raw is None:
        return DEFAULT_DAILY_CAP_USD
    try:
        v = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "budget: invalid %s=%r; falling back to default $%.2f",
            ENV_OVERRIDE, raw, DEFAULT_DAILY_CAP_USD,
        )
        return DEFAULT_DAILY_CAP_USD
    if v < 0:
        logger.warning("budget: negative cap %r → using default", v)
        return DEFAULT_DAILY_CAP_USD
    return v


# ---------------------------------------------------------------------------
# Spend lookup
# ---------------------------------------------------------------------------


@contextmanager
def _connect() -> Iterator[Any]:
    with _db_connect() as conn:
        yield conn


def _utc_day_start_iso() -> str:
    """Today at 00:00:00 UTC as an ISO 8601 string for the SQL filter."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat(timespec="seconds")


def tenant_spend_today_usd(tenant_slug: str) -> float:
    """Sum of ``llm_calls.cost_usd`` for this tenant since UTC midnight.

    Joins ``llm_calls`` with ``article_index`` to resolve the tenant from
    ``article_id``. Calls without an ``article_id`` (e.g. onboarding
    company-profile lookup) are NOT counted — they're not tenant-scoped
    work; tracked separately via the ``stage`` column for ops.

    Returns 0.0 on any error (fail open). This is intentional: a DB
    hiccup must not block real-paying-customer work. Alerting on
    ``/metrics`` covers the runaway case.
    """
    if not tenant_slug:
        return 0.0
    try:
        from engine.models.llm_calls import ensure_schema as _llm_ensure
        _llm_ensure()
    except Exception:  # noqa: BLE001
        return 0.0

    try:
        cutoff = _utc_day_start_iso()
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(c.cost_usd), 0)
                FROM llm_calls c
                JOIN article_index a ON a.id = c.article_id
                WHERE a.company_slug = ?
                  AND c.ts >= ?
                """,
                (tenant_slug, cutoff),
            ).fetchone()
        if row is None:
            return 0.0
        return float(row[0] or 0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "budget: spend lookup failed for tenant=%r (fail open): %s",
            tenant_slug, exc,
        )
        return 0.0


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def assert_under_cap(tenant_slug: str) -> None:
    """Raise ``TenantBudgetExceeded`` when the tenant is at or above the
    daily cap. Caller decides fail-closed vs fail-open.

    A cap of 0 disables the gate (always passes). Lets ops panic-disable
    via ``SNOWKAP_PER_TENANT_DAILY_CAP_USD=0`` without a code change.
    """
    cap = get_daily_cap_usd()
    if cap <= 0:
        return
    spent = tenant_spend_today_usd(tenant_slug)
    if spent >= cap:
        raise TenantBudgetExceeded(tenant_slug, spent, cap)


def under_cap(tenant_slug: str) -> tuple[bool, float, float]:
    """Non-raising helper. Returns ``(is_under, spent_usd, cap_usd)``.

    Useful when the caller wants to log the check or include the spend
    in a response payload without try/except boilerplate.
    """
    cap = get_daily_cap_usd()
    spent = tenant_spend_today_usd(tenant_slug)
    if cap <= 0:
        return True, spent, cap
    return spent < cap, spent, cap
