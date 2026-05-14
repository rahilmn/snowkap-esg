"""W1.4 — User tier (Tier 2) builder tests."""
from __future__ import annotations

from engine.wiki.paths import (
    user_history_path,
    user_index_path,
    user_log_path,
    user_painpoints_path,
    user_saved_path,
    user_theme_path,
)
from engine.wiki.user_builder import build_user_tier


def _mk_history_entry(article_id: str, **kw):
    base = {
        "article_id": article_id,
        "tenant_slug": "adani-power",
        "url": f"https://x.com/{article_id}",
        "title": f"Article {article_id}",
        "published_at": "2026-05-13T10:00:00+00:00",
        "themes": ["water"],
        "read_at": "2026-05-14T08:00:00+00:00",
    }
    base.update(kw)
    return base


def test_build_writes_user_index(tmp_path):
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[],
        saved=[],
        persona=None,
        base=tmp_path,
    )
    p = user_index_path("alice@snowkap.com", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "alice" in text.lower()


def test_build_writes_painpoints_from_persona(tmp_path):
    persona = {
        "role": "analyst",
        "esg_focus": ["climate", "labour"],
        "frameworks": ["BRSR", "CSRD"],
        "geographies": ["IN", "EU"],
        "horizon": "12m",
        "risk_appetite": "medium",
    }
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[],
        saved=[],
        persona=persona,
        base=tmp_path,
    )
    p = user_painpoints_path("alice@snowkap.com", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "climate" in text
    assert "BRSR" in text


def test_build_writes_painpoints_empty_when_no_persona(tmp_path):
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[],
        saved=[],
        persona=None,
        base=tmp_path,
    )
    p = user_painpoints_path("alice@snowkap.com", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "not yet" in text.lower() and "persona" in text.lower()


def test_build_writes_history_ordered_by_read_at(tmp_path):
    history = [
        _mk_history_entry("a1", read_at="2026-05-10T00:00:00+00:00"),
        _mk_history_entry("a2", read_at="2026-05-14T00:00:00+00:00"),
        _mk_history_entry("a3", read_at="2026-05-12T00:00:00+00:00"),
    ]
    build_user_tier(
        user_id="alice@snowkap.com",
        history=history,
        saved=[],
        persona=None,
        base=tmp_path,
    )
    p = user_history_path("alice@snowkap.com", base=tmp_path)
    text = p.read_text(encoding="utf-8")
    # a2 (most recent) should appear before a3 before a1
    a2_idx = text.index("a2")
    a3_idx = text.index("a3")
    a1_idx = text.index("a1")
    assert a2_idx < a3_idx < a1_idx


def test_build_writes_saved_articles(tmp_path):
    saved = [
        _mk_history_entry("s1", title="Saved 1"),
        _mk_history_entry("s2", title="Saved 2"),
    ]
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[],
        saved=saved,
        persona=None,
        base=tmp_path,
    )
    p = user_saved_path("alice@snowkap.com", base=tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "Saved 1" in text and "Saved 2" in text


def test_build_writes_user_theme_pages_from_history(tmp_path):
    history = [
        _mk_history_entry("a1", themes=["water"]),
        _mk_history_entry("a2", themes=["water", "climate"]),
        _mk_history_entry("a3", themes=["climate"]),
    ]
    build_user_tier(
        user_id="alice@snowkap.com",
        history=history,
        saved=[],
        persona=None,
        base=tmp_path,
    )
    p = user_theme_path("alice@snowkap.com", "water", base=tmp_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # Both water-themed entries in history must appear
    assert "a1" in text and "a2" in text


def test_build_user_theme_page_links_back_to_tenant_and_system(tmp_path):
    history = [_mk_history_entry("a1", themes=["water"], tenant_slug="adani-power")]
    build_user_tier(
        user_id="alice@snowkap.com",
        history=history,
        saved=[],
        persona=None,
        base=tmp_path,
    )
    p = user_theme_path("alice@snowkap.com", "water", base=tmp_path)
    text = p.read_text(encoding="utf-8")
    # Cross-tier links: up to system + tenant
    assert "../../../system/themes/water.md" in text
    assert "../../../tenants/adani-power/themes/water.md" in text


def test_build_is_idempotent(tmp_path):
    persona = {"role": "analyst", "esg_focus": ["climate"]}
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[_mk_history_entry("a1")],
        saved=[],
        persona=persona,
        base=tmp_path,
    )
    idx = user_index_path("alice@snowkap.com", base=tmp_path)
    text1 = idx.read_text(encoding="utf-8")
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[_mk_history_entry("a1")],
        saved=[],
        persona=persona,
        base=tmp_path,
    )
    text2 = idx.read_text(encoding="utf-8")
    assert text1 == text2


def test_build_appends_log(tmp_path):
    build_user_tier(
        user_id="alice@snowkap.com",
        history=[_mk_history_entry("a1")],
        saved=[], persona=None, base=tmp_path,
    )
    log = user_log_path("alice@snowkap.com", base=tmp_path)
    assert log.exists() and log.read_text(encoding="utf-8").strip()
