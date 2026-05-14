"""Batch discovery promoter — runs periodically to promote qualifying candidates.

Reads from the DiscoveryBuffer, applies confidence thresholds and
frequency gating, deduplicates against the live ontology, and promotes
qualifying candidates to ``data/ontology/discovered.ttl``.

Every promotion is logged to ``data/ontology/discovery_audit.jsonl``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rdflib import Literal, Namespace, RDF, RDFS, URIRef

from engine.config import get_data_path
from engine.ontology.discovery.candidates import (
    CATEGORY_ENTITY,
    CATEGORY_EVENT,
    CATEGORY_FRAMEWORK,
    CATEGORY_THEME,
    STATUS_PENDING,
    STATUS_PROMOTED,
    DiscoveryCandidate,
    get_buffer,
)

logger = logging.getLogger(__name__)

SNOWKAP = Namespace("http://snowkap.com/ontology/esg#")

# ---------------------------------------------------------------------------
# Confidence thresholds (from plan)
# ---------------------------------------------------------------------------

THRESHOLDS: dict[str, dict[str, Any]] = {
    CATEGORY_ENTITY: {"min_confidence": 0.80, "min_articles": 3, "min_sources": 2, "auto_promote": True},
    CATEGORY_THEME: {"min_confidence": 0.70, "min_articles": 5, "min_sources": 2, "auto_promote": False},
    CATEGORY_EVENT: {"min_confidence": 0.75, "min_articles": 3, "min_sources": 2, "auto_promote": True},
    CATEGORY_FRAMEWORK: {"min_confidence": 0.70, "min_articles": 1, "min_sources": 1, "auto_promote": True},
    # Categories 4-6: always pending (never auto-promote)
    "edge": {"min_confidence": 0.80, "min_articles": 5, "min_sources": 3, "auto_promote": False},
    "weight": {"min_confidence": 0.0, "min_articles": 10, "min_sources": 1, "auto_promote": False},
    "stakeholder": {"min_confidence": 0.70, "min_articles": 5, "min_sources": 2, "auto_promote": False},
}

MAX_DISCOVERED_TRIPLES = 10_000
DISCOVERED_TTL_PATH = Path("data/ontology/discovered.ttl")
AUDIT_LOG_PATH = Path("data/ontology/discovery_audit.jsonl")


# ---------------------------------------------------------------------------
# Triple builders (one per category)
# ---------------------------------------------------------------------------


def _build_entity_triples(c: DiscoveryCandidate) -> list[tuple]:
    """Build RDF triples for a discovered entity."""
    uri = SNOWKAP[f"disc_{c.slug}"]
    entity_type = c.data.get("entity_type", "unknown")
    rdf_type = {
        "company": SNOWKAP.Company,
        "competitor": SNOWKAP.Competitor,
        "regulator": SNOWKAP.Regulation,
        "facility": SNOWKAP.Facility,
        "supplier": SNOWKAP.Supplier,
    }.get(entity_type, SNOWKAP.Company)

    return [
        (uri, RDF.type, rdf_type),
        (uri, RDF.type, SNOWKAP.DiscoveredTriple),
        (uri, RDFS.label, Literal(c.label)),
        (uri, SNOWKAP.slug, Literal(c.slug)),
        (uri, SNOWKAP.discoveredFrom, Literal(c.article_ids[0] if c.article_ids else "")),
        (uri, SNOWKAP.discoveredAt, Literal(c.last_seen)),
        (uri, SNOWKAP.discoveryConfidence, Literal(c.confidence)),
        (uri, SNOWKAP.discoveryCategory, Literal(c.category)),
        (uri, SNOWKAP.discoveryStatus, Literal(STATUS_PROMOTED)),
    ]


def _build_framework_triples(c: DiscoveryCandidate) -> list[tuple]:
    """Build RDF triples for a discovered framework/regulation."""
    uri = SNOWKAP[f"disc_fw_{c.slug}"]
    return [
        (uri, RDF.type, SNOWKAP.ComplianceDeadline),
        (uri, RDF.type, SNOWKAP.DiscoveredTriple),
        (uri, RDFS.label, Literal(c.label)),
        (uri, SNOWKAP.slug, Literal(c.slug)),
        (uri, SNOWKAP.discoveredFrom, Literal(c.article_ids[0] if c.article_ids else "")),
        (uri, SNOWKAP.discoveredAt, Literal(c.last_seen)),
        (uri, SNOWKAP.discoveryConfidence, Literal(c.confidence)),
        (uri, SNOWKAP.discoveryCategory, Literal(c.category)),
        (uri, SNOWKAP.discoveryStatus, Literal(STATUS_PROMOTED)),
    ]


def _build_triples(c: DiscoveryCandidate) -> list[tuple]:
    """Route to the appropriate triple builder."""
    if c.category == CATEGORY_ENTITY:
        return _build_entity_triples(c)
    if c.category == CATEGORY_FRAMEWORK:
        return _build_framework_triples(c)
    # Other categories (theme, event, edge, weight, stakeholder) are
    # always pending and get promoted via admin API with manual triple construction
    return []


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _entity_exists_fuzzy(label: str) -> bool:
    """Check if an entity with a similar name exists (Jaro-Winkler ≥ 0.90)."""
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        # First: exact match
        exact = g.ask(f"""
            ASK {{ ?x rdfs:label ?lbl .
                   FILTER(LCASE(STR(?lbl)) = LCASE("{label.replace('"', '')}")) }}
        """)
        if exact:
            return True

        # Second: fetch all labels and do Jaro-Winkler
        rows = g.select_rows("""
            SELECT ?lbl WHERE {
                { ?x a snowkap:Company . ?x rdfs:label ?lbl }
                UNION
                { ?x a snowkap:Competitor . ?x rdfs:label ?lbl }
            }
        """)
        target = label.lower()
        for row in rows:
            existing = str(row["lbl"]).lower()
            if _jaro_winkler(target, existing) >= 0.90:
                return True
    except Exception:
        pass
    return False


def _jaro_winkler(s1: str, s2: str) -> float:
    """Compute Jaro-Winkler similarity (0.0-1.0)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_dist = max(len1, len2) // 2 - 1
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_dist)
        end = min(i + match_dist + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3

    # Winkler adjustment
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    return jaro + prefix * 0.1 * (1 - jaro)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def _log_audit(action: str, candidate: DiscoveryCandidate, triples_count: int = 0) -> None:
    """Append an audit entry to discovery_audit.jsonl."""
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "category": candidate.category,
            "label": candidate.label,
            "slug": candidate.slug,
            "confidence": candidate.confidence,
            "article_count": candidate.article_count,
            "source_count": candidate.source_count,
            "article_ids": candidate.article_ids[:5],
            "sources": candidate.sources[:5],
            "triples_added": triples_count,
            "status": candidate.status,
        }
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Count discovered triples
# ---------------------------------------------------------------------------


def _count_discovered_triples() -> int:
    """Count current triples in discovered.ttl."""
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        rows = g.select_rows(
            "SELECT (COUNT(?x) AS ?cnt) WHERE { ?x a snowkap:DiscoveredTriple }"
        )
        return int(rows[0]["cnt"]) if rows else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Main batch promoter
# ---------------------------------------------------------------------------


def batch_promote() -> dict[str, int]:
    """Promote qualifying candidates from buffer to ontology.

    Returns dict with counts: {promoted: N, skipped: N, pending: N}.
    """
    buf = get_buffer()
    pending = buf.get_all(status=STATUS_PENDING)

    if not pending:
        return {"promoted": 0, "skipped": 0, "pending": 0}

    current_discovered = _count_discovered_triples()
    promoted = 0
    skipped = 0
    still_pending = 0

    for candidate in pending:
        threshold = THRESHOLDS.get(candidate.category, {})
        min_conf = threshold.get("min_confidence", 1.0)
        min_articles = threshold.get("min_articles", 999)
        min_sources = threshold.get("min_sources", 999)
        auto = threshold.get("auto_promote", False)

        # Check if meets promotion criteria
        meets_criteria = (
            candidate.confidence >= min_conf
            and candidate.article_count >= min_articles
            and candidate.source_count >= min_sources
        )

        if not meets_criteria:
            still_pending += 1
            continue

        if not auto:
            # Needs human review — keep as pending but log
            still_pending += 1
            continue

        # Check triple budget
        if current_discovered >= MAX_DISCOVERED_TRIPLES:
            logger.warning("batch_promote: triple cap reached (%d), skipping", current_discovered)
            skipped += 1
            continue

        # Dedup check
        if candidate.category == CATEGORY_ENTITY:
            if _entity_exists_fuzzy(candidate.label):
                buf.update_status(candidate.category, candidate.slug, "duplicate")
                _log_audit("duplicate_skipped", candidate)
                skipped += 1
                continue

        # Build triples
        triples = _build_triples(candidate)
        if not triples:
            still_pending += 1
            continue

        # Insert into graph
        try:
            from engine.ontology.graph import get_graph
            g = get_graph()
            g.insert_triples(triples)
            current_discovered += 1

            # Update status
            buf.update_status(candidate.category, candidate.slug, STATUS_PROMOTED)
            _log_audit("promoted", candidate, len(triples))
            promoted += 1

            logger.info(
                "batch_promote: promoted %s '%s' (%d triples, conf=%.2f, %d articles)",
                candidate.category, candidate.label, len(triples),
                candidate.confidence, candidate.article_count,
            )
        except Exception as exc:
            logger.error("batch_promote: insertion failed for '%s': %s", candidate.label, exc)
            skipped += 1

    # Persist discovered triples to disk
    if promoted > 0:
        try:
            _persist_discovered()
        except Exception as exc:
            logger.error("batch_promote: persist failed: %s", exc)

    result = {"promoted": promoted, "skipped": skipped, "pending": still_pending}
    logger.info("batch_promote: %s", result)
    return result


def _persist_discovered() -> None:
    """Serialize all DiscoveredTriple instances to discovered.ttl."""
    try:
        from engine.ontology.graph import get_graph
        from rdflib import Graph

        g = get_graph()
        # Extract only discovered triples
        disc_graph = Graph()
        disc_graph.bind("snowkap", SNOWKAP)
        disc_graph.bind("rdf", RDF)
        disc_graph.bind("rdfs", RDFS)

        for s, p, o in g.graph.triples((None, RDF.type, SNOWKAP.DiscoveredTriple)):
            # Get all triples about this subject
            for s2, p2, o2 in g.graph.triples((s, None, None)):
                disc_graph.add((s2, p2, o2))

        DISCOVERED_TTL_PATH.parent.mkdir(parents=True, exist_ok=True)
        disc_graph.serialize(destination=str(DISCOVERED_TTL_PATH), format="turtle")
        logger.info("Persisted %d discovered triples to %s", len(disc_graph), DISCOVERED_TTL_PATH)
    except Exception as exc:
        logger.error("_persist_discovered failed: %s", exc)


# ---------------------------------------------------------------------------
# Phase 24 (W2) — manual decide helper
# ---------------------------------------------------------------------------


# Status emitted when the analyst defers a candidate (re-review next cycle).
STATUS_DEFERRED = "deferred"


class DecideResult:
    """Lightweight result wrapper for ``manual_decide`` returns.

    Keeping this as a class (not a dataclass) avoids dataclass overhead in
    the API path and matches the existing dict-returning convention used
    by the legacy approve/reject endpoints.
    """

    def __init__(
        self,
        ok: bool,
        message: str,
        category: str | None = None,
        slug: str | None = None,
        decision: str | None = None,
        triples_added: int = 0,
        new_status: str | None = None,
    ) -> None:
        self.ok = ok
        self.message = message
        self.category = category
        self.slug = slug
        self.decision = decision
        self.triples_added = triples_added
        self.new_status = new_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "category": self.category,
            "slug": self.slug,
            "decision": self.decision,
            "triples_added": self.triples_added,
            "new_status": self.new_status,
        }


def manual_decide(
    candidate_id: str,
    decision: str,
    *,
    toulmin: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> DecideResult:
    """Apply a manual decision to a discovery candidate.

    ``decision`` ∈ {``promote``, ``reject``, ``defer``}.

    ``promote`` → builds triples (if the category supports it), inserts
    into the live graph, persists discovered.ttl, marks STATUS_PROMOTED.

    ``reject`` → marks STATUS_REJECTED. Triple count unchanged.

    ``defer`` → marks STATUS_DEFERRED. Buffer keeps the candidate visible
    so the next admin review session can revisit it. Defer is NOT pending —
    pending = "engine hasn't decided yet"; defer = "human looked, decided
    not to decide right now".

    Every decision writes BOTH:
      1. The legacy ``data/ontology/discovery_audit.jsonl`` (back-compat
         with Phase 19 + downstream tooling that reads it)
      2. The new Phase 24 ``data/audit/promotion_log.jsonl`` via
         ``engine.audit.append_promotion`` — this carries the required
         Toulmin block and is the audit surface the admin UI reads from.

    The Toulmin block is required for ``reject`` and ``defer`` (those
    decisions need a stated reason). It is optional for ``promote``
    (the candidate's existing confidence + article count is the warrant).
    """
    if decision not in {"promote", "reject", "defer"}:
        return DecideResult(
            ok=False, message=f"unknown decision '{decision}' "
            "(expected promote | reject | defer)",
        )

    parts = candidate_id.split(":", 1)
    if len(parts) != 2:
        return DecideResult(
            ok=False, message="invalid candidate_id (expected 'category:slug')",
        )
    category, slug = parts

    from engine.ontology.discovery.candidates import (
        STATUS_PENDING,
        STATUS_REJECTED,
        get_buffer,
    )
    buf = get_buffer()
    candidate = buf.get(category, slug)
    if candidate is None:
        return DecideResult(
            ok=False, message=f"candidate not found: {candidate_id}",
            category=category, slug=slug,
        )

    # Reject + defer require Toulmin justification (W2 discipline)
    if decision in {"reject", "defer"} and not toulmin:
        return DecideResult(
            ok=False,
            message=f"{decision} requires a toulmin justification "
            "({claim, grounds[], warrant, ...})",
            category=category, slug=slug,
        )

    triples_count = 0
    new_status: str

    if decision == "promote":
        # Build + insert triples
        triples = _build_triples(candidate)
        if triples:
            try:
                from engine.ontology.graph import get_graph
                g = get_graph()
                g.insert_triples(triples)
                _persist_discovered()
                triples_count = len(triples)
            except Exception as exc:
                logger.error("manual_decide promote insert failed for %s: %s",
                             candidate_id, exc)
                return DecideResult(
                    ok=False, message=f"triple insert failed: {exc}",
                    category=category, slug=slug, decision=decision,
                )
        new_status = STATUS_PROMOTED
    elif decision == "reject":
        new_status = STATUS_REJECTED
    else:  # defer
        new_status = STATUS_DEFERRED

    # Update buffer status
    buf.update_status(category, slug, new_status)

    # Legacy audit log (Phase 19 back-compat)
    _log_audit(f"manual_{decision}", candidate, triples_count)

    # Phase 24 promotion log — carries Toulmin + user_id for audit
    try:
        from engine import audit as _audit
        _audit.append_promotion(
            decision,  # type: ignore[arg-type]
            candidate_id=candidate_id,
            category=category,
            candidate_payload={
                "label": candidate.label,
                "slug": candidate.slug,
                "article_count": candidate.article_count,
                "source_count": candidate.source_count,
                "data": candidate.data,
            },
            confidence=candidate.confidence,
            toulmin=toulmin,  # type: ignore[arg-type]
            user_id=user_id,
            automated=False,
            extra={"triples_added": triples_count},
        )
    except Exception as exc:  # noqa: BLE001 — never block on audit failure
        logger.warning("manual_decide audit append failed (non-fatal): %s", exc)

    logger.info(
        "manual_decide: %s '%s' (%s) → %s, %d triples, user=%s",
        decision, candidate.label, candidate_id, new_status,
        triples_count, user_id or "unknown",
    )
    return DecideResult(
        ok=True,
        message=f"{decision} applied to {candidate.label}",
        category=category, slug=slug, decision=decision,
        triples_added=triples_count, new_status=new_status,
    )
