"""Phase 56 — self-service onboarding resilience.

A flaky/slow company-resolution LLM call used to wedge the whole onboard in
'pending' forever (observed live: an Ather onboard stuck ~20 min, no error, no
recourse — only a manual re-POST recovered it). Three guards now prevent it:

  1. ``profile._resolve_company_with_retry`` — fail-fast (per-attempt wall-clock
     cap) + auto-retry; on exhaustion -> state='failed' (retryable), never an
     eternal 'pending'.
  2. ``onboarding_status.mark_kickoff`` — resets ``started_at`` on each (re)kick
     so the watchdog clock measures THIS attempt, not a stale earlier one.
  3. ``onboarding_status.expire_if_stale`` — watchdog: a non-terminal job older
     than the budget is flipped to 'failed' on the next status poll.
"""
from __future__ import annotations

import pytest

from engine.models import onboarding_status as ob


@pytest.fixture(autouse=True)
def _clean_rows():
    ob.ensure_schema()
    ob._truncate_all()
    yield
    ob._truncate_all()


def _seed_with_age(slug: str, state: str, started_at: str) -> None:
    """Create a row whose started_at is `started_at` (so we can age it)."""
    orig_now = ob._now
    ob._now = lambda: started_at  # type: ignore[assignment]
    try:
        ob.mark_kickoff(slug)
    finally:
        ob._now = orig_now  # type: ignore[assignment]
    if state != "pending":
        ob.upsert(slug, state=state)


# --------------------------------------------------------------------------- #
# mark_kickoff — resets the clock (unlike upsert)
# --------------------------------------------------------------------------- #
def test_mark_kickoff_resets_started_at_unlike_upsert():
    old = "2026-01-01T00:00:00+00:00"
    _seed_with_age("acme", "pending", old)
    assert ob.get("acme").started_at == old

    # upsert(state=...) must NOT move started_at (the live bug we worked around)
    ob.upsert("acme", state="fetching")
    assert ob.get("acme").started_at == old

    # ...but mark_kickoff RESETS it and clears any prior error / finished_at.
    ob.upsert("acme", state="failed", error="boom", finished_at=old)
    row = ob.mark_kickoff("acme")
    assert row.state == "pending"
    assert row.started_at != old          # fresh clock
    assert row.error is None
    assert row.finished_at is None


# --------------------------------------------------------------------------- #
# expire_if_stale — the watchdog
# --------------------------------------------------------------------------- #
def test_watchdog_fails_a_stale_nonterminal_job():
    _seed_with_age("stuck", "fetching", "2026-01-01T00:00:00+00:00")  # ancient
    row = ob.expire_if_stale("stuck", max_minutes=15)
    assert row is not None and row.state == "failed"
    assert "timed out" in (row.error or "").lower()
    assert row.finished_at is not None


def test_watchdog_leaves_a_young_job_alone():
    ob.mark_kickoff("fresh")              # started_at = now
    ob.upsert("fresh", state="analysing")
    assert ob.expire_if_stale("fresh", max_minutes=15).state == "analysing"


def test_watchdog_noop_for_terminal_states():
    ob.mark_kickoff("done")
    ob.upsert("done", state="ready")
    # Even with a zero budget, a terminal row is never touched.
    assert ob.expire_if_stale("done", max_minutes=0).state == "ready"

    _seed_with_age("already", "failed", "2026-01-01T00:00:00+00:00")
    assert ob.expire_if_stale("already", max_minutes=0).state == "failed"


def test_watchdog_returns_none_for_missing_slug():
    assert ob.expire_if_stale("nope", max_minutes=15) is None


# --------------------------------------------------------------------------- #
# _resolve_company_with_retry — fail-fast + auto-retry
# --------------------------------------------------------------------------- #
class _FakeInfo:
    slug = "acme-inc"


def test_resolve_retries_then_succeeds(monkeypatch):
    from api.routes import profile
    import engine.ingestion.llm_company_resolver as resolver

    calls = {"n": 0}

    def _flaky(domain, name_hint=None, *, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient OpenRouter blip")
        return _FakeInfo()

    monkeypatch.setattr(resolver, "resolve_company_from_domain", _flaky)
    ob.mark_kickoff("acme")
    info = profile._resolve_company_with_retry("acme.com", "acme")
    assert isinstance(info, _FakeInfo)
    assert calls["n"] == 2                       # retried once, then succeeded
    assert ob.get("acme").state == "pending"     # NOT failed — recovered


def test_resolve_exhausts_then_marks_failed(monkeypatch):
    from api.routes import profile
    import engine.ingestion.llm_company_resolver as resolver

    def _always_raise(*_a, **_k):
        raise RuntimeError("still hung")

    monkeypatch.setattr(profile, "_RESOLVE_ATTEMPTS", 3)
    monkeypatch.setattr(resolver, "resolve_company_from_domain", _always_raise)
    ob.mark_kickoff("acme")
    out = profile._resolve_company_with_retry("acme.com", "acme")
    assert out is None
    row = ob.get("acme")
    assert row.state == "failed"
    assert "retry" in (row.error or "").lower()  # user-facing + retryable


def test_resolve_none_response_marks_failed(monkeypatch):
    from api.routes import profile
    import engine.ingestion.llm_company_resolver as resolver

    monkeypatch.setattr(resolver, "resolve_company_from_domain", lambda *a, **k: None)
    ob.mark_kickoff("acme")
    assert profile._resolve_company_with_retry("acme.com", "acme") is None
    assert ob.get("acme").state == "failed"
