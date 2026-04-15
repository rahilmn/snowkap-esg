"""Stakeholder concern discovery — detects new stakeholder concerns.

Parses narrative_stakeholder_framing from NLP output to identify
stakeholder concerns not captured in the ontology's stakeholder model.

Auto-promotion: NEVER (interpretive — requires human review).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import DiscoveryCandidate

logger = logging.getLogger(__name__)


def discover_stakeholder_concerns(
    nlp: Any,
    result: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Detect new stakeholder concern patterns from article framing."""
    if not nlp:
        return []

    framing = nlp.narrative_stakeholder_framing or ""
    if len(framing) < 30:
        return []

    # Look for concern keywords not typically in the ontology
    novel_concern_keywords = [
        "greenwashing", "activist investor", "class action", "shareholder revolt",
        "proxy fight", "divestment campaign", "stranded asset", "just transition",
        "climate litigation", "biodiversity credit", "nature positive",
        "ai ethics", "algorithmic bias", "digital divide",
    ]

    text = framing.lower()
    found_concerns: list[str] = []
    for kw in novel_concern_keywords:
        if kw in text:
            found_concerns.append(kw)

    if not found_concerns:
        return []

    return [DiscoveryCandidate(
        category="stakeholder",
        label=f"Concern: {', '.join(found_concerns[:3])}",
        slug=f"concern_{'_'.join(found_concerns[:2])}",
        article_ids=[article_id],
        sources=[source],
        companies=[company_slug],
        confidence=0.6,
        first_seen=now,
        last_seen=now,
        data={
            "concerns": found_concerns,
            "framing_text": framing[:200],
            "theme": result.themes.primary_theme if hasattr(result, "themes") and result.themes else "",
        },
    )]
