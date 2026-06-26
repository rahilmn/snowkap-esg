"""Phase 56.J — per-tenant deck freeze + hero-image override (demo protection).

Freeze: the weekly Sunday refresh + overnight batch both flow through
``build_company_deck``; a frozen tenant must no-op there so a hand-curated
deck survives. Image: a dead/404 source image can be replaced, writing
shared_analysis (the field the feed reads first) + the per-company view.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.analysis import deck_builder as db
from engine.models import deck_freeze


# ---------------------------------------------------------------------------
# deck_freeze model (round-trips against the conftest SQLite DB)
# ---------------------------------------------------------------------------


def test_deck_freeze_roundtrip():
    slug = "maruti-suzuki-india"
    assert deck_freeze.is_frozen(slug) is False           # default: not frozen
    deck_freeze.set_frozen(slug, True, reason="tuesday demo")
    assert deck_freeze.is_frozen(slug) is True
    assert any(r["slug"] == slug for r in deck_freeze.list_frozen())
    deck_freeze.set_frozen(slug, False)                   # un-freeze
    assert deck_freeze.is_frozen(slug) is False
    assert all(r["slug"] != slug for r in deck_freeze.list_frozen())


def test_is_frozen_empty_slug_is_false():
    assert deck_freeze.is_frozen("") is False


# ---------------------------------------------------------------------------
# build_company_deck freeze guard — the single chokepoint
# ---------------------------------------------------------------------------


def test_build_company_deck_noops_when_frozen(monkeypatch):
    monkeypatch.setattr("engine.models.deck_freeze.is_frozen", lambda slug: True)
    # If the guard fails, the pipeline would try to run on this candidate and
    # raise (no real LLM/DB) — so a clean no-op proves the freeze short-circuits.
    company = SimpleNamespace(slug="maruti-suzuki-india", industry="Automotive")
    candidates = [SimpleNamespace(id="a1", title="x", url="http://x/1")]
    summary = db.build_company_deck(company, candidates, n_critical=3, n_total=10)
    assert "frozen" in summary.errors
    assert summary.processed == 0
    assert summary.critical_published == 0 and summary.light_published == 0


def test_build_company_deck_runs_when_not_frozen(monkeypatch):
    """The guard must NOT short-circuit a normal (un-frozen) tenant — empty
    candidates is the cheapest path past the guard."""
    monkeypatch.setattr("engine.models.deck_freeze.is_frozen", lambda slug: False)
    company = SimpleNamespace(slug="yes-bank", industry="Banks")
    summary = db.build_company_deck(company, [], n_critical=3, n_total=10)
    assert "frozen" not in summary.errors        # reached the normal no-candidates return


# ---------------------------------------------------------------------------
# set_article_image — shared_analysis (feed reads it first) + the view
# ---------------------------------------------------------------------------


def test_set_article_image_updates_shared_and_view(monkeypatch):
    captured = {}
    pool_row = SimpleNamespace(
        id="qr6", url="http://x/ciaz", title="Ciaz discontinued", source="V3Cars",
        published_at="2026-06-20T00:00:00+00:00", primary_industry="Automotive",
        material_industries=["Automotive"], primary_pillar=None, primary_theme=None,
        event_id=None, event_polarity="neutral",
        shared_analysis={"image_url": "https://dead//media/404.webp", "what_changed": {}},
    )
    view_row = SimpleNamespace(
        personalised_analysis={"image_url": "https://dead//media/404.webp"},
        criticality_score=0.3, criticality_band="LOW",
    )
    monkeypatch.setattr("engine.models.article_pool.get", lambda aid: pool_row)
    monkeypatch.setattr("engine.models.article_pool.upsert",
                        lambda **kw: captured.update(pool=kw))
    monkeypatch.setattr("engine.models.company_article_view.get", lambda aid, slug: view_row)
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(view=kw))
    good = "https://assets.v3cars.com/media/content/og-imgs/426479-ciaz.webp"
    ok = db.set_article_image("maruti-suzuki-india", "qr6", good)
    assert ok is True
    # shared_analysis (primary) updated, other keys preserved
    assert captured["pool"]["shared_analysis"]["image_url"] == good
    assert "what_changed" in captured["pool"]["shared_analysis"]
    assert captured["pool"]["article_id"] == "qr6"           # mapped from row.id
    # per-company view updated too, band/score preserved
    assert captured["view"]["personalised_analysis"]["image_url"] == good
    assert captured["view"]["criticality_band"] == "LOW"


def test_set_article_image_rejects_empty():
    assert db.set_article_image("slug", "", "http://x") is False
    assert db.set_article_image("slug", "aid", "") is False


# ---------------------------------------------------------------------------
# company_article_view.delete_one — surgical single-card removal
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self


class _FakeCM:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


def test_delete_one_removes_existing_returns_true(monkeypatch):
    from engine.models import company_article_view as cav
    conn = _FakeConn()
    monkeypatch.setattr(cav, "_db_connect", lambda: _FakeCM(conn))
    monkeypatch.setattr(cav, "get", lambda aid, slug: object())   # row exists
    assert cav.delete_one("ugc1", "maruti-suzuki-india") is True
    sql, params = conn.executed[0]
    assert "DELETE FROM company_article_view" in sql
    assert params == ("ugc1", "maruti-suzuki-india")             # both keys bound


def test_delete_one_missing_returns_false(monkeypatch):
    from engine.models import company_article_view as cav
    conn = _FakeConn()
    monkeypatch.setattr(cav, "_db_connect", lambda: _FakeCM(conn))
    monkeypatch.setattr(cav, "get", lambda aid, slug: None)       # row absent
    assert cav.delete_one("nope", "maruti-suzuki-india") is False
    assert cav.delete_one("", "maruti-suzuki-india") is False     # guard, no DB call
