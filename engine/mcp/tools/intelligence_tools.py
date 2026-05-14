"""MCP adapters for intelligence-competitors / intelligence-forecast.

Both are read-only. `intelligence-forecast` runs the deterministic
polarity series first; if `OPENAI_DISABLED=1` is set or no insights
are on disk, the LLM call is bypassed and a neutral horizon is
returned (matches `forecaster.py` graceful-no-op).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def handle_intelligence_competitors(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    from engine.ontology.intelligence import query_competitors

    tenant = payload["tenant"]
    try:
        rows = query_competitors(tenant)
    except Exception as exc:  # noqa: BLE001 — ontology graph may not be loaded in tests
        return {"tenant": tenant, "competitors": [], "error": f"{type(exc).__name__}: {exc}"}
    return {"tenant": tenant, "competitors": rows}


def _load_recent_insights(base_data: Path, slug: str, max_files: int = 60) -> list[dict[str, Any]]:
    insights_dir = base_data / "outputs" / slug / "insights"
    if not insights_dir.exists():
        return []
    files = sorted(insights_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for f in files[:max_files]:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def handle_intelligence_forecast(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    from engine.analysis.forecaster import forecast_sentiment_trajectory

    tenant = payload["tenant"]
    insights = _load_recent_insights(base_data, tenant)
    # Honour OPENAI_DISABLED=1 by passing a stub client; falls through
    # to deterministic horizons.
    stub_client = None
    if os.environ.get("OPENAI_DISABLED") == "1":
        class _DisabledClient:  # noqa: D401
            class chat:  # noqa: N801
                class completions:  # noqa: N801
                    @staticmethod
                    def create(**_kwargs):  # noqa: ANN003
                        raise RuntimeError("OPENAI_DISABLED=1")
        stub_client = _DisabledClient()
    return forecast_sentiment_trajectory(
        company_slug=tenant,
        insights=insights,
        client=stub_client,
    )
