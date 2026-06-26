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


# ---------------------------------------------------------------------------
# Article-level framework hit (Phase 56.F) — shown even with no recs
# ---------------------------------------------------------------------------


def test_clean_framework_prose_no_redundant_code():
    from engine.analysis.unified_analysis import _clean_framework_prose
    anchor = {
        "framework": "BRSR", "principle_code": "BRSR:P6",
        "principle_title": "Principle 6 — Environmental Protection", "mandatory": True,
    }
    p = _clean_framework_prose(anchor, "Energy", "Maruti Suzuki India Limited")
    assert "Principle 6" in p and "mandatory" in p
    assert "BRSR BRSR" not in p            # no redundant "BRSR BRSR:P6"
    assert "Energy development" in p


def test_article_framework_hit_surfaces_principle_without_recs(monkeypatch):
    from engine.analysis import unified_analysis as ua
    monkeypatch.setattr(
        "engine.config.get_company",
        lambda slug: SimpleNamespace(
            name="Maruti Suzuki India Limited", framework_region="INDIA",
            slug=slug, market_cap="Large Cap",
        ),
    )
    monkeypatch.setattr(
        "engine.analysis.recommendation_engine._framework_hit_anchor",
        lambda result, company: {
            "framework": "BRSR", "principle_code": "BRSR:P6",
            "principle_title": "Principle 6 — Environmental Protection",
            "mandatory": True, "region": "INDIA",
        },
    )
    result = SimpleNamespace(
        company_slug="maruti-suzuki-india",
        themes=SimpleNamespace(primary_theme="Energy"),
    )
    fh = ua._article_framework_hit(result, [])  # NO recs
    assert fh is not None
    assert fh["framework"] == "BRSR" and fh["principle_code"] == "BRSR:P6"
    assert fh["mandatory"] is True
    assert len(fh["interpretation"]) >= 25 and "BRSR BRSR" not in fh["interpretation"]


# ---------------------------------------------------------------------------
# Phase 56.F — hero image (metadata) + admin-curated recommendations
# ---------------------------------------------------------------------------


def test_publish_quick_read_reads_image_from_metadata(monkeypatch):
    """The NewsAPI hero image lives in IngestedArticle.metadata['image_url'];
    publish_quick_read must surface it (not the missing .image_url attr)."""
    captured = {}
    monkeypatch.setattr("engine.models.article_pool.upsert",
                        lambda **kw: captured.update(pool=kw))
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(view=kw))
    art = SimpleNamespace(
        id="qr1", url="http://x/1", title="Maruti price hike", source="CarDekho",
        published_at="2026-06-20T00:00:00+00:00", content="body text",
        summary="s", metadata={"image_url": "https://img/hero.jpg"},
    )
    out = db.publish_quick_read(SimpleNamespace(slug="maruti-suzuki-india", industry="Automotive"), art)
    assert out == "published"
    assert captured["pool"]["shared_analysis"].get("image_url") == "https://img/hero.jpg"
    assert captured["view"]["personalised_analysis"].get("image_url") == "https://img/hero.jpg"


def test_stamp_curated_card_overlays_recommendations(monkeypatch):
    """Admin-supplied recommendations overlay the degraded engine's monitor rec,
    keeping the article-level framework_hit and re-pinning band=CRITICAL."""
    existing = SimpleNamespace(
        personalised_analysis={
            "what_it_triggers": {
                "framework_hit": {"framework": "BRSR", "principle_code": "BRSR:P6"},
                "recommended_actions": [{"title": "Monitor — Energy (no action required yet)"}],
            },
            "why_it_matters": {"criticality_summary": "x"},
        },
        criticality_score=0.9, criticality_band="CRITICAL",
    )
    captured = {}
    monkeypatch.setattr("engine.models.company_article_view.get",
                        lambda aid, slug: existing)
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(kw))
    ok = db.stamp_curated_card(
        SimpleNamespace(slug="maruti-suzuki-india"), "aid1",
        recommendations=[
            {"title": "Model CAFE-3 compliance gap + penalty exposure", "owner": "ESG", "type": "compliance"},
            {"title": "Accelerate flex-fuel mix to bank super-credits", "type": "strategic"},
        ],
        key_risk="Hundreds of crores of CAFE-3 penalty exposure",
    )
    assert ok is True
    actions = captured["personalised_analysis"]["what_it_triggers"]["recommended_actions"]
    assert len(actions) == 2
    assert actions[0]["title"].startswith("Model CAFE-3")
    assert actions[0]["framework_hit"]["principle_code"] == "BRSR:P6"  # kept
    assert captured["criticality_band"] == "CRITICAL"  # re-pinned
    assert "penalty exposure" in captured["personalised_analysis"]["why_it_matters"]["stakes_for_company"]
