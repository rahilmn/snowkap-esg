"""W1.6 — Wiki BM25 search index tests."""
from __future__ import annotations

from engine.wiki.index import (
    SearchHit,
    WikiIndex,
    tier_of,
    tokenize,
)


def _seed(tmp_path):
    """Create a minimal 3-tier wiki at tmp_path/wiki/."""
    wiki = tmp_path / "wiki"
    (wiki / "system" / "themes").mkdir(parents=True)
    (wiki / "system" / "themes" / "water.md").write_text(
        "# Water\n\nWater scarcity, droughts, aquifer depletion.\n",
        encoding="utf-8",
    )
    (wiki / "system" / "themes" / "climate.md").write_text(
        "# Climate\n\nClimate change, carbon emissions, transition risk.\n",
        encoding="utf-8",
    )
    (wiki / "tenants" / "adani-power" / "themes").mkdir(parents=True)
    (wiki / "tenants" / "adani-power" / "themes" / "water.md").write_text(
        "# Adani Power → water\n\nWater stress impact on coal-fired plants.\n",
        encoding="utf-8",
    )
    (wiki / "users" / "alice" / "themes").mkdir(parents=True)
    (wiki / "users" / "alice" / "themes" / "climate.md").write_text(
        "# Alice → climate\n\nAlice reads about carbon and emissions.\n",
        encoding="utf-8",
    )
    return wiki


def test_tokenize_lowercases_and_keeps_alphanumeric():
    assert tokenize("Climate change, carbon emissions!") == ["climate", "change", "carbon", "emissions"]


def test_tokenize_handles_empty_and_punctuation():
    assert tokenize("") == []
    assert tokenize("---") == []


def test_tier_of_classifies_correctly(tmp_path):
    wiki = _seed(tmp_path)
    assert tier_of(wiki / "system" / "themes" / "water.md", wiki) == "system"
    assert tier_of(wiki / "tenants" / "adani-power" / "themes" / "water.md", wiki) == "tenant"
    assert tier_of(wiki / "users" / "alice" / "themes" / "climate.md", wiki) == "user"


def test_index_builds_over_full_wiki(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    # 4 pages indexed
    assert len(idx) == 4


def test_search_returns_hits_for_query(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    hits = idx.search("water scarcity")
    assert hits
    assert all(isinstance(h, SearchHit) for h in hits)
    # Top hit should be a water-themed page
    assert "water" in hits[0].path.name


def test_search_score_descending(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    hits = idx.search("water")
    assert len(hits) >= 2
    for i in range(len(hits) - 1):
        assert hits[i].score >= hits[i + 1].score


def test_search_filter_by_tier(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    sys_hits = idx.search("water", tier="system")
    for h in sys_hits:
        assert h.tier == "system"


def test_search_tenant_filter_routes_to_namespace(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    hits = idx.search("water", tier="tenant", tenant_slug="adani-power")
    assert hits
    for h in hits:
        assert h.tier == "tenant"
        assert "adani-power" in str(h.path)


def test_search_empty_query_returns_empty(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    assert idx.search("") == []
    assert idx.search("   ") == []


def test_search_no_match_returns_empty(tmp_path):
    wiki = _seed(tmp_path)
    idx = WikiIndex.build(wiki)
    assert idx.search("zorblattagonist") == []
