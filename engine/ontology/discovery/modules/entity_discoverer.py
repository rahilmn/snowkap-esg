"""Entity discovery module — discovers new companies, regulators, facilities.

Entities are extracted by the NLP stage (Stage 1). This module checks
each entity against the ontology and emits candidates for new ones.

Auto-promotion: entities with 3+ article mentions from 2+ sources.
Dedup: Jaro-Winkler ≥ 0.90 against existing entity labels.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import (
    CATEGORY_ENTITY,
    DiscoveryCandidate,
)

logger = logging.getLogger(__name__)

# Entity types worth tracking
INTERESTING_TYPES = {"company", "organization", "regulator", "facility", "supplier", "competitor"}


def discover_entities(
    nlp: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Extract entity candidates from NLP extraction output.

    Returns a list of DiscoveryCandidate objects for entities not
    already in the ontology.
    """
    if not nlp:
        return []

    entities = nlp.entities or []
    entity_types = nlp.entity_types if hasattr(nlp, "entity_types") else {}
    candidates: list[DiscoveryCandidate] = []

    for entity_name in entities[:15]:
        if len(entity_name) < 3:
            continue

        etype = str(entity_types.get(entity_name, "")).lower()
        if etype and etype not in INTERESTING_TYPES:
            continue

        slug = _slugify(entity_name)

        # Skip if it's the company being analyzed
        if slug == company_slug or entity_name.lower() in company_slug.replace("-", " "):
            continue

        # Skip common non-entity words that NLP sometimes extracts
        skip_words = {"india", "government", "ministry", "court", "supreme court", "high court"}
        if entity_name.lower() in skip_words:
            continue

        # Check ontology existence
        if _entity_exists(entity_name):
            continue

        candidates.append(DiscoveryCandidate(
            category=CATEGORY_ENTITY,
            label=entity_name,
            slug=slug,
            article_ids=[article_id],
            sources=[source],
            companies=[company_slug],
            confidence=0.75 if etype in INTERESTING_TYPES else 0.55,
            first_seen=now,
            last_seen=now,
            data={"entity_type": etype or "unknown"},
        ))

    return candidates


def _slugify(text: str) -> str:
    return text.lower().strip().replace(" ", "_").replace("&", "and").replace("/", "_")[:64]


def _entity_exists(label: str) -> bool:
    """Check if entity already exists in ontology (exact match)."""
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        return g.ask(f"""
            ASK {{ ?x rdfs:label ?lbl .
                   FILTER(LCASE(STR(?lbl)) = LCASE("{label.replace('"', '')}")) }}
        """)
    except Exception:
        return False
