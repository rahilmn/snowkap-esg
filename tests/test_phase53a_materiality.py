"""Phase 53 (A) — revive the SASB sector-materiality overlay.

The bug: query_materiality_weight canonicalised a topic to its human rdfs:label
("Climate Change") and passed THAT to the SASB loader, whose
STRENDS(STR(?topic), <normalised label>) match against snowkap:topic_<suffix>
("topic_climate") then missed for 17/21 topics — silently collapsing the SASB
overlay to the 0.5/base default. These tests pin the fix (label→suffix map) and
the new per-sector material-topic primitive.
"""
from __future__ import annotations

import pytest

from engine.ontology.materiality_aliases import canonical_sasb_topic
from engine.ontology.intelligence import query_materiality_weight
from engine.ontology.sasb_loader import query_material_topics_for_sector


def test_canonical_sasb_topic_maps_label_to_suffix():
    assert canonical_sasb_topic("Climate Change") == "climate"
    assert canonical_sasb_topic("Data Privacy & Security") == "data_privacy"
    assert canonical_sasb_topic("Waste & Circularity") == "waste"
    assert canonical_sasb_topic("Diversity, Equity & Inclusion") == "dei"
    # free-text aliases resolve through canonical_topic first
    assert canonical_sasb_topic("carbon") == "emissions"
    assert canonical_sasb_topic("cybersecurity") == "data_privacy"
    # unknown topic falls back to the canonical label (loader then misses cleanly)
    assert canonical_sasb_topic("Totally Unknown Topic") == "Totally Unknown Topic"


@pytest.mark.parametrize("topic,expected", [
    ("Climate Change", 0.95),       # was 0.8 (base) — the headline regression
    ("Data Privacy & Security", 0.90),  # was 0.5 (neutral default)
    ("Emissions", 0.90),
    ("Ethics & Compliance", 0.85),
    ("Stakeholder Governance", 0.80),
    ("Supply Chain Labor", 0.65),
])
def test_commercial_banks_sasb_weights_fire(topic, expected):
    w = query_materiality_weight(topic, "Financials/Banking", sasb_sector="Commercial Banks")
    assert w == pytest.approx(expected), f"{topic} should hit SASB {expected}, got {w}"


def test_material_topics_for_sector_ordered_by_weight():
    topics = query_material_topics_for_sector("Commercial Banks")
    assert topics, "Commercial Banks must have material topics"
    suffixes = [t[0] for t in topics]
    weights = [t[1] for t in topics]
    assert suffixes[0] == "climate" and weights[0] == pytest.approx(0.95)
    assert "data_privacy" in suffixes and "emissions" in suffixes
    assert weights == sorted(weights, reverse=True)  # descending


def test_new_sectors_present():
    # Phase 53 (A3) — the 5 sectors the resolver mapped to but the TTL lacked.
    for sector in ("Real Estate", "Telecommunication Services", "Aerospace & Defense",
                   "Household & Personal Products", "Industrial Conglomerates"):
        assert query_material_topics_for_sector(sector), f"{sector} must resolve"


def test_every_resolver_industry_resolves():
    """The 'any onboarded company' guarantee: every industry the resolver can emit
    maps to a non-empty SASB material-topic set."""
    from engine.ingestion.llm_company_resolver import INDUSTRY_TO_SASB_DEFAULT
    empty = [(ind, sasb) for ind, sasb in INDUSTRY_TO_SASB_DEFAULT.items()
             if not query_material_topics_for_sector(sasb)]
    assert not empty, f"industries with no material topics: {empty}"
