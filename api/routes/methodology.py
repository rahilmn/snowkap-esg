"""Phase 28 / Feature 2 — Methodology API.

``GET /api/insights/{article_id}/methodology[?role=cfo]`` returns:

* ``role_explainer`` — per-role 3-part block (``why_important_for_me``,
  ``how_it_impacts_business``, ``analysis_result``, ``simple_logic``).
* ``methodology`` — per-metric provenance (criticality, relevance,
  persona_boost, sentiment_trajectory, framework_match), each with
  ``source``, ``simple_logic``, ``formula_human``, ``ontology_anchors``,
  ``your_inputs``.

Drives the ``MethodologyDrawer.tsx`` side-drawer triggered by the "i"
icon on each panel header. Read-only, no auth gate beyond the API-key
middleware that every ``/api/insights/...`` route already enforces.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import require_api_key
from engine.analysis.methodology_provenance import (
    METRIC_DISPATCH,
    build_methodology,
    build_panel_methodology,
)
from engine.analysis.role_explainer import (
    build_criticality_summary,
    build_role_explainer,
)
from engine.config import get_data_path
from engine.index.sqlite_index import get_by_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["methodology"], dependencies=[Depends(require_api_key)])


def _resolve_json_path(relative_path: str) -> Path:
    p = Path(relative_path)
    if p.is_absolute():
        return p
    project_root = get_data_path().parent
    return project_root / relative_path


@router.get("/api/insights/{article_id}/methodology")
def methodology_for_article(
    article_id: str,
    role: str | None = Query(
        None, regex="^(cfo|ceo|esg-analyst)$",
        description="Role lens for criticality weights + role-explainer.",
    ),
    panel: str | None = Query(
        None,
        description=(
            "Phase 29 — when supplied, return ONLY this panel's methodology "
            "block (plus the role explainer). Drives the per-panel info "
            "popover. Without `panel`, all metrics + panels are returned "
            "for back-compat with the legacy MethodologyDrawer."
        ),
    ),
) -> dict:
    """Return source + logic + role-specific analysis for one insight.

    Falls back gracefully when the article exists in the index but the
    on-disk JSON is missing (returns 404 instead of 500). Designed to
    be cheap on every drawer-open — pure-Python computation, no LLM.

    Phase 29 — supports per-panel filtering via ``?panel=`` so the
    per-panel info popover can fetch just the one block it needs.
    """
    row = get_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Insight {article_id} not found")

    # Phase 29 — validate panel id BEFORE doing any work (cheap 422).
    if panel is not None and panel not in METRIC_DISPATCH:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown panel {panel!r}. "
                f"Valid panels: {sorted(METRIC_DISPATCH.keys())}"
            ),
        )

    json_path = _resolve_json_path(row["json_path"])
    if not json_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Insight JSON missing on disk for {article_id}. "
            "Click the article in the feed to trigger on-demand re-enrichment.",
        )

    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("methodology: cannot read %s: %s", json_path, exc)
        raise HTTPException(status_code=500, detail="Insight payload unreadable.")

    insight = payload.get("insight") or payload
    # Pipeline state sits one level up alongside the insight block in the
    # writer.py output shape; methodology_provenance reads from both.
    # We merge them so the same dict can be passed to both builders.
    merged: dict = {**(payload.get("pipeline") or {}), **(insight or {})}
    if "pipeline" in payload:
        merged["pipeline"] = payload["pipeline"]
    if "criticality" not in merged and "criticality" in insight:
        merged["criticality"] = insight["criticality"]

    # Phase 29 — single-panel path. Smaller payload + faster paint.
    if panel is not None:
        single = build_panel_methodology(merged, panel, role=role)
        if single is None:
            raise HTTPException(
                status_code=500, detail=f"Methodology builder failed for {panel}.",
            )
        return {
            "article_id": article_id,
            "company_slug": row.get("company_slug"),
            "role": role,
            "panel": panel,
            "methodology": {panel: single},
            "role_explainer": (
                # Only the active role's block when one was requested
                {role: build_role_explainer(merged).get(role, {})}
                if role else build_role_explainer(merged)
            ),
            "criticality_summary": build_criticality_summary(merged),
            "headline": (insight.get("headline") or row.get("title") or "")[:200],
            "criticality_band": (insight.get("criticality") or {}).get("band"),
            "schema_version": (payload.get("meta") or {}).get("schema_version"),
        }

    methodology = build_methodology(merged, role=role)
    role_explainer = build_role_explainer(merged)

    return {
        "article_id": article_id,
        "company_slug": row.get("company_slug"),
        "role": role,
        "methodology": methodology,
        "role_explainer": role_explainer,
        "criticality_summary": build_criticality_summary(merged),
        "headline": (insight.get("headline") or row.get("title") or "")[:200],
        "criticality_band": (insight.get("criticality") or {}).get("band"),
        "schema_version": (payload.get("meta") or {}).get("schema_version"),
    }
