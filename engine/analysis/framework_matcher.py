"""Framework matcher — query the ontology to find applicable frameworks.

Replaces legacy ``framework_rag.py`` (98 KB of hardcoded Python). All
framework knowledge lives in ``knowledge_base.ttl``. This module only
traverses the graph.

Pipeline per article:
1. For the primary + secondary themes, query ``triggersFramework`` edges.
2. For each framework, fetch label + profitability link.
3. Boost frameworks based on company region / market cap (mandatory flags).
4. Return a ranked list of :class:`FrameworkMatch`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.nlp.theme_tagger import ESGThemeTags
from engine.ontology.intelligence import (
    FrameworkRef,
    query_compliance_deadlines,
    query_frameworks_detail,
    query_mandatory_rules,
    query_regional_boosts,
)

logger = logging.getLogger(__name__)

# Regional boosts and mandatory rules are now ontology-driven.
# See knowledge_expansion.ttl: RegionalFrameworkBoost / MandatoryRule triples.


@dataclass
class FrameworkMatch:
    framework_id: str
    framework_label: str
    relevance: float  # 0.0-1.0
    is_mandatory: bool
    profitability_link: str
    triggered_by_themes: list[str] = field(default_factory=list)
    applicable_deadlines: list[str] = field(default_factory=list)
    triggered_sections: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _region_key(country: str, region: str) -> str:
    country = (country or "").lower()
    region = (region or "").lower()
    if "india" in country:
        return "INDIA"
    if "europ" in country or "eu" in region:
        return "EU"
    if "us" in country or "united states" in country or "america" in region:
        return "US"
    return "GLOBAL"


def match_frameworks(
    tags: ESGThemeTags,
    company_industry: str,
    company_country: str,
    company_region: str,
    market_cap: str,
) -> tuple[list[FrameworkMatch], int]:
    """Return frameworks triggered by the article's themes for this company.

    Returns ``(matches, ontology_query_count)``.
    """
    themes: list[str] = []
    if tags.primary_theme:
        themes.append(tags.primary_theme)
    for sec in tags.secondary_themes:
        if sec.get("theme"):
            themes.append(sec["theme"])

    # Collect triggered frameworks per theme
    collected: dict[str, FrameworkMatch] = {}
    queries = 0
    for theme in themes:
        refs = query_frameworks_detail(theme)
        queries += 1
        base_weight = 1.0 if theme == tags.primary_theme else 0.6
        for ref in refs:
            existing = collected.get(ref.id)
            if existing:
                existing.relevance = min(1.0, existing.relevance + base_weight * 0.1)
                if theme not in existing.triggered_by_themes:
                    existing.triggered_by_themes.append(theme)
            else:
                collected[ref.id] = FrameworkMatch(
                    framework_id=ref.id,
                    framework_label=ref.label,
                    relevance=min(1.0, base_weight * 0.55),
                    is_mandatory=False,
                    profitability_link=ref.profitability_link,
                    triggered_by_themes=[theme],
                )

    # Regional boost — ontology-driven
    region_key = _region_key(company_country, company_region)
    boosts = query_regional_boosts(region_key)
    queries += 1
    for boost in boosts:
        match = collected.get(boost.framework_id)
        if match:
            match.relevance = min(1.0, match.relevance + boost.boost_value)

    # Mandatory marking — ontology-driven
    mandatory_rules = query_mandatory_rules(region_key)
    queries += 1
    for rule in mandatory_rules:
        if rule.cap_tier == "ALL" or rule.cap_tier == market_cap:
            match = collected.get(rule.framework_id)
            if match:
                match.is_mandatory = True

    # Attach regulatory deadlines for mandatory frameworks
    jurisdiction = {"INDIA": "India", "EU": "EU", "US": "US", "GLOBAL": None}[region_key]
    deadlines = query_compliance_deadlines(jurisdiction)
    queries += 1
    for match in collected.values():
        if not match.is_mandatory:
            continue
        hits = [
            d.label for d in deadlines
            if d.framework == match.framework_label or d.framework.startswith(match.framework_id[:4])
        ]
        match.applicable_deadlines = hits[:3]

    # Phase 14: Populate triggered_sections from ontology
    from engine.ontology.intelligence import query_framework_sections
    primary_theme = tags.primary_theme or ""
    for match in collected.values():
        sections = query_framework_sections(match.framework_id, primary_theme)
        if sections:
            match.triggered_sections = sections
        queries += 1

    matches = sorted(collected.values(), key=lambda m: m.relevance, reverse=True)
    return matches, queries
