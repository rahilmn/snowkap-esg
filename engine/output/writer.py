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

    # Phase 32 — compose the unified 4-bullet analysis block from the same
    # pipeline + insight + perspectives + recommendations. Stamped at write
    # time so the frontend reads it directly with no extra API hop.
    # Empty dict on builder failure — never blocks the write.
    unified_analysis_dict: dict[str, Any] = {}
    try:
        from engine.analysis.unified_analysis import build_unified_analysis
        # Phase 32 — surface SASB warning + external benchmarks on the
        # unified analysis block. Both are best-effort: empty/None falls
        # through to the neutral path in the composer.
        sasb_warning = None
        benchmarks: list[dict[str, Any]] = []
        try:
            from engine.config import get_company
            from engine.ontology.sasb_loader import is_sector_mapped
            from engine.analysis.benchmarks import get_benchmarks_for_company
            company = get_company(result.company_slug)
            sasb_cat = getattr(company, "sasb_category", None) if company else None
            if sasb_cat is None or not is_sector_mapped(sasb_cat):
                sasb_warning = "sasb_unmapped"
            benchmarks = get_benchmarks_for_company(result.company_slug, max_n=4)
        except Exception as exc:  # noqa: BLE001 — best-effort enrichment
            logger.debug("sasb/benchmarks enrich failed (non-fatal): %s", exc)
        unified_analysis_dict = build_unified_analysis(
            result, insight,
            recommendations=recommendations,
            sasb_warning=sasb_warning,
            benchmarks=benchmarks,
        )
    except Exception as exc:  # noqa: BLE001 — additive, never block writes
        logger.warning("unified_analysis build failed (non-fatal): %s", exc)

    # Stamp the unified analysis on the insight dict so frontend reads
    # `insight.analysis` directly (single source of truth per Phase 32).
    insight_dict_with_analysis: dict[str, Any] | None = None
    if insight is not None:
        insight_dict_with_analysis = insight.to_dict()
        if unified_analysis_dict:
            insight_dict_with_analysis["analysis"] = unified_analysis_dict

    # Phase 39 — editorial lede pass. Adds the 2-3 sentence story-style
    # opener that sits above the structured WHAT CHANGED / WHY IT MATTERS
    # sections in the email + in-app /now article sheet + chat seed.
    # Best-effort: never blocks the write. Per-article cached in-memory
    # so resends pay zero LLM cost. See engine/analysis/lede_writer.py.
    if unified_analysis_dict and insight_dict_with_analysis is not None:
        try:
            from engine.analysis.lede_writer import write_lede
            lede = write_lede(
                article_id=result.article_id,
                insight=insight_dict_with_analysis,
                result=result,
                evidence_pack=evidence_pack_dict,
            )
            if lede and lede.get("text"):
                # Stamp on the analysis block so the frontend, email
                # renderer, and chat seed all read from the same place.
                unified_analysis_dict["lede"] = lede
                insight_dict_with_analysis["analysis"]["lede"] = lede
        except Exception as exc:  # noqa: BLE001 — additive, never block writes
            logger.warning("lede_writer failed (non-fatal): %s", exc)

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
        "insight": insight_dict_with_analysis,
        "recommendations": recommendations.to_dict() if recommendations else None,
        # Phase 32 — legacy `perspectives` + `role_payloads` blocks stay on
        # disk for 1 release (DECISION 5.2) so existing frontends + tests
        # don't break. Drop in Phase 30. Frontend reads `insight.analysis`
        # first; falls back to these only when `analysis` is absent.
        "perspectives": {k: v.to_dict() for k, v in perspectives.items()},
        # Phase 3 §5.1 — structured EvidencePack stamped at write time so
        # consumers (future role generators, debugging tools) read it
        # directly without rebuilding from pipeline + insight.
        "evidence_pack": evidence_pack_dict,
        # Phase 3 §5.2 — per-role RoleDistinctPayload (CFO / CEO /
        # esg-analyst). 1-release shim per DECISION 5.2; frontend prefers
        # insight.analysis. Empty {} when the dispatcher couldn't build any
        # role (e.g. REJECTED article).
        "role_payloads": role_payloads_dict,
        "meta": {
            "written_at": datetime.now(timezone.utc).isoformat(),
            # Phase 34 polish — bumped 3.1-intelligence-hardened -> 3.2-template-hardened.
            # Existing on-disk files (all 7 baseline companies + every
            # session-onboarded tenant) trigger on-demand re-enrichment
            # on next view via engine/analysis/on_demand.py's schema gate,
            # picking up the unified-analysis template fixes:
            #   - criticality_summary cleaned (no broken sentences / unbalanced parens)
            #   - stakes_for_company always populated (deterministic fallback)
            #   - materiality_weight read from the right source (clamped [0,1])
            #   - top_risk_categories dropped on positive events (no Legal/Political/Social noise)
            #   - next_decision_window holds a real label + by_date, not the exposure headline
            #   - recommendation titles deduplicated of trailing "by <deadline>"
            # Old "3.x" and "2.x" files remain readable; the gate just
            # triggers a fresh pipeline run.
            # Phase 39 — bump to 3.3-editorial-lede. Existing on-disk
            # insights at schema 3.2 will re-enrich on next view via the
            # engine.analysis.on_demand schema-stale check, picking up
            # the new analysis.lede block.
            "schema_version": "3.3-editorial-lede",
        },
    }

    # Phase 45.I — LAST-LINE safety net at the persist boundary.
    # Earlier defensive fixes (Phase 45.H) tried to guarantee these two
    # fields inside the build pipeline, but a real onboard showed the
    # contract still violated on disk: criticality_summary='' and
    # recommendations=[]. That means SOME upstream code path is
    # bypassing the inner fallbacks (silently caught exception, alternate
    # write path, or a code branch we haven't identified yet).
    # This block ENFORCES the contract at the outermost layer — right
    # before _write — so the on-disk JSON is guaranteed to satisfy the
    # frontend + validation contract regardless of any upstream silent
    # failure. Pure-Python, no LLM calls, no network — runs in <1 ms.
    if insight_dict_with_analysis is not None:
        analysis_block = insight_dict_with_analysis.get("analysis") or {}
        why_block = analysis_block.get("why_it_matters") or {}
        if not why_block.get("criticality_summary"):
            band_label = (
                analysis_block.get("why_it_matters") or {}
            ).get("materiality_band") or "MEDIUM"
            band_prefix = {
                "CRITICAL": "Critical",
                "HIGH": "High priority",
                "MEDIUM": "Worth reviewing",
                "LOW": "Low priority",
            }.get(str(band_label).upper(), "Worth reviewing")
            # Try the proper builder one more time; fall to a literal.
            recovered = ""
            try:
                from engine.analysis.role_explainer import build_criticality_summary
                recovered = build_criticality_summary({
                    "criticality": (
                        getattr(insight, "criticality", None) or {}
                        if insight else {}
                    ),
                    "decision_summary": (
                        getattr(insight, "decision_summary", None) or {}
                        if insight else {}
                    ),
                    "event_polarity": (
                        getattr(insight, "event_polarity", "") or ""
                        if insight else ""
                    ),
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Phase 45.I safety net: criticality_summary recompute "
                    "failed (%s) — falling to literal",
                    exc,
                )
            if not recovered:
                # Phase 47.L — article-specific fallback using headline.
                # Avoids the user seeing identical "ESG-relevant article
                # flagged" text on every card when the LLM was thin.
                article_title = ""
                if insight is not None:
                    article_title = (
                        getattr(insight, "headline", "") or ""
                    ).strip()
                if not article_title:
                    article_title = (result.title or "").strip()
                # Take first clause before separator
                topic = article_title
                for sep in (" — ", " - ", " | ", " : ", ": "):
                    if sep in topic:
                        topic = topic.split(sep, 1)[0]
                topic = topic.strip().rstrip(".")[:100] or "this article"
                recovered = f"{band_prefix} — developing story: {topic}."
            why_block["criticality_summary"] = recovered[:280]
            analysis_block["why_it_matters"] = why_block
            insight_dict_with_analysis["analysis"] = analysis_block
            insight_payload["insight"] = insight_dict_with_analysis
            logger.warning(
                "Phase 45.I safety net fired for %s: stamped criticality_summary",
                result.article_id,
            )

    # Same enforcement for recommendations. If the article is not
    # rejected AND has a populated insight (i.e. Stage 10 succeeded),
    # there MUST be at least one recommendation on disk so the UI never
    # shows blank "RECOMMENDED ACTIONS" and so test 06 (recs vary
    # article-to-article) has something to compare.
    if insight_dict_with_analysis is not None and not result.rejected:
        recs_block = insight_payload.get("recommendations")
        existing_recs = []
        if isinstance(recs_block, dict):
            existing_recs = recs_block.get("recommendations") or []
        if not existing_recs:
            # Synthesize a deterministic monitor rec inline so persisted
            # JSON has ≥1 row. Uses the article's primary theme to vary
            # the title across articles (test 06's uniqueness check).
            theme = ""
            try:
                themes = (result.themes.primary_theme if result.themes else "") or ""
                theme = str(themes).replace("topic_", "").replace("_", " ") or "ESG signal"
            except Exception:  # noqa: BLE001
                theme = "ESG signal"
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            deadline_iso = (
                _dt.now(_tz.utc) + _td(days=30)
            ).date().isoformat()
            fallback_rec = {
                "title": f"Monitor {theme} signals for materiality drift",
                "description": (
                    f"Track this {theme} disclosure for materiality drift. "
                    "Escalation trigger: event severity increase or ₹10 Cr "
                    "exposure threshold breach. If crossed, re-run full "
                    "analysis and revisit within 14 days."
                ),
                "type": "operational",
                "responsible_party": "ESG / Risk team",
                "framework_section": "BRSR:P1:Q1 (stakeholder review cycle)",
                "deadline": deadline_iso,
                "estimated_budget": "₹0 Cr (internal monitoring)",
                "success_criterion": "Materiality re-rated within 30 days",
                "urgency": "short_term",
                "confidence": "high",
                "validation_notes": (
                    "Phase 45.I safety net rec — Stage 12 returned no "
                    "validated recommendations for this article. This "
                    "deterministic monitor ensures the UI never shows "
                    "blank RECOMMENDED ACTIONS."
                ),
                "profitability_link": (
                    "Prevents materiality drift surprise. No active cost; "
                    "only watch-list inclusion."
                ),
                "roi_percentage": None,
                "payback_months": None,
                "priority": "LOW",
                "peer_benchmark": "Standard practice",
                "audit_trail": [{
                    "source": "ontology",
                    "ref": "phase_45i_safety_net",
                    "value": "Inserted at writer.py persist boundary because "
                             "upstream Stage 12 returned 0 recommendations.",
                }],
            }
            new_recs_block = {
                "recommendations": [fallback_rec],
                "do_nothing": False,
                "gate_reason": "phase_45i_safety_net",
                "generator_count": 0,
                "validated_count": 1,
                "priority_matrix": None,
                "recommendation_rankings": None,
            }
            insight_payload["recommendations"] = new_recs_block
            logger.warning(
                "Phase 45.I safety net fired for %s: stamped fallback rec",
                result.article_id,
            )

    written.insight = _write(base / "insights" / name, insight_payload)

    # Mirror the row into the SQLite index so the API layer can read it fast
    try:
        upsert_article(insight_payload, written.insight)
    except Exception as exc:  # noqa: BLE001 — index failure must not break writes
        logger.warning("sqlite index upsert failed: %s", exc)

    # POW-2 — dual-write to the new industry-shared article_pool and
    # per-company company_article_view tables. Failure is non-fatal
    # during the migration window so the legacy article_index still
    # serves reads. See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.1.
    try:
        _upsert_pool_and_view(result, insight_payload, unified_analysis_dict)
    except Exception as exc:  # noqa: BLE001
        logger.warning("article_pool / company_article_view upsert failed (non-fatal): %s", exc)

    # Phase 51.B — mirror the full insight payload into Postgres so the
    # detail view survives Railway restarts (the on-disk JSON is ephemeral)
    # and data/outputs can eventually leave the image. Non-fatal: the disk
    # write above remains the immediate source of truth.
    try:
        from engine.models import insight_payload as insight_payload_store
        insight_payload_store.upsert(
            result.article_id, result.company_slug, insight_payload,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("insight_payload DB upsert failed (non-fatal): %s", exc)

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


def write_light_insight(result: PipelineResult) -> WrittenFiles:
    """Phase 48.C — persist a LIGHT (headline-tier) article.

    The 7 low-priority deck articles run Stages 1-9 only — no Stage 10
    deep insight, no Stage 11 perspectives, no Stage 12 recs, no lede.
    This writes a valid deck card (what_changed + banded why_it_matters +
    frameworks + risks) at LOW band so it sorts below the 3 critical.

    Reuses the same persistence as `write_insight` (disk JSON + sqlite
    index + article_pool + company_article_view) via `_upsert_pool_and_view`,
    so chat, /now/feed and the newsletter all read it uniformly. Pure-Python
    — no LLM calls (the whole point of the light tier).
    """
    from engine.analysis.unified_analysis import build_light_analysis

    slug = result.company_slug
    base = get_output_dir(slug)
    name = _filename(result)
    written = WrittenFiles(perspectives={})

    light_analysis = build_light_analysis(result)

    # event_polarity from NLP sentiment (no Stage 10 to compute it properly)
    polarity = "neutral"
    sent = getattr(getattr(result, "nlp", None), "sentiment", None)
    if isinstance(sent, (int, float)):
        polarity = "positive" if sent > 0 else "negative" if sent < 0 else "neutral"

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
        # Synthetic insight: carries the light analysis + a LOW criticality
        # band so _upsert_pool_and_view sorts it below the critical 3.
        "insight": {
            "headline": result.title,
            "analysis": light_analysis,
            "event_polarity": polarity,
            "criticality": {
                "band": "LOW",
                "score": float((getattr(result, "criticality", None) or {}).get("score") or 0.0),
            },
        },
        "recommendations": None,
        "perspectives": {},
        "evidence_pack": None,
        "role_payloads": {},
        "meta": {
            "written_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "3.3-editorial-lede",
            "tier": "light",
        },
    }

    written.insight = _write(base / "insights" / name, insight_payload)

    try:
        upsert_article(insight_payload, written.insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("light: sqlite index upsert failed: %s", exc)

    try:
        _upsert_pool_and_view(result, insight_payload, light_analysis)
    except Exception as exc:  # noqa: BLE001
        logger.warning("light: article_pool / company_article_view upsert failed: %s", exc)

    # Split-out framework/risk/causal files for direct UI consumption.
    if result.risk:
        written.risk = _write(base / "risk" / name, result.risk.to_dict())
    if result.frameworks:
        written.frameworks = _write(
            base / "frameworks" / name,
            {"frameworks": [fm.to_dict() for fm in result.frameworks]},
        )

    return written


# ---------------------------------------------------------------------------
# POW-2 — Dual-write to article_pool + company_article_view.
# ---------------------------------------------------------------------------


def _upsert_pool_and_view(
    result: Any,
    insight_payload: dict[str, Any],
    unified_analysis_dict: dict[str, Any],
) -> None:
    """Write the new industry-shared + per-company tables.

    Called from `write_insight()` after the legacy `article_index`
    upsert. Failure is logged but never raised — during the POW
    migration window the legacy index still serves reads.

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.1 — "Where each piece lands".
    """
    from engine.analysis.unified_analysis import split_analysis
    from engine.models import article_pool, company_article_view
    from engine.config import get_company, load_companies

    article = insight_payload.get("article") or {}
    article_id = article.get("id") or result.article_id
    url = article.get("url") or result.url
    title = article.get("title") or result.title or ""
    source = article.get("source") or result.source
    published_at = article.get("published_at") or result.published_at
    company_slug = result.company_slug

    if not article_id or not url or not company_slug:
        logger.warning(
            "_upsert_pool_and_view: missing id/url/slug (id=%r url=%r slug=%r)",
            article_id, url, company_slug,
        )
        return

    # 1. Industry-shared fields → article_pool
    company = get_company(company_slug)
    primary_industry = (getattr(company, "industry", None) or "").strip() or "Unknown"
    pipeline = insight_payload.get("pipeline") or {}
    themes = pipeline.get("themes") or {}
    primary_theme = themes.get("primary_theme") or None
    primary_pillar = themes.get("primary_pillar") or None
    event = pipeline.get("event") or {}
    event_id = event.get("event_id") or None
    nlp = pipeline.get("nlp") or {}
    # Stage 12 stamps event_polarity onto the deep insight; fall back to
    # NLP sentiment direction when the insight is absent.
    event_polarity = (insight_payload.get("insight") or {}).get("event_polarity")
    if not event_polarity:
        s = nlp.get("sentiment")
        if isinstance(s, (int, float)):
            event_polarity = "positive" if s > 0 else "negative" if s < 0 else "neutral"

    # `material_industries` — ontology-driven list of industries where
    # this theme passes the materiality floor. Always includes the
    # company's primary_industry so the article shows on at least one
    # deck.
    all_industries = sorted({c.industry for c in load_companies() if c.industry})
    material_industries = article_pool.compute_material_industries(
        primary_theme, all_industries,
    )
    if primary_industry not in material_industries:
        material_industries.insert(0, primary_industry)

    # Industry-shared analysis = what_changed + its methodology block.
    shared, personalised = split_analysis(unified_analysis_dict or {})

    # Phase 48.E — carry the hero image through the deck. article_pool has
    # no dedicated image column, so the URL rides inside shared_analysis
    # (which deck_for_company returns and the frontend already reads). This
    # is what makes SwipeCard's `article.image_url` render — previously the
    # NowPage adapter hardcoded "" because nothing surfaced the image.
    img = (
        (insight_payload.get("article") or {}).get("image_url")
        or getattr(result, "image_url", "")
        or ""
    )
    if img:
        shared["image_url"] = img
        personalised["image_url"] = img

    article_pool.upsert(
        article_id=article_id,
        url=url,
        title=title,
        source=source,
        published_at=published_at,
        primary_industry=primary_industry,
        material_industries=material_industries,
        primary_pillar=primary_pillar,
        primary_theme=primary_theme,
        event_id=event_id,
        event_polarity=event_polarity,
        shared_analysis=shared,
    )

    # 2. Per-company fields → company_article_view
    insight = insight_payload.get("insight") or {}
    criticality = insight.get("criticality") or {}
    crit_score = float(criticality.get("score") or 0.0)
    crit_band = (criticality.get("band") or "MEDIUM").upper()

    # Phase 47.P — LLM materiality escalation.
    #
    # The deterministic criticality_scorer applies a 0.2 staleness penalty
    # on articles >30 days old and a 0.2 polarity-drift penalty when the
    # narrative tone disagrees with the event polarity. For positive ESG
    # transition events (e.g. -47% Scope 1+2 reduction with ₹755 Cr cascade
    # upside) both penalties can fire AND the financial_magnitude component
    # is zero (upside, not exposure), driving the band to LOW.
    #
    # Stage 10's deep-insight LLM, by contrast, has seen the full article
    # body PLUS the company context (industry, painpoints, KPIs, framework
    # region) and rates these as MODERATE / HIGH / CRITICAL on the
    # `decision_summary.materiality` field. That's the more reliable
    # signal for surfacing to the reader.
    #
    # Rule: if the LLM materiality is HIGHER than the engine band,
    # escalate. Never DOWNGRADE the engine band — when the engine
    # detects a CRITICAL signal, that stands.
    _LLM_TO_ENGINE = {
        "CRITICAL": "CRITICAL",
        "HIGH": "HIGH",
        "MODERATE": "MEDIUM",
        "MEDIUM": "MEDIUM",
        "LOW": "LOW",
    }
    _BAND_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    llm_mat = (
        (insight.get("decision_summary") or {}).get("materiality") or ""
    ).strip().upper()
    llm_band = _LLM_TO_ENGINE.get(llm_mat)
    if llm_band and _BAND_RANK.get(llm_band, 0) > _BAND_RANK.get(crit_band, 0):
        crit_band = llm_band

    company_article_view.upsert(
        article_id=article_id,
        company_slug=company_slug,
        personalised_analysis=personalised,
        criticality_score=crit_score,
        criticality_band=crit_band,
    )
