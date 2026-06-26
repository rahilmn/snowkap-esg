"""Phase 56.F — admin curated deck ingest (demo / manual override).

Pins hand-picked articles as criticals (full pipeline) and fills the quick-read
tier from the live fetch with forum/UGC noise filtered. These tests cover the
orchestration (no LLM / DB) by stubbing the publish helpers, plus the UGC
filter that keeps "I am planning to buy a car…" forum posts out of the deck.
"""
from __future__ import annotations

import json
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


def test_stamp_curated_card_overrides_fabricated_interpretation(monkeypatch):
    """Phase 56.G — an admin framework_interpretation replaces the engine's
    (sometimes hallucinated) prose on BOTH the article-level hit and each rec's
    hit, so no invented ₹ figure (e.g. CAFE-3's '~₹55 crore') survives."""
    existing = SimpleNamespace(
        personalised_analysis={
            "what_it_triggers": {
                "framework_hit": {
                    "framework": "BRSR", "principle_code": "BRSR:P6",
                    "interpretation": "…modeled penalty exposure ~₹55 crore at the engine level…",
                },
                "recommended_actions": [{"title": "Monitor — Energy (no action required yet)"}],
            },
        },
        criticality_score=0.9, criticality_band="CRITICAL",
    )
    captured = {}
    monkeypatch.setattr("engine.models.company_article_view.get",
                        lambda aid, slug: existing)
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(kw))
    clean = ("Under BRSR Principle 6, the draft CAFE-3 norms are a material "
             "regulatory-transition risk; no specific penalty figure should be "
             "stated while the norms remain in draft.")
    ok = db.stamp_curated_card(
        SimpleNamespace(slug="maruti-suzuki-india"), "aid1",
        recommendations=[{"title": "Model CAFE-3 fleet-CO2 gap", "type": "compliance"}],
        framework_interpretation=clean,
    )
    assert ok is True
    wit = captured["personalised_analysis"]["what_it_triggers"]
    assert wit["framework_hit"]["interpretation"] == clean          # article-level overridden
    assert "55 crore" not in wit["framework_hit"]["interpretation"]  # fabrication gone
    assert wit["recommended_actions"][0]["framework_hit"]["interpretation"] == clean  # per-rec too


def test_publish_curated_critical_direct_uses_supplied_interpretation(monkeypatch):
    """The direct-write fallback prefers the admin's fact-checked interpretation
    over its number-free template."""
    captured = {}
    monkeypatch.setattr("engine.models.article_pool.upsert",
                        lambda **kw: captured.update(pool=kw))
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(view=kw))
    art = SimpleNamespace(
        id="d1", url="http://x/d", title="Maruti recall", source="X",
        published_at="2026-06-20T00:00:00+00:00", content="recall body",
        metadata={},
    )
    company = SimpleNamespace(slug="maruti-suzuki-india", industry="Automotive",
                             name="Maruti Suzuki India Limited", framework_region="INDIA")
    clean = "Under BRSR Principle 9, Maruti must disclose this recall in its product-safety metrics."
    ok = db.publish_curated_critical_direct(
        company, art, recommendations=[{"title": "Complete the replacement"}],
        principle_code="BRSR:P9", principle_title="Principle 9 — Consumer Responsibility",
        framework_interpretation=clean,
    )
    assert ok is True
    fh = captured["view"]["personalised_analysis"]["what_it_triggers"]["framework_hit"]
    assert fh["interpretation"] == clean


def test_stamp_curated_insight_patches_detail_payload(monkeypatch):
    """Phase 56.H — the swipe-up detail view reads insight_payload (deep_insight),
    not the deck card. stamp_curated_insight must overlay the curated recs +
    clean framework_hit + key-risk onto insight.analysis.what_it_triggers so the
    detail matches the card and the fabricated figure is gone."""
    payload = {
        "company_slug": "maruti-suzuki-india",
        "insight": {
            "decision_summary": {"materiality": "CRITICAL"},
            "analysis": {
                "why_it_matters": {"stakes_for_company": "Carries potential downside…"},
                "what_it_triggers": {
                    "framework_hit": {
                        "framework": "BRSR", "principle_code": "BRSR:P6",
                        "interpretation": "…modeled penalty exposure ~₹55 crore at the engine level…",
                    },
                    "recommended_actions": [
                        {"title": "File PMO Representation Restoring Small-Car CAFE-3 Credit Concessions"}
                    ],
                },
            },
        },
    }
    captured = {}
    monkeypatch.setattr("engine.models.insight_payload.get", lambda aid: payload)
    monkeypatch.setattr("engine.models.insight_payload.upsert",
                        lambda aid, slug, p: captured.update(aid=aid, slug=slug, payload=p))
    clean = ("Under BRSR Principle 6, the draft CAFE-3 norms are a material regulatory-transition "
             "risk; no specific penalty figure should be stated while the norms remain in draft.")
    ok = db.stamp_curated_insight(
        "fdb24e6ff3226d25", "maruti-suzuki-india",
        recommendations=[{"title": "Model the FY27 CAFE-3 fleet-CO2 gap + per-car penalty exposure",
                          "owner": "ESG / Finance", "type": "compliance"}],
        key_risk="Hundreds of crores in CAFE-3 penalties if the fleet misses 3.73->3.01 L/100km.",
        framework_interpretation=clean,
    )
    assert ok is True
    wit = captured["payload"]["insight"]["analysis"]["what_it_triggers"]
    assert wit["recommended_actions"][0]["title"].startswith("Model the FY27")  # curated rec
    assert wit["framework_hit"]["interpretation"] == clean                       # article-level clean
    assert "55 crore" not in wit["framework_hit"]["interpretation"]              # fabrication gone
    assert wit["recommended_actions"][0]["framework_hit"]["interpretation"] == clean  # per-rec clean
    wim = captured["payload"]["insight"]["analysis"]["why_it_matters"]
    assert "CAFE-3 penalties" in wim["stakes_for_company"]                       # key-risk overlaid


def test_scrub_engine_exposure_strips_fabricated_rupee_figures():
    """Phase 56.H — remove the engine's '~₹55 Cr modeled exposure (engine
    estimate)' from every engine field, KEEP the '(engine estimate)' disclosure
    and the article-sourced 'hundreds of crores' qualitative framing, and NEVER
    touch the source article body."""
    payload = {
        "article": {"body": "Penalties could reach ~₹55 Cr per the filing."},  # source — untouched
        "insight": {
            "decision_summary": {
                "financial_exposure": "~₹55 Cr modeled direct transition exposure (engine estimate); "
                                      "article states penalties could reach hundreds of crores at fleet scale",
                "key_risk": "~₹55 Cr modeled direct exposure (engine estimate) rising to hundreds of crores",
            },
            "financial_timeline": {"immediate": {
                "headline": "~₹55 Cr modeled regulatory transition exposure (engine estimate) as CAFE-3 advances"}},
            "impact_analysis": {"valuation_cashflow":
                "Penalty exposure of hundreds of crores (engine estimate: ~₹50–60 Cr modeled direct exposure)"},
        },
        "recommendations": {"validated_recommendations": [
            {"profitability_link": "compresses margin by approximately 0.4-1 bps per ₹55 Cr of direct exposure"}]},
    }
    out = db._scrub_engine_exposure(payload)
    # Engine-generated subtrees must be free of the fabricated figure …
    engine_blob = json.dumps({"insight": out["insight"], "recommendations": out["recommendations"]},
                             ensure_ascii=False)
    assert "55 Cr" not in engine_blob and "₹55" not in engine_blob and "50–60 Cr" not in engine_blob
    assert "engine estimate" in out["insight"]["decision_summary"]["financial_exposure"]  # disclosure kept
    assert "hundreds of crores" in out["insight"]["decision_summary"]["financial_exposure"]  # sourced framing kept
    assert "modeled regulatory transition exposure" in out["insight"]["financial_timeline"]["immediate"]["headline"]
    assert out["article"]["body"] == "Penalties could reach ~₹55 Cr per the filing."  # SOURCE untouched
    # no dangling double-spaces / orphaned punctuation
    assert "  " not in out["insight"]["decision_summary"]["financial_exposure"]


def test_stamp_curated_insight_noop_when_no_payload(monkeypatch):
    """No insight payload row → no-op (returns False), never raises."""
    monkeypatch.setattr("engine.models.insight_payload.get", lambda aid: None)
    ok = db.stamp_curated_insight("missing", "slug",
                                  recommendations=[{"title": "x"}],
                                  framework_interpretation="y")
    assert ok is False


def test_publish_curated_critical_direct_writes_full_card(monkeypatch):
    """Fallback when the pipeline fails to write a curated critical: builds the
    article_pool + company_article_view rows directly (critical tier, lede, recs,
    image, BRSR chip) so the card reliably shows."""
    captured = {}
    monkeypatch.setattr("engine.models.article_pool.upsert",
                        lambda **kw: captured.update(pool=kw))
    monkeypatch.setattr("engine.models.company_article_view.upsert",
                        lambda **kw: captured.update(view=kw))
    art = SimpleNamespace(
        id="rev1", url="http://x/rev", title="Maruti Q4: profit down 6.45%",
        source="Whalesbook", published_at="2026-04-29T00:00:00+00:00",
        content="Net profit declined 6.45% to Rs 3,659 crore on margin pressure.",
        metadata={"image_url": "https://img/rev.jpg"},
    )
    company = SimpleNamespace(slug="maruti-suzuki-india", industry="Automotive",
                             name="Maruti Suzuki India Limited", framework_region="INDIA")
    ok = db.publish_curated_critical_direct(
        company, art,
        recommendations=[{"title": "Defend operating margin", "owner": "Finance", "type": "financial"}],
        key_risk="Net profit down 6.45% on margin pressure",
        principle_code="BRSR:P1", principle_title="Principle 1 — Ethical Conduct",
    )
    assert ok is True
    pe = captured["view"]["personalised_analysis"]
    assert pe["tier"] == "critical" and pe.get("lede", {}).get("text")  # tier=critical via lede
    actions = pe["what_it_triggers"]["recommended_actions"]
    assert actions[0]["title"] == "Defend operating margin"
    assert actions[0]["framework_hit"]["principle_code"] == "BRSR:P1"
    assert captured["view"]["criticality_band"] == "CRITICAL"
    assert captured["pool"]["shared_analysis"]["image_url"] == "https://img/rev.jpg"
