"""Theme discovery module — discovers novel ESG themes not in the 21-theme taxonomy.

The theme tagger (Stage 2) can return themes not in the known taxonomy
when the LLM identifies a new ESG topic. This module compares against
the taxonomy and emits candidates for novel themes.

Auto-promotion: NEVER (themes require materiality weights, risk mappings,
framework triggers — all need human review).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import (
    CATEGORY_THEME,
    DiscoveryCandidate,
)

logger = logging.getLogger(__name__)


def discover_themes(
    themes: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Check if primary/secondary themes are novel (not in ontology taxonomy).

    Returns candidates for themes not matching any of the 21 known themes.
    """
    if not themes:
        return []

    candidates: list[DiscoveryCandidate] = []
    known_labels = _get_known_themes()

    # Check primary theme
    primary = themes.primary_theme or ""
    confidence = themes.confidence or 0.0

    if primary and primary.lower() not in known_labels and confidence >= 0.6:
        candidates.append(DiscoveryCandidate(
            category=CATEGORY_THEME,
            label=primary,
            slug=_slugify(primary),
            article_ids=[article_id],
            sources=[source],
            companies=[company_slug],
            confidence=confidence,
            first_seen=now,
            last_seen=now,
            data={
                "pillar": themes.primary_pillar or "mixed",
                "sub_metrics": themes.primary_sub_metrics or [],
            },
        ))

    # Check secondary themes
    for sec in (themes.secondary_themes or []):
        sec_label = sec.get("theme", "")
        if sec_label and sec_label.lower() not in known_labels:
            candidates.append(DiscoveryCandidate(
                category=CATEGORY_THEME,
                label=sec_label,
                slug=_slugify(sec_label),
                article_ids=[article_id],
                sources=[source],
                companies=[company_slug],
                confidence=confidence * 0.8,  # secondary themes get lower confidence
                first_seen=now,
                last_seen=now,
                data={
                    "pillar": sec.get("pillar", "mixed"),
                    "sub_metrics": sec.get("sub_metrics", []),
                },
            ))

    return candidates


def _slugify(text: str) -> str:
    return text.lower().strip().replace(" ", "_").replace("&", "and").replace("/", "_")[:64]


def _get_known_themes() -> set[str]:
    """Load the 21-theme taxonomy labels from the ontology."""
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        rows = g.select_rows("""
            SELECT ?label WHERE {
                { ?topic a snowkap:EnvironmentalTopic . ?topic rdfs:label ?label }
                UNION
                { ?topic a snowkap:SocialTopic . ?topic rdfs:label ?label }
                UNION
                { ?topic a snowkap:GovernanceTopic . ?topic rdfs:label ?label }
            }
        """)
        return {str(row["label"]).lower() for row in rows}
    except Exception:
        return set()
