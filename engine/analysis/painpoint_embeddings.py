"""Phase 1.3 — Painpoint embedding cache.

Computes + caches embeddings for the painpoints in each tenant's
``painpoints.ttl``. The Criticality scorer's ``painpoint_match``
component (Phase 1.1) does cosine similarity between the article
embedding and the cached painpoint embeddings — this module owns the
painpoint-side of that comparison.

Layout:
    data/ontology/tenants/{slug}/painpoint_embeddings.json
        {
          "model": "text-embedding-3-small",
          "computed_at": "2026-05-08T12:00:00+00:00",
          "embeddings": [
            {"topic_slug": "carbon", "severity": 0.95, "embedding": [...1536 floats...]},
            ...
          ]
        }

Cost: ~$0.00002 per painpoint × 6 painpoints/tenant × 27 tenants ≈ $0.003.
Cheaper than the plan's $0.50 estimate because text-embedding-3-small is
$0.02 per million tokens and a painpoint embeds in ~50 tokens.

Idempotent: skipped if ``painpoint_embeddings.json`` exists for a tenant
and is newer than the painpoints.ttl. Set ``force=True`` to re-embed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


EMBEDDINGS_FILENAME = "painpoint_embeddings.json"
EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class CachedPainpoint:
    """One embedded painpoint loaded from disk."""
    topic_slug: str
    severity: float
    embedding: list[float]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def tenant_embeddings_path(tenant_id: str) -> Path:
    """Path to a tenant's painpoint embeddings JSON file."""
    from engine.ontology.tenant_resolver import ensure_tenant_dir
    return ensure_tenant_dir(tenant_id) / EMBEDDINGS_FILENAME


def needs_refresh(tenant_id: str) -> bool:
    """True iff ``painpoint_embeddings.json`` is missing or older than
    the tenant's ``painpoints.ttl``."""
    from engine.ingestion.painpoint_writer import tenant_painpoints_path
    pp_path = tenant_painpoints_path(tenant_id)
    if not pp_path.exists():
        # No painpoints to embed — caller should still treat as "no refresh needed"
        return False
    emb_path = tenant_embeddings_path(tenant_id)
    if not emb_path.exists():
        return True
    try:
        return emb_path.stat().st_mtime < pp_path.stat().st_mtime
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Read painpoints from TTL (re-uses the W3 writer's output format)
# ---------------------------------------------------------------------------


_TOPIC_RE = re.compile(r"snowkap:weightForTopic\s+snowkap:topic_(\w+)")
_SEVERITY_RE = re.compile(r"snowkap:weightValue\s+([\d.]+)")
_EVIDENCE_RE = re.compile(r'rdfs:comment\s+"([^"]*)"', re.DOTALL)


def parse_painpoints_from_ttl(tenant_id: str) -> list[tuple[str, float, str]]:
    """Read ``painpoints.ttl`` and return list of (topic_slug, severity, evidence)
    tuples — one per painpoint MaterialityWeight block.

    Returns [] when the file is missing or empty.
    """
    from engine.ingestion.painpoint_writer import tenant_painpoints_path
    p = tenant_painpoints_path(tenant_id)
    if not p.exists():
        return []
    try:
        ttl = p.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("painpoint_embeddings: read failed for %s: %s", p, exc)
        return []

    # Split on blank-line gaps between MaterialityWeight blocks. Each block
    # is small (~10 lines) — split + parse is more robust than one giant
    # multi-group regex.
    blocks = re.split(r"\n\s*\n", ttl)
    out: list[tuple[str, float, str]] = []
    for block in blocks:
        if "snowkap:MaterialityWeight" not in block:
            continue
        topic_m = _TOPIC_RE.search(block)
        sev_m = _SEVERITY_RE.search(block)
        ev_m = _EVIDENCE_RE.search(block)
        if not topic_m:
            continue
        topic_slug = topic_m.group(1)
        try:
            severity = float(sev_m.group(1)) if sev_m else 0.5
        except (TypeError, ValueError):
            severity = 0.5
        evidence = ev_m.group(1).strip() if ev_m else ""
        out.append((topic_slug, severity, evidence))
    return out


# ---------------------------------------------------------------------------
# Embedding compute (OpenAI text-embedding-3-small)
# ---------------------------------------------------------------------------


def embed_text(text: str) -> list[float]:
    """Single embedding via text-embedding-3-small. ~$0.00002/call.

    Returns [] on any error so the caller degrades gracefully (the
    Criticality scorer treats empty embedding as 0 painpoint_match).
    """
    if not text or not text.strip():
        return []
    try:
        from openai import APIError, APITimeoutError
        from engine.llm import get_llm_client
    except ImportError as exc:
        logger.warning("painpoint_embeddings: openai import failed: %s", exc)
        return []
    try:
        client = get_llm_client(task_class="embeddings").sync
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text[:8000])
        return list(resp.data[0].embedding)
    except (APIError, APITimeoutError) as exc:
        logger.warning(
            "painpoint_embeddings: API error (%s) — returning empty vector",
            type(exc).__name__,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("painpoint_embeddings: unexpected error: %s", exc)
        return []


def embed_painpoints_for_tenant(
    tenant_id: str, *, force: bool = False,
) -> int:
    """Compute + cache embeddings for all painpoints of one tenant.

    Returns the number of painpoints embedded. 0 means no painpoints.ttl
    or file is fresh + ``force=False``.
    """
    if not force and not needs_refresh(tenant_id):
        return 0

    painpoints = parse_painpoints_from_ttl(tenant_id)
    if not painpoints:
        return 0

    embeddings: list[dict[str, Any]] = []
    for topic_slug, severity, evidence in painpoints:
        # Embedding text: topic + evidence, concatenated. The article
        # title+head will be embedded the same way at score time so the
        # cosine compares like-with-like.
        embed_input = f"{topic_slug.replace('_', ' ')}: {evidence}".strip()
        vec = embed_text(embed_input)
        if not vec:
            continue
        embeddings.append({
            "topic_slug": topic_slug,
            "severity": severity,
            "embedding": vec,
        })

    if not embeddings:
        logger.warning(
            "painpoint_embeddings: no embeddings produced for %s — skipping write",
            tenant_id,
        )
        return 0

    payload = {
        "model": EMBEDDING_MODEL,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tenant_id": tenant_id,
        "embeddings": embeddings,
    }
    out_path = tenant_embeddings_path(tenant_id)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "painpoint_embeddings: wrote %d embeddings to %s",
        len(embeddings), out_path,
    )
    return len(embeddings)


# ---------------------------------------------------------------------------
# Cache loader (used by the Criticality scorer at score time)
# ---------------------------------------------------------------------------


def load_painpoint_embeddings(
    tenant_id: str,
) -> list[tuple[list[float], float]]:
    """Return the cached painpoint embeddings as a list of
    ``(embedding, severity_weight)`` tuples — exactly the shape the
    Criticality scorer's ``painpoint_match`` component expects.

    Returns [] when the cache is missing or unreadable. The scorer
    handles [] cleanly (returns painpoint_match = 0.0).
    """
    p = tenant_embeddings_path(tenant_id)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "painpoint_embeddings: load failed for %s: %s", tenant_id, exc,
        )
        return []
    out: list[tuple[list[float], float]] = []
    for entry in payload.get("embeddings", []) or []:
        emb = entry.get("embedding") or []
        sev = entry.get("severity") or 0.5
        if isinstance(emb, list) and emb:
            out.append((emb, float(sev)))
    return out


def embed_article_for_scoring(
    title: str, head_text: str = "",
) -> list[float]:
    """Embed the article side of the painpoint comparison. Concatenates
    title + first ~200 chars of body so the cosine target matches the
    painpoint embedding format (topic-heavy, short).
    """
    text = (title or "").strip()
    if head_text:
        head = head_text.strip()[:200]
        text = f"{text}. {head}" if text else head
    return embed_text(text)
