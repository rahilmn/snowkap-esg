"""W1.3 — Tenant tier (Tier 1) builder tests."""
from __future__ import annotations

from engine.wiki.paths import (
    tenant_article_path,
    tenant_belief_path,
    tenant_index_path,
    tenant_log_path,
    tenant_relations_path,
    tenant_theme_path,
)
from engine.wiki.tenant_builder import build_tenant_tier


def _mk_insight(article_id: str, **kw) -> dict:
    base = {
        "article_id": article_id,
        "tenant_slug": "adani-power",
        "url": f"https://x.com/{article_id}",
        "title": f"Article {article_id}",
        "published_at": "2026-05-13T10:00:00+00:00",
        "themes": ["water"],
        "event_id": "event_regulatory_penalty",
        "summary": "Summary",
        "materiality": "HIGH",
        "tier": "HOME",
        "decision_summary": {"financial_exposure": "₹500 Cr exposure"},
    }
    base.update(kw)
    return base


def test_build_writes_tenant_index(tmp_path):
    result = build_tenant_tier(
        tenant_slug="adani-power",
        insights=[_mk_insight("a1")],
        base=tmp_path,
    )
    p = tenant_index_path("adani-power", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "adani-power" in text
    assert result.articles_written == 1


def test_build_writes_per_article_tenant_pages(tmp_path):
    build_tenant_tier(
        tenant_slug="adani-power",
        insights=[_mk_insight("a1"), _mk_insight("a2")],
        base=tmp_path,
    )
    p1 = tenant_article_path("adani-power", "a1", base=tmp_path)
    p2 = tenant_article_path("adani-power", "a2", base=tmp_path)
    assert p1.exists() and p2.exists()
    text = p1.read_text(encoding="utf-8")
    assert "type: tenant_article" in text
    # Cross-tier link back to system article page
    assert "../../../system/articles/" in text


def test_build_writes_tenant_theme_pages(tmp_path):
    insights = [
        _mk_insight("a1", themes=["water"]),
        _mk_insight("a2", themes=["water", "climate"]),
        _mk_insight("a3", themes=["climate"]),
    ]
    build_tenant_tier(tenant_slug="adani-power", insights=insights, base=tmp_path)
    water = tenant_theme_path("adani-power", "water", base=tmp_path)
    climate = tenant_theme_path("adani-power", "climate", base=tmp_path)
    assert water.exists() and climate.exists()
    # Water page must list both water articles
    wtext = water.read_text(encoding="utf-8")
    assert "a1" in wtext and "a2" in wtext


def test_build_writes_relations_page(tmp_path):
    build_tenant_tier(
        tenant_slug="adani-power",
        insights=[_mk_insight("a1")],
        competitors=["jsw-energy", "adani-green"],
        base=tmp_path,
    )
    p = tenant_relations_path("adani-power", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "jsw-energy" in text
    assert "adani-green" in text


def test_build_writes_beliefs_page_from_company_agent_snapshot(tmp_path):
    # Pre-seed a CompanyAgent belief snapshot
    from engine.governance.belief_schema import RiskBandBelief
    from engine.governance.company_agent import CompanyAgent
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    agent.update_typed_belief(
        RiskBandBelief(topic="climate", band="HIGH", confidence_band="moderate"),
        rationale="test", actor="company_agent",
    )
    agent.dump_to_disk()

    build_tenant_tier(
        tenant_slug="adani-power",
        insights=[_mk_insight("a1")],
        beliefs_audit_dir=tmp_path,
        base=tmp_path,
    )
    p = tenant_belief_path("adani-power", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "risk_band:climate" in text
    assert "HIGH" in text


def test_build_is_idempotent(tmp_path):
    insights = [_mk_insight("a1")]
    build_tenant_tier(tenant_slug="adani-power", insights=insights, base=tmp_path)
    idx = tenant_index_path("adani-power", base=tmp_path)
    text1 = idx.read_text(encoding="utf-8")
    build_tenant_tier(tenant_slug="adani-power", insights=insights, base=tmp_path)
    text2 = idx.read_text(encoding="utf-8")
    assert text1 == text2


def test_build_only_processes_matching_tenant(tmp_path):
    """An insight from a DIFFERENT tenant should NOT leak into this tenant's pages."""
    insights = [
        _mk_insight("a1", tenant_slug="adani-power"),
        _mk_insight("j1", tenant_slug="jsw-energy"),
    ]
    build_tenant_tier(tenant_slug="adani-power", insights=insights, base=tmp_path)
    adani_a1 = tenant_article_path("adani-power", "a1", base=tmp_path)
    adani_j1 = tenant_article_path("adani-power", "j1", base=tmp_path)
    assert adani_a1.exists()
    assert not adani_j1.exists()  # jsw insight does NOT leak into adani tenant pages


def test_build_appends_to_tenant_log(tmp_path):
    build_tenant_tier(
        tenant_slug="adani-power", insights=[_mk_insight("a1")], base=tmp_path,
    )
    log = tenant_log_path("adani-power", base=tmp_path)
    assert log.exists()
    assert log.read_text(encoding="utf-8").strip()
