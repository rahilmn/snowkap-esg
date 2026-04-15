"""Edge discovery module — discovers new causal primitive edges.

Parses narrative_implied_causation to identify primitive→primitive
relationships not in the ontology.

Auto-promotion: NEVER (causal edges require human review — wrong edges
corrupt cascade computations).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import DiscoveryCandidate

logger = logging.getLogger(__name__)

# Keyword → primitive slug mapping
KEYWORD_TO_PRIMITIVE = {
    "energy price": "EP", "electricity price": "EP", "fuel cost": "EP", "power price": "EP",
    "freight": "FR", "logistics": "FR", "shipping": "FR", "transport cost": "FR",
    "lead time": "LT", "delivery time": "LT", "supply delay": "LT",
    "interest rate": "IR", "credit": "IR", "borrowing cost": "IR", "cost of capital": "IR",
    "currency": "FX", "exchange rate": "FX", "rupee": "FX", "dollar": "FX",
    "regulation": "RG", "regulatory": "RG", "compliance": "CL", "penalty": "CL", "fine": "CL",
    "weather": "XW", "drought": "XW", "flood": "XW", "cyclone": "XW", "heat": "XW",
    "commodity": "CM", "coal": "CM", "oil": "CM", "raw material": "CM",
    "labor": "LC", "wage": "LC", "salary": "LC", "workforce": "WF", "worker": "WF",
    "operating cost": "OX", "opex": "OX", "cost increase": "OX",
    "revenue": "RV", "demand": "RV", "sales": "RV",
    "capex": "CX", "investment": "CX", "expansion": "CX",
    "emission": "GE", "ghg": "GE", "carbon": "GE",
    "energy use": "EU", "electricity consumption": "EU",
    "water": "WA", "waste": "WS",
    "downtime": "DT", "outage": "DT", "shutdown": "DT",
    "supply chain": "SC", "supplier": "SC",
    "cyber": "CY", "data breach": "CY",
    "safety": "HS", "injury": "HS", "accident": "HS",
}


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

    text = causation.lower()
    found_primitives: list[str] = []

    for keyword, prim in KEYWORD_TO_PRIMITIVE.items():
        if keyword in text and prim not in found_primitives:
            found_primitives.append(prim)

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
