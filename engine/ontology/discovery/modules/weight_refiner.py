"""Weight refinement module — tracks materiality weight divergence.

Compares actual relevance scores from articles against ontology weights.
When divergence > 0.2 across 10+ articles, suggests weight adjustment.

Auto-promotion: NEVER (weight changes affect scoring for all future articles).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import DiscoveryCandidate

logger = logging.getLogger(__name__)


def discover_weight_refinement(
    result: Any,
    article_id: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Track relevance score vs ontology weight for this (topic, industry) pair."""
    if not hasattr(result, "themes") or not result.themes:
        return []
    if not hasattr(result, "relevance") or not result.relevance:
        return []

    topic = result.themes.primary_theme or ""
    if not topic:
        return []

    observed_score = result.relevance.adjusted_total or 0
    ontology_weight = result.relevance.materiality_weight or 0.5

    # Normalize observed to 0-1 range (score is 0-10)
    observed_weight = observed_score / 10.0

    divergence = abs(observed_weight - ontology_weight)
    if divergence < 0.15:  # Not significant enough
        return []

    return [DiscoveryCandidate(
        category="weight",
        label=f"{topic} weight divergence",
        slug=f"weight_{topic.lower().replace(' ', '_')}",
        article_ids=[article_id],
        sources=[],
        companies=[company_slug],
        confidence=0.0,  # N/A for weights — uses article count threshold
        first_seen=now,
        last_seen=now,
        data={
            "topic": topic,
            "ontology_weight": ontology_weight,
            "observed_weight": round(observed_weight, 3),
            "divergence": round(divergence, 3),
        },
    )]
