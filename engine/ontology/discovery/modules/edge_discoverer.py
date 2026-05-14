"""Edge discovery module — discovers new causal primitive edges.

Parses narrative_implied_causation to identify primitive→primitive
relationships not in the ontology.

Auto-promotion: NEVER (causal edges require human review — wrong edges
corrupt cascade computations).

L1 #9 (2026-05-13) — keyword→primitive mapping migrated from a hardcoded
Python KEYWORD_TO_PRIMITIVE dict (24 entries) to TTL via
``snw:keywordTrigger`` triples in ``data/ontology/primitives_keywords.ttl``.
The mapping is now loaded into the live ontology graph at startup and
queried via parameterised SPARQL — adding a new keyword is a TTL edit, not
a Python change. Per Snowkap's "every dict lookup is a smell" rule and the
v2 plan's L1/#9 deliverable.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from engine.ontology.discovery.candidates import DiscoveryCandidate

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_keyword_to_primitive() -> tuple[tuple[str, str], ...]:
    """Load (keyword, primitive_slug) pairs from the live ontology graph.

    Cached at module load (LRU size=1). Each entry is a tuple
    ``(keyword_lowercase, primitive_slug_uppercase)``. Returned as a
    tuple-of-tuples so it's hashable and immutable.

    Returns an empty tuple if the ontology is unavailable (defensive —
    discovery should NEVER crash a pipeline run).
    """
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        g.ensure_loaded()
        rows = g.select_rows("""
            SELECT ?prim_uri ?keyword WHERE {
                ?prim_uri snowkap:keywordTrigger ?keyword .
            }
        """)
    except Exception as exc:  # noqa: BLE001 — never let discovery kill a run
        logger.debug("keyword trigger SPARQL failed: %s", exc)
        return ()

    pairs: list[tuple[str, str]] = []
    for row in rows:
        prim_uri = row.get("prim_uri", "")
        keyword = row.get("keyword", "")
        if not keyword or not prim_uri:
            continue
        # Extract slug from URI (e.g. http://...#prim_EP → "EP")
        if "prim_" in prim_uri:
            slug = prim_uri.rsplit("prim_", 1)[-1]
        else:
            slug = prim_uri.rsplit("#", 1)[-1]
        pairs.append((keyword.lower().strip(), slug.upper().strip()))
    return tuple(pairs)


def _detect_primitives_in_text(text: str) -> list[str]:
    """Return the ordered list of distinct primitives whose keyword triggers
    appear in ``text``. Order matches the order of first occurrence in the
    keyword catalog so behavior is deterministic across runs."""
    if not text:
        return []
    text_lower = text.lower()
    found: list[str] = []
    for keyword, prim in _load_keyword_to_primitive():
        if keyword in text_lower and prim not in found:
            found.append(prim)
    return found


def discover_edges(
    nlp: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Parse causal statements to find potential new primitive edges."""
    if not nlp:
        return []

    causation = nlp.narrative_implied_causation or ""
    if len(causation) < 20:
        return []

    found_primitives = _detect_primitives_in_text(causation)

    # Need at least 2 primitives to form an edge
    if len(found_primitives) < 2:
        return []

    # Check if this edge already exists
    source_prim = found_primitives[0]
    target_prim = found_primitives[1]

    try:
        from engine.ontology.intelligence import query_p2p_edges
        existing = query_p2p_edges(source_prim)
        existing_targets = {e.target_slug.upper() for e in existing}
        if target_prim.upper() in existing_targets:
            return []  # Edge already exists
    except Exception:
        pass

    return [DiscoveryCandidate(
        category="edge",
        label=f"{source_prim}→{target_prim}",
        slug=f"{source_prim.lower()}_{target_prim.lower()}",
        article_ids=[article_id],
        sources=[source],
        companies=[company_slug],
        confidence=0.5,
        first_seen=now,
        last_seen=now,
        data={
            "source_primitive": source_prim,
            "target_primitive": target_prim,
            "causation_text": causation[:200],
        },
    )]
