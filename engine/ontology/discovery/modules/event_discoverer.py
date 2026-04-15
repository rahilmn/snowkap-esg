"""Event discovery module — discovers new event types from unclassified articles.

Triggered when the event classifier falls to 'Unclassified' or theme fallback.
Clusters similar unclassified events and suggests new event types.

Auto-promotion: Conditional (3+ articles from 2+ sources with similar pattern).
"""

from __future__ import annotations

import logging
from typing import Any

from engine.ontology.discovery.candidates import CATEGORY_EVENT, DiscoveryCandidate

logger = logging.getLogger(__name__)


def discover_events(
    event: Any,
    result: Any,
    article_id: str,
    source: str,
    company_slug: str,
    now: str,
) -> list[DiscoveryCandidate]:
    """Emit candidate when event classifier falls to default/unclassified."""
    if not event:
        return []

    event_id = event.event_id if hasattr(event, "event_id") else ""
    label = event.label if hasattr(event, "label") else ""

    # Only collect if event fell to generic classification
    if "default" not in event_id.lower() and "unclassified" not in label.lower():
        return []

    title = result.title if hasattr(result, "title") else ""
    theme = result.themes.primary_theme if hasattr(result, "themes") and result.themes else ""

    return [DiscoveryCandidate(
        category=CATEGORY_EVENT,
        label=f"Unclassified: {title[:60]}",
        slug=_slugify(title[:40]),
        article_ids=[article_id],
        sources=[source],
        companies=[company_slug],
        confidence=0.5,
        first_seen=now,
        last_seen=now,
        data={"title": title, "theme": theme},
    )]


def _slugify(text: str) -> str:
    return text.lower().strip().replace(" ", "_").replace("&", "and").replace("/", "_")[:64]
