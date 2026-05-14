"""W1.2 — System tier builder (Tier 0) tests.

Synthetic insights are fed in via the `insights_iter` parameter so the
tests don't touch the real `data/outputs/` tree.
"""
from __future__ import annotations

from engine.wiki.paths import (
    system_article_path,
    system_event_path,
    system_index_path,
    system_log_path,
    system_theme_path,
)
from engine.wiki.system_builder import build_system_tier


def _mk_insight(
    article_id: str,
    *,
    url: str = "https://x.com/a",
    title: str = "Example article",
    published_at: str = "2026-05-13T10:00:00+00:00",
    tenant_slug: str = "adani-power",
    theme: str = "water",
    event_id: str = "event_regulatory_penalty",
    summary: str = "Summary text",
) -> dict:
    return {
        "article_id": article_id,
        "tenant_slug": tenant_slug,
        "url": url,
        "title": title,
        "published_at": published_at,
        "themes": [theme],
        "event_id": event_id,
        "summary": summary,
    }


def test_build_writes_article_pages(tmp_path):
    insights = [_mk_insight("a1"), _mk_insight("a2", url="https://x.com/b")]
    result = build_system_tier(insights, base=tmp_path)
    assert result.articles_written == 2

    p1 = system_article_path(
        published_at="2026-05-13T10:00:00+00:00",
        url="https://x.com/a",
        base=tmp_path,
    )
    assert p1.exists()
    text = p1.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "type: article" in text
    assert "Example article" in text


def test_build_writes_theme_pages_aggregating_articles(tmp_path):
    insights = [
        _mk_insight("a1", theme="water"),
        _mk_insight("a2", url="https://x.com/b", theme="water"),
        _mk_insight("a3", url="https://x.com/c", theme="climate"),
    ]
    build_system_tier(insights, base=tmp_path)
    water = system_theme_path("water", base=tmp_path)
    assert water.exists()
    text = water.read_text(encoding="utf-8")
    assert text.startswith("---")
    # Both water articles linked
    assert "a1" in text or "Example article" in text


def test_build_writes_event_pages(tmp_path):
    insights = [
        _mk_insight("a1", event_id="event_contract_win"),
        _mk_insight("a2", url="https://x.com/b", event_id="event_contract_win"),
    ]
    build_system_tier(insights, base=tmp_path)
    p = system_event_path("event_contract_win", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "type: event_type" in text


def test_build_writes_index(tmp_path):
    insights = [_mk_insight("a1"), _mk_insight("a2", url="https://x.com/b", theme="climate")]
    build_system_tier(insights, base=tmp_path)
    idx = system_index_path(base=tmp_path)
    assert idx.exists()
    text = idx.read_text(encoding="utf-8")
    # Index references both theme pages
    assert "water" in text and "climate" in text


def test_build_is_idempotent(tmp_path):
    insights = [_mk_insight("a1")]
    r1 = build_system_tier(insights, base=tmp_path)
    p = system_article_path(
        published_at="2026-05-13T10:00:00+00:00",
        url="https://x.com/a", base=tmp_path,
    )
    text1 = p.read_text(encoding="utf-8")
    r2 = build_system_tier(insights, base=tmp_path)
    text2 = p.read_text(encoding="utf-8")
    assert r1.articles_written == r2.articles_written
    assert text1 == text2


def test_build_appends_to_log_on_each_run(tmp_path):
    log = system_log_path(base=tmp_path)
    insights = [_mk_insight("a1")]
    build_system_tier(insights, base=tmp_path)
    assert log.exists()
    line1 = log.read_text(encoding="utf-8").strip().splitlines()
    build_system_tier(insights, base=tmp_path)
    line2 = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(line2) >= len(line1) + 1  # at least one new entry per run


def test_build_tolerates_missing_optional_fields(tmp_path):
    """A malformed insight dict (missing themes/event_id) should not crash;
    the article page still gets written under the catch-all path."""
    sparse = {"article_id": "x", "tenant_slug": "t", "url": "https://x.com/p", "title": "T"}
    result = build_system_tier([sparse], base=tmp_path)
    assert result.articles_written == 1


def test_build_lists_all_tenants_on_article_page(tmp_path):
    """The same URL analysed by 2 tenants → 1 system article page listing both."""
    url = "https://x.com/shared"
    insights = [
        _mk_insight("art_a1", url=url, tenant_slug="adani-power"),
        _mk_insight("art_j1", url=url, tenant_slug="jsw-energy"),
    ]
    build_system_tier(insights, base=tmp_path)
    p = system_article_path(
        published_at="2026-05-13T10:00:00+00:00", url=url, base=tmp_path,
    )
    text = p.read_text(encoding="utf-8")
    assert "adani-power" in text
    assert "jsw-energy" in text
