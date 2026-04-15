"""Framework discovery module — discovers new regulatory references.

Triggered when NLP extracts regulatory_references not in the ontology.
Auto-promotes Tier-1 regulatory sources (SEBI, RBI, SEC).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import CATEGORY_FRAMEWORK, DiscoveryCandidate

logger = logging.getLogger(__name__)


def discover_frameworks(
    nlp: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Check regulatory_references against known frameworks."""
    if not nlp:
        return []

    refs = nlp.regulatory_references or []
    if not refs:
        return []

    known = _get_known_frameworks()
    candidates: list[DiscoveryCandidate] = []

    for ref in refs:
        if ref.lower() not in known and len(ref) > 2:
            # Higher confidence for Tier-1 regulators
            is_tier1 = any(r in ref.upper() for r in ["SEBI", "RBI", "SEC", "EU", "CSRD", "ESRS"])
            candidates.append(DiscoveryCandidate(
                category=CATEGORY_FRAMEWORK,
                label=ref,
                slug=_slugify(ref),
                article_ids=[article_id],
                sources=[source],
                companies=[company_slug],
                confidence=0.85 if is_tier1 else 0.6,
                first_seen=now,
                last_seen=now,
                data={"reference": ref, "tier1": is_tier1},
            ))

    return candidates


def _slugify(text: str) -> str:
    return text.lower().strip().replace(" ", "_").replace("&", "and").replace("/", "_")[:64]


def _get_known_frameworks() -> set[str]:
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        known = set()
        for query in [
            "SELECT ?code WHERE { ?s snowkap:sectionCode ?code }",
            "SELECT ?label WHERE { ?fw a snowkap:ESGFramework . ?fw rdfs:label ?label }",
        ]:
            for row in g.select_rows(query):
                known.add(str(list(row.values())[0]).lower())
        return known
    except Exception:
        return set()
