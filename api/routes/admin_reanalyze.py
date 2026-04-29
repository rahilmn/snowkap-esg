"""Phase 18 — Bulk schema-version reanalyze endpoint.

Lets a sales / super-admin user invalidate the cached deep_insight on
every article for a given company so the next user click triggers fresh
enrichment.

Background: when the engine ships a new schema version (Phase 17, Phase 18,
...), articles already on disk keep their old `meta.schema_version`. The
on-demand pipeline (`engine/analysis/on_demand.enrich_on_demand`) treats
old-schema articles as cached and returns them as-is. Without this admin
hook, the only way to refresh every article is to delete the JSON files
manually or wait for the user to click each one.

This endpoint walks every article JSON in `data/outputs/<slug>/insights/`
and bumps `meta.schema_version` to a placeholder value (`"_invalidated"`)
that is guaranteed to mismatch CURRENT_SCHEMA_VERSION. Next user click →
on-demand re-runs stages 10-12 → fresh insight.

Routes:
    POST /api/admin/companies/{slug}/reanalyze →
        {"status": "ok", "invalidated": <int>, "skipped": <int>}

Gated by `manage_drip_campaigns` (super-admin only).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.config import get_data_path, load_companies

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-reanalyze"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


_INVALIDATION_MARKER = "_invalidated"


def _invalidate_company_insights(slug: str) -> dict[str, int]:
    """Walk insights/*.json and bump every meta.schema_version. Returns a
    counts dict for the response."""
    insights_dir = get_data_path("outputs", slug, "insights")
    if not insights_dir.exists():
        return {"invalidated": 0, "skipped": 0, "errors": 0}

    counts = {"invalidated": 0, "skipped": 0, "errors": 0}
    for json_path in insights_dir.glob("*.json"):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("reanalyze: failed to read %s: %s", json_path, exc)
            counts["errors"] += 1
            continue

        meta = payload.get("meta") or {}
        # Skip files already marked — idempotent on repeat calls
        if meta.get("schema_version") == _INVALIDATION_MARKER:
            counts["skipped"] += 1
            continue

        # Preserve the prior version under a side key so an operator can
        # diff or roll back without losing provenance.
        if meta.get("schema_version"):
            meta["_pre_invalidation_schema_version"] = meta["schema_version"]
        meta["schema_version"] = _INVALIDATION_MARKER
        payload["meta"] = meta

        try:
            json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            counts["invalidated"] += 1
        except OSError as exc:
            logger.warning("reanalyze: failed to write %s: %s", json_path, exc)
            counts["errors"] += 1

    return counts


@router.post("/companies/{slug}/reanalyze")
def reanalyze_company(slug: str) -> dict[str, Any]:
    """Bump schema_version on every article for `slug` so the next user
    click re-runs stages 10-12 with the latest engine version.

    Returns counts: {"invalidated", "skipped", "errors"}. Idempotent —
    safe to call repeatedly.
    """
    # Resolve via load_companies so we accept both target-company slugs
    # AND onboarded-company slugs (the registry surface is the same).
    known_slugs = {c.slug for c in load_companies()}
    if slug not in known_slugs:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown company slug '{slug}'. Onboard via /api/admin/onboard first.",
        )

    counts = _invalidate_company_insights(slug)
    logger.info(
        "reanalyze[%s]: invalidated=%d skipped=%d errors=%d",
        slug, counts["invalidated"], counts["skipped"], counts["errors"],
    )
    return {"status": "ok", "company_slug": slug, **counts}


@router.post("/articles/{article_id}/reanalyze")
def reanalyze_article(article_id: str) -> dict[str, Any]:
    """Single-article version — search every company folder for the article,
    invalidate just that file. Useful for "this article looks wrong, re-run
    it" UX.

    Returns {"status": "ok", "company_slug": <slug>, "invalidated": 1} or
    a 404 if the article isn't found anywhere on disk.
    """
    outputs_root = get_data_path("outputs")
    if not outputs_root.exists():
        raise HTTPException(status_code=500, detail="outputs directory missing")

    target: Path | None = None
    target_slug: str | None = None
    for company_dir in outputs_root.iterdir():
        if not company_dir.is_dir():
            continue
        insights_dir = company_dir / "insights"
        if not insights_dir.exists():
            continue
        matches = list(insights_dir.glob(f"*{article_id}*"))
        if matches:
            target = matches[0]
            target_slug = company_dir.name
            break

    if target is None or target_slug is None:
        raise HTTPException(
            status_code=404, detail=f"No insight JSON found for article '{article_id}'"
        )

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read insight: {exc}")

    meta = payload.get("meta") or {}
    if meta.get("schema_version"):
        meta["_pre_invalidation_schema_version"] = meta["schema_version"]
    meta["schema_version"] = _INVALIDATION_MARKER
    payload["meta"] = meta

    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "company_slug": target_slug,
        "article_id": article_id,
        "invalidated": 1,
    }
