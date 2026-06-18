"""Phase 51.J — NewsAPI budget persistence (C#3, production-readiness W3).

The monthly token cap lived in memory only, so every Railway restart zeroed
``spent_this_month`` — the cap was never enforced across a month and a restart
loop could blow the real NewsAPI.ai quota. Persist the per-month counters.

Run: python -m pytest tests/test_phase51j_budget_persist.py -q
"""
from __future__ import annotations

from engine.models import newsapi_budget as nb


def test_save_load_roundtrip():
    nb.save("2099-01", 1234, 56)
    assert nb.load("2099-01") == {"spent_this_month": 1234, "burst_spent": 56}


def test_load_missing_returns_none():
    assert nb.load("1900-12") is None
    assert nb.load("") is None


def test_upsert_overwrites():
    nb.save("2099-02", 100, 10)
    nb.save("2099-02", 500, 50)  # upsert, not a second row
    assert nb.load("2099-02") == {"spent_this_month": 500, "burst_spent": 50}


def test_router_restores_spend_after_restart():
    """A fresh router (post-restart) must restore the month's spend from the
    DB instead of starting at 0."""
    from engine.ingestion import news_router as nr

    month = nr._current_month()
    nb.save(month, 777, 88)
    nr._DEFAULT_ROUTER = None  # force a fresh construct (simulates a restart)
    try:
        router = nr.get_router()
        assert router.budget.spent_this_month == 777
        assert router.budget.burst_spent == 88
    finally:
        nr._DEFAULT_ROUTER = None  # don't leak the doctored singleton to other tests
