"""Phase 56.F — admin curated deck ingest (demo / manual override).

Pins hand-picked articles as criticals (full pipeline) and fills the quick-read
tier from the live fetch with forum/UGC noise filtered. These tests cover the
orchestration (no LLM / DB) by stubbing the publish helpers, plus the UGC
filter that keeps "I am planning to buy a car…" forum posts out of the deck.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.analysis import deck_builder as db
from api.routes.legacy_adapter import _looks_like_ugc


# ---------------------------------------------------------------------------
# UGC filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title, source, is_ugc", [
    ("I am planning to buy a car. Currently having the Triber", "TeamBHP", True),
    ("I currently own an Alto that is now 15 years old", "team-bhp.com", True),
    ("Hi, I am planning to buy a car with automatic transmission", "Forum", True),
    ("Which car should I buy under 10 lakhs?", "Quora", True),
    ("Maruti Suzuki Ignis Recalled: Check Issue", "Autocar", False),
    ("Maruti Suzuki to invest Rs 150 crore in biogas projects", "Energetica", False),
    ("Maruti Suzuki Dzire Price Hike June 2026", "CarDekho", False),
])
def test_looks_like_ugc(title, source, is_ugc):
    assert _looks_like_ugc(title, source) is is_ugc


# ---------------------------------------------------------------------------
# build_curated_deck orchestration (stubbed publish helpers)
# ---------------------------------------------------------------------------


def _art(i):
    return SimpleNamespace(id=f"a{i}", title=f"Article {i}", url=f"http://x/{i}")


def _result(i):
    return SimpleNamespace(article_id=f"a{i}", title=f"Article {i}", rejected=False)


def _patch(monkeypatch, *, critical_outcomes, light_outcome="published",
           quick_read_outcome="published"):
    """Stub the heavy pipeline so we test the tier orchestration only."""
    monkeypatch.setattr(db, "_run_stages_1_to_9",
                        lambda art, company, **kw: _result(art.id.lstrip("a")))
    monkeypatch.setattr(db, "_force_critical_band", lambda company, aid: None)
    outcomes = iter(critical_outcomes)
    monkeypatch.setattr(db, "_publish_critical",
                        lambda result, company: next(outcomes))
    # demoted criticals → _publish_light; curated quick reads → publish_quick_read
    monkeypatch.setattr(db, "_publish_light", lambda result: light_outcome)
    monkeypatch.setattr(db, "publish_quick_read",
                        lambda company, art: quick_read_outcome)


def test_curated_pins_criticals_and_fills_quick_reads(monkeypatch):
    _patch(monkeypatch, critical_outcomes=["published", "published", "published"])
    company = SimpleNamespace(slug="maruti-suzuki-india")
    criticals = [_art(i) for i in range(3)]
    lights = [_art(i) for i in range(10, 17)]  # 7 quick reads
    summary = db.build_curated_deck(company, criticals, lights, n_total=10)
    assert summary.critical_published == 3
    assert summary.light_published == 7
    tiers = [p["tier"] for p in summary.published_items]
    assert tiers.count("critical") == 3 and tiers.count("light") == 7


def test_curated_critical_failing_approval_demotes_to_light(monkeypatch):
    # 2nd critical fails approval → it should drop into the light tier.
    _patch(monkeypatch, critical_outcomes=["published", "rejected_approval", "published"])
    company = SimpleNamespace(slug="maruti-suzuki-india")
    criticals = [_art(i) for i in range(3)]
    summary = db.build_curated_deck(company, criticals, [], n_total=10)
    assert summary.critical_published == 2
    assert summary.approval_rejected == 1
    assert summary.light_published == 1  # the demoted one


def test_curated_light_capped_to_remaining_slots(monkeypatch):
    _patch(monkeypatch, critical_outcomes=["published", "published", "published"])
    company = SimpleNamespace(slug="maruti-suzuki-india")
    criticals = [_art(i) for i in range(3)]
    lights = [_art(i) for i in range(10, 30)]  # 20 offered
    summary = db.build_curated_deck(company, criticals, lights, n_total=10)
    assert summary.critical_published == 3
    assert summary.light_published == 7  # capped at n_total - criticals
