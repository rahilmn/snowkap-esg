"""Output writer — persists insight JSON + perspective views to disk.

Folder layout (per company slug)::

    data/outputs/{slug}/insights/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/perspectives/cfo/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/perspectives/ceo/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/perspectives/esg-analyst/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/risk/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/frameworks/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/causal/YYYY-MM-DD_{article_id}.json
    data/outputs/{slug}/recommendations/YYYY-MM-DD_{article_id}.json

Every file is JSONB-compatible (strict JSON, no comments, no trailing commas,
no Python-specific types).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.perspective_engine import CrispOutput
from engine.analysis.pipeline import PipelineResult
from engine.analysis.recommendation_engine import RecommendationResult
from engine.config import get_output_dir
from engine.index.sqlite_index import upsert_article

logger = logging.getLogger(__name__)


@dataclass
class WrittenFiles:
    insight: Path | None = None
    risk: Path | None = None
    frameworks: Path | None = None
    causal: Path | None = None
    recommendations: Path | None = None
    perspectives: dict[str, Path] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "insight": str(self.insight) if self.insight else None,
            "risk": str(self.risk) if self.risk else None,
            "frameworks": str(self.frameworks) if self.frameworks else None,
            "causal": str(self.causal) if self.causal else None,
            "recommendations": str(self.recommendations) if self.recommendations else None,
            "perspectives": {k: str(v) for k, v in (self.perspectives or {}).items()},
        }


def _date_prefix(published_at: str | None) -> str:
    if not published_at:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return published_at[:10]


def _filename(result: PipelineResult) -> str:
    return f"{_date_prefix(result.published_at)}_{result.article_id}.json"


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    return path


def write_insight(
    result: PipelineResult,
    insight: DeepInsight | None,
    perspectives: dict[str, CrispOutput],
    recommendations: RecommendationResult | None,
) -> WrittenFiles:
    slug = result.company_slug
    base = get_output_dir(slug)
    name = _filename(result)
    written = WrittenFiles(perspectives={})

    # Combined insight payload
    insight_payload: dict[str, Any] = {
        "article": {
            "id": result.article_id,
            "title": result.title,
            "url": result.url,
            "source": result.source,
            "published_at": result.published_at,
            "company_slug": result.company_slug,
        },
        "pipeline": result.to_dict(),
        "insight": insight.to_dict() if insight else None,
        "recommendations": recommendations.to_dict() if recommendations else None,
        "perspectives": {k: v.to_dict() for k, v in perspectives.items()},
        "meta": {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "2.0-primitives-l2",
        },
    }
    written.insight = _write(base / "insights" / name, insight_payload)

    # Mirror the row into the SQLite index so the API layer can read it fast
    try:
        upsert_article(insight_payload, written.insight)
    except Exception as exc:  # noqa: BLE001 — index failure must not break writes
        logger.warning("sqlite index upsert failed: %s", exc)

    # Split-out files for direct consumption by the UI API later
    if result.risk:
        written.risk = _write(base / "risk" / name, result.risk.to_dict())
    if result.frameworks:
        written.frameworks = _write(
            base / "frameworks" / name,
            {"frameworks": [fm.to_dict() for fm in result.frameworks]},
        )
    if result.causal_chains:
        written.causal = _write(
            base / "causal" / name,
            {
                "paths": [
                    {
                        "nodes": p.nodes,
                        "edges": p.edges,
                        "hops": p.hops,
                        "relationship_type": p.relationship_type,
                        "impact_score": p.impact_score,
                        "explanation": p.explanation,
                    }
                    for p in result.causal_chains
                ]
            },
        )
    if recommendations and not recommendations.do_nothing:
        written.recommendations = _write(
            base / "recommendations" / name, recommendations.to_dict()
        )

    # Perspective views (one file per lens)
    for lens, crisp in perspectives.items():
        path = base / "perspectives" / lens / name
        written.perspectives[lens] = _write(path, crisp.to_dict())

    return written
