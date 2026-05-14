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


def _parse_budget_cr(budget: Any) -> float | None:
    """Phase 3 §5.2 — extract a numeric ₹ Cr from Recommendation.estimated_budget
    so RoleDistinctPayload's RecommendationStub can carry a typed value.

    Tolerates the legacy free-form string format: '₹500 Cr', 'Rs. 1,200 Cr',
    '₹0.5-1 Cr' (takes the upper bound), '500'. Returns None when no
    numeric budget can be parsed (rec stays in the role payload, just
    without a budget figure).
    """
    if budget is None:
        return None
    if isinstance(budget, (int, float)):
        return float(budget)
    s = str(budget)
    if not s:
        return None
    import re
    # Match all numbers (handles ranges by taking the largest)
    matches = re.findall(r"[\d,]+(?:\.\d+)?", s)
    if not matches:
        return None
    try:
        nums = [float(m.replace(",", "")) for m in matches]
        return max(nums) if nums else None
    except ValueError:
        return None


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

    # Phase 3 §5.1 — assemble the deterministic EvidencePack from existing
    # pipeline + insight outputs and stamp it onto the payload. Today this
    # is consumer-only (the role generators in the deferred Stage 11 split
    # will read it). Building it here means downstream code never has to
    # reconstruct it from raw pipeline state.
    evidence_pack_dict: dict[str, Any] | None = None
    role_payloads_dict: dict[str, dict[str, Any]] = {}
    try:
        from engine.analysis.evidence_pack import build_evidence_pack
        insight_dict_for_pack = insight.to_dict() if insight else {}
        # Splice in the perspective views so the pack builder can read
        # `insight.perspectives.ceo.stakeholder_map`
        if perspectives:
            insight_dict_for_pack = dict(insight_dict_for_pack)
            insight_dict_for_pack["perspectives"] = {
                k: v.to_dict() for k, v in perspectives.items()
            }
        pack = build_evidence_pack(result, insight_dict_for_pack)
        evidence_pack_dict = pack.to_dict()

        # Phase 3 §5.2 — Stage 11 dispatcher. Build all 3 role payloads
        # from the same EvidencePack and stamp them onto the persisted
        # insight. Future LLM-prompt versions of the generators replace
        # the body without changing this call site. Any single-role
        # failure surfaces as a placeholder payload — never raises.
        try:
            from engine.analysis.role_generators import (
                RecommendationStub,
                dispatch_role_payloads_as_dict,
            )
            # Convert recommendation_engine.Recommendation → RecommendationStub
            # so the role generators can apply per-role whitelists. Empty
            # list when recs are absent or do_nothing-only.
            rec_stubs: list[RecommendationStub] = []
            if recommendations and getattr(recommendations, "recommendations", None):
                for r in recommendations.recommendations:
                    rec_stubs.append(RecommendationStub(
                        title=getattr(r, "title", "") or "",
                        type=getattr(r, "type", "") or "",
                        budget_cr=_parse_budget_cr(getattr(r, "estimated_budget", None)),
                        payback_months=getattr(r, "payback_months", None),
                        framework_section=getattr(r, "framework_section", "") or "",
                    ))
            role_payloads_dict = dispatch_role_payloads_as_dict(
                pack, recommendations=rec_stubs,
            )
        except Exception as exc:  # noqa: BLE001 — dispatcher must never block writes
            logger.debug("role-payload dispatch failed (non-fatal): %s", exc)
    except Exception as exc:  # noqa: BLE001 — never block writes on the scaffold
        logger.debug("evidence_pack build failed (non-fatal): %s", exc)

    # Combined insight payload
    insight_payload: dict[str, Any] = {
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
        # Phase 3 §5.1 — structured EvidencePack stamped at write time so
        # consumers (future role generators, debugging tools) read it
        # directly without rebuilding from pipeline + insight.
        "evidence_pack": evidence_pack_dict,
        # Phase 3 §5.2 — per-role RoleDistinctPayload (CFO / CEO /
        # esg-analyst). The frontend can read role_payloads[role] to
        # render the role-distinct headline + hero metric + takeaways
        # without re-deriving them from pipeline state. Empty {} when
        # the dispatcher couldn't build any role (e.g. REJECTED article).
        "role_payloads": role_payloads_dict,
        "meta": {
            "written_at": datetime.now(timezone.utc).isoformat(),
            # W5 — schema bump signals the per-role rebuild (W4a: why_critical_for_role,
            # W4b: role-aware personal_stakes, W4d: role_panel_order). Old "2.0-primitives-l2"
            # files are still readable but trigger on-demand re-enrichment on next click.
            "schema_version": "2.1-role-distinct",
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
