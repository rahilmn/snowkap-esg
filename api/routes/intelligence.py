"""Repos Integration mount endpoints — exposes intelligence enrichments
(competitors + sentiment forecast) over a tenant slug for the W2 +
W3 UX components to consume.

Read-only. X-API-Key gated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_api_key
from engine.analysis.forecaster import forecast_sentiment_trajectory
from engine.config import get_data_path
from engine.ontology.intelligence import query_competitors

router = APIRouter(
    prefix="/api/intelligence",
    tags=["intelligence"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{slug}/competitors")
def competitors(slug: str) -> dict[str, Any]:
    """Return the competitor labels (and slugified IDs) for a tenant.

    Reads from the existing `competessWith` ontology predicate via
    `engine.ontology.intelligence.query_competitors`.
    """
    try:
        labels = query_competitors(slug)
    except Exception as exc:  # noqa: BLE001
        # Ontology not loaded yet on a fresh checkout → graceful empty
        return {
            "tenant_slug": slug,
            "competitors": [],
            "error": str(exc)[:200],
        }

    def _slug(label: str) -> str:
        import re
        return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")

    return {
        "tenant_slug": slug,
        "competitors": [
            {"slug": _slug(label), "name": label, "shared_risks": []}
            for label in labels
        ],
    }


def _load_recent_insights(slug: str, limit: int = 100) -> list[dict[str, Any]]:
    """Pull recent insight JSONs for one tenant from data/outputs/<slug>/insights/.

    Capped at `limit` most-recent files (by mtime).
    """
    try:
        outputs_dir = get_data_path("outputs") / slug / "insights"
    except Exception:
        return []
    if not outputs_dir.exists():
        return []
    files = sorted(outputs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for p in files[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(data)
    return out


@router.get("/{slug}/forecast")
def forecast(slug: str) -> dict[str, Any]:
    """Return the sentiment-trajectory forecast for a tenant.

    Reads recent insights from disk, calls
    `engine.analysis.forecaster.forecast_sentiment_trajectory`, returns
    the full result (horizons + trajectory + polarity_series).
    """
    insights = _load_recent_insights(slug)
    result = forecast_sentiment_trajectory(
        company_slug=slug,
        insights=insights,
    )
    return result
