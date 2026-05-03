"""Shared output formatting helpers.

Provides :func:`format_pipeline_artifact` — a single entry point the rest of
the codebase calls to build the full combined payload (pipeline + insight +
perspectives + recommendations) in a stable shape.
"""

from __future__ import annotations

from typing import Any

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.perspective_engine import CrispOutput
from engine.analysis.pipeline import PipelineResult
from engine.analysis.recommendation_engine import RecommendationResult


def format_pipeline_artifact(
    result: PipelineResult,
    insight: DeepInsight | None,
    perspectives: dict[str, CrispOutput],
    recommendations: RecommendationResult | None,
) -> dict[str, Any]:
    """Return a single JSONB-compatible dict with every stage output."""
    return {
        "article": {
            "id": result.article_id,
            "title": result.title,
            "url": result.url,
            "source": result.source,
            "published_at": result.published_at,
            "company_slug": result.company_slug,
            "image_url": result.image_url,
        },
        "pipeline": result.to_dict(),
        "insight": insight.to_dict() if insight else None,
        "recommendations": recommendations.to_dict() if recommendations else None,
        "perspectives": {k: v.to_dict() for k, v in perspectives.items()},
    }
