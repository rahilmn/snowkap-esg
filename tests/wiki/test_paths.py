"""W1.1 — Wiki path conventions (foundation for all 3 tiers)."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.wiki.paths import (
    WIKI_ROOT_DIRNAME,
    article_hash,
    relative_link,
    slugify,
    system_article_path,
    system_entity_path,
    system_event_path,
    system_theme_path,
    tenant_article_path,
    tenant_belief_path,
    tenant_index_path,
    tenant_theme_path,
    user_history_path,
    user_painpoints_path,
    user_root,
    user_theme_path,
    wiki_root,
)


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


def test_slugify_lowercase_and_dashes():
    assert slugify("Water Stress") == "water-stress"
    assert slugify("Supply-Chain Labor") == "supply-chain-labor"


def test_slugify_strips_punctuation_and_dedupes_dashes():
    assert slugify("Adani Power Ltd. (NSE)") == "adani-power-ltd-nse"
    assert slugify("---spaces  --  in--between") == "spaces-in-between"


def test_slugify_handles_empty_and_unicode():
    assert slugify("") == ""
    # NFKD decomposes ü → u+combining-umlaut; ASCII-drop keeps 'u'
    assert slugify("Adani über") == "adani-uber"
    # Pure non-ASCII (no decomposable base) drops to empty
    assert slugify("中文") == ""


def test_article_hash_is_deterministic_and_short():
    h1 = article_hash("https://example.com/a")
    h2 = article_hash("https://example.com/a")
    assert h1 == h2
    assert 8 <= len(h1) <= 16
    # Different URLs → different hashes
    assert article_hash("https://example.com/b") != h1


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def test_wiki_root_resolves_to_repo_top_level(tmp_path):
    root = wiki_root(base=tmp_path)
    assert root == tmp_path / WIKI_ROOT_DIRNAME
    # mkdir=True creates it
    root2 = wiki_root(base=tmp_path, mkdir=True)
    assert root2.exists() and root2.is_dir()


def test_user_root_includes_user_id(tmp_path):
    p = user_root(user_id="alice@snowkap.com", base=tmp_path)
    # The path must include a slug-safe form of the email
    assert "alice" in str(p)
    # And be under wiki/users/
    assert "users" in p.parts


# ---------------------------------------------------------------------------
# System tier paths
# ---------------------------------------------------------------------------


def test_system_article_path_partitions_by_year_month(tmp_path):
    p = system_article_path(
        published_at="2026-05-13T10:00:00+00:00",
        url="https://example.com/article",
        base=tmp_path,
    )
    # Path: wiki/system/articles/2026/05/<hash>.md
    parts = p.parts
    assert "system" in parts
    assert "articles" in parts
    assert "2026" in parts
    assert "05" in parts
    assert p.suffix == ".md"


def test_system_article_path_fallback_when_published_at_missing(tmp_path):
    """When published_at is missing/invalid, bucket under 0000/00 (catch-all)."""
    p = system_article_path(
        published_at=None, url="https://x.com/a", base=tmp_path,
    )
    assert "0000" in p.parts
    assert "00" in p.parts


def test_system_theme_path(tmp_path):
    p = system_theme_path(theme="water", base=tmp_path)
    assert p.parts[-3:] == ("system", "themes", "water.md")


def test_system_entity_path_slugifies(tmp_path):
    p = system_entity_path(entity="Securities and Exchange Board of India", base=tmp_path)
    assert "securities-and-exchange-board-of-india.md" in str(p)


def test_system_event_path(tmp_path):
    p = system_event_path(event_type="event_regulatory_penalty", base=tmp_path)
    assert p.parts[-3:] == ("system", "events", "event_regulatory_penalty.md")


# ---------------------------------------------------------------------------
# Tenant tier paths
# ---------------------------------------------------------------------------


def test_tenant_paths_namespaced_by_slug(tmp_path):
    assert "adani-power" in str(tenant_index_path("adani-power", base=tmp_path))
    assert "adani-power" in str(tenant_article_path("adani-power", "art_1", base=tmp_path))
    assert "adani-power" in str(tenant_theme_path("adani-power", "water", base=tmp_path))
    assert "adani-power" in str(tenant_belief_path("adani-power", base=tmp_path))


def test_tenant_article_path_uses_article_id(tmp_path):
    p = tenant_article_path("adani-power", "art_xyz_123", base=tmp_path)
    assert "art_xyz_123.md" in str(p)


# ---------------------------------------------------------------------------
# User tier paths
# ---------------------------------------------------------------------------


def test_user_paths_namespaced_by_user(tmp_path):
    p1 = user_painpoints_path("alice@snowkap.com", base=tmp_path)
    p2 = user_history_path("alice@snowkap.com", base=tmp_path)
    p3 = user_theme_path("alice@snowkap.com", "water", base=tmp_path)
    # All under the same user dir
    user_dir = user_root("alice@snowkap.com", base=tmp_path)
    assert user_dir in p1.parents
    assert user_dir in p2.parents
    assert user_dir in p3.parents


def test_two_users_get_separate_dirs(tmp_path):
    a = user_root("alice@x.com", base=tmp_path)
    b = user_root("bob@x.com", base=tmp_path)
    assert a != b


# ---------------------------------------------------------------------------
# Cross-tier relative links
# ---------------------------------------------------------------------------


def test_relative_link_user_to_tenant(tmp_path):
    src = user_theme_path("alice@x.com", "water", base=tmp_path)
    dst = tenant_theme_path("adani-power", "water", base=tmp_path)
    rel = relative_link(src, dst)
    # Should walk up out of users/<id>/themes/ then into tenants/<slug>/themes/
    assert rel.endswith("water.md")
    assert ".." in rel


def test_relative_link_tenant_to_system(tmp_path):
    src = tenant_theme_path("adani-power", "water", base=tmp_path)
    dst = system_theme_path("water", base=tmp_path)
    rel = relative_link(src, dst)
    assert rel.endswith("system/themes/water.md") or rel.endswith("system\\themes\\water.md")


def test_relative_link_uses_posix_separators_on_windows(tmp_path):
    """Wiki markdown links must use forward slashes for portability."""
    src = user_theme_path("alice@x.com", "water", base=tmp_path)
    dst = system_theme_path("water", base=tmp_path)
    rel = relative_link(src, dst)
    assert "\\" not in rel
