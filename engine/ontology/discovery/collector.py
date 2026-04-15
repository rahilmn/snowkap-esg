"""Inline discovery collector — runs after each article analysis (~5ms).

Examines the pipeline result and insight output to identify candidates
for ontology enrichment across all 7 discovery categories.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from engine.ontology.discovery.candidates import (
    CATEGORY_ENTITY,
    CATEGORY_EVENT,
    CATEGORY_FRAMEWORK,
    CATEGORY_THEME,
    DiscoveryCandidate,
    get_buffer,
)

logger = logging.getLogger(__name__)


def collect_discoveries(
    result: Any,  # PipelineResult
    insight: Any | None,  # DeepInsight or None
    company_slug: str,
) -> int:
    """Extract discovery candidates from a completed pipeline run.

    Called after write_insight() in on_demand.py. Returns the number
    of candidates collected (for logging).

    This function must be FAST (<10ms) — no LLM calls, no disk I/O
    beyond the buffer persist.
    """
    buf = get_buffer()
    count_before = buf.count
    now = datetime.now(timezone.utc).isoformat()
    article_id = result.article_id if hasattr(result, "article_id") else ""
    source = result.source if hasattr(result, "source") else ""

    nlp = result.nlp if hasattr(result, "nlp") else None
    themes = result.themes if hasattr(result, "themes") else None
    event_obj = result.event if hasattr(result, "event") else None

    # --- Category 1: Entity Discovery ---
    try:
        from engine.ontology.discovery.modules.entity_discoverer import discover_entities
        for c in discover_entities(nlp, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("entity discovery failed: %s", exc)

    # --- Category 2: Theme Discovery ---
    try:
        from engine.ontology.discovery.modules.theme_discoverer import discover_themes
        for c in discover_themes(themes, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("theme discovery failed: %s", exc)

    # --- Category 3: Event Discovery ---
    try:
        from engine.ontology.discovery.modules.event_discoverer import discover_events
        for c in discover_events(event_obj, result, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("event discovery failed: %s", exc)

    # --- Category 4: Causal Edge Discovery ---
    try:
        from engine.ontology.discovery.modules.edge_discoverer import discover_edges
        for c in discover_edges(nlp, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("edge discovery failed: %s", exc)

    # --- Category 5: Materiality Weight Refinement ---
    try:
        from engine.ontology.discovery.modules.weight_refiner import discover_weight_refinement
        for c in discover_weight_refinement(result, article_id, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("weight refinement failed: %s", exc)

    # --- Category 6: Stakeholder Concern Discovery ---
    try:
        from engine.ontology.discovery.modules.stakeholder_discoverer import discover_stakeholder_concerns
        for c in discover_stakeholder_concerns(nlp, result, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("stakeholder discovery failed: %s", exc)

    # --- Category 7: Framework Discovery ---
    try:
        from engine.ontology.discovery.modules.framework_discoverer import discover_frameworks
        for c in discover_frameworks(nlp, article_id, source, company_slug, now):
            buf.add(c)
    except Exception as exc:
        logger.debug("framework discovery failed: %s", exc)

    collected = buf.count - count_before
    if collected > 0:
        logger.info(
            "collect_discoveries: %d new candidates from article %s (%d total in buffer)",
            collected, article_id[:8], buf.count,
        )
    return collected
    """Discover new entities (companies, regulators, facilities) not in ontology."""
    if not hasattr(result, "nlp") or not result.nlp:
        return

    entities = result.nlp.entities or []
    entity_types = result.nlp.entity_types if hasattr(result.nlp, "entity_types") else {}

    # Only consider meaningful entity types
    interesting_types = {"company", "organization", "regulator", "facility", "supplier", "competitor"}

    for entity_name in entities[:15]:  # cap at 15 per article
        if len(entity_name) < 3:
            continue
        etype = str(entity_types.get(entity_name, "")).lower()
        if etype and etype not in interesting_types:
            continue

        slug = _slugify(entity_name)

        # Quick check: skip if it's the company itself
        if slug == company_slug or entity_name.lower() in company_slug.replace("-", " "):
            continue

        # Check if entity already exists in ontology (lazy import to avoid circular)
        try:
            from engine.ontology.graph import get_graph
            g = get_graph()
            exists = g.ask(f"""
                ASK {{ ?x rdfs:label ?label .
                       FILTER(LCASE(STR(?label)) = LCASE("{entity_name}")) }}
            """)
            if exists:
                continue
        except Exception:
            pass  # If check fails, still collect as candidate

        buf.add(DiscoveryCandidate(
            category=CATEGORY_ENTITY,
            label=entity_name,
            slug=slug,
            article_ids=[article_id],
            sources=[source],
            companies=[company_slug],
            confidence=0.7 if etype in interesting_types else 0.5,
            first_seen=now,
            last_seen=now,
            data={"entity_type": etype or "unknown"},
        ))


def _collect_themes(
    result: Any, article_id: str, source: str, company_slug: str, now: str, buf: Any
) -> None:
    """Discover novel ESG themes not in the 21-theme taxonomy."""
    if not hasattr(result, "themes") or not result.themes:
        return

    themes = result.themes
    primary = themes.primary_theme or ""
    confidence = themes.confidence or 0.0

    # Check if primary theme matches known taxonomy
    try:
        from engine.nlp.theme_tagger import _load_topic_taxonomy
        known_labels = {t.get("label", "").lower() for t in _load_topic_taxonomy()}
    except Exception:
        known_labels = set()

    if primary and primary.lower() not in known_labels and confidence >= 0.6:
        buf.add(DiscoveryCandidate(
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


def _collect_events(
    result: Any, article_id: str, source: str, company_slug: str, now: str, buf: Any
) -> None:
    """Discover new event patterns when classifier falls to default/unclassified."""
    if not hasattr(result, "event") or not result.event:
        return

    event = result.event
    event_id = event.event_id if hasattr(event, "event_id") else ""
    label = event.label if hasattr(event, "label") else ""

    # Only collect if event fell to a generic/default classification
    if "default" in event_id.lower() or "unclassified" in label.lower():
        title = result.title if hasattr(result, "title") else ""
        buf.add(DiscoveryCandidate(
            category=CATEGORY_EVENT,
            label=f"Unclassified: {title[:60]}",
            slug=_slugify(title[:40]),
            article_ids=[article_id],
            sources=[source],
            companies=[company_slug],
            confidence=0.5,
            first_seen=now,
            last_seen=now,
            data={
                "title": title,
                "theme": result.themes.primary_theme if hasattr(result, "themes") and result.themes else "",
            },
        ))


def _collect_frameworks(
    result: Any, article_id: str, source: str, company_slug: str, now: str, buf: Any
) -> None:
    """Discover regulatory references not in the ontology."""
    if not hasattr(result, "nlp") or not result.nlp:
        return

    refs = result.nlp.regulatory_references or []
    if not refs:
        return

    # Check which refs are NOT in the ontology
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        known_frameworks = set()
        rows = g.select_rows("SELECT ?code WHERE { ?s snowkap:sectionCode ?code }")
        for row in rows:
            known_frameworks.add(str(row["code"]).lower())
        # Also add framework labels
        rows2 = g.select_rows("SELECT ?label WHERE { ?fw a snowkap:ESGFramework . ?fw rdfs:label ?label }")
        for row in rows2:
            known_frameworks.add(str(row["label"]).lower())
    except Exception:
        known_frameworks = set()

    for ref in refs:
        if ref.lower() not in known_frameworks and len(ref) > 2:
            buf.add(DiscoveryCandidate(
                category=CATEGORY_FRAMEWORK,
                label=ref,
                slug=_slugify(ref),
                article_ids=[article_id],
                sources=[source],
                companies=[company_slug],
                confidence=0.6,
                first_seen=now,
                last_seen=now,
                data={"reference": ref},
            ))
