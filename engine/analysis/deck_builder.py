"""Phase 48.C — tier-gated deck builder.

The SINGLE orchestrator used by both `api/routes/onboard_v3.py` and the
Sunday refresh cron (`engine/scheduler.run_weekly_deck_refresh_job`). Given a
company + a list of freshly-fetched candidate articles, it produces the
10-card deck:

    1. Rank candidates with the free heuristic (no LLM) → keep a buffer.
    2. Stages 1-9 on the buffer (cheap NLP + ontology); drop REJECTED.
    3. Rank survivors by criticality band + negativity + score.
    4. Top 3  → CRITICAL: Stage 10-12 + lede + Opus approval → persist.
    5. Next 7 → LIGHT:    Stages 1-9 only + deterministic approval → persist.
    6. An approval-rejected CRITICAL backfills from the next-ranked survivor.

Cost: ~`buffer` NLP calls (gpt-4.1-mini) + 3 Opus pipelines + 3 Opus
approvals per company. NewsAPI.ai tokens were already spent at fetch time.

Returns a summary dict. No SSE, no state machine, no tier-gate-in-pipeline
(the gate lives HERE, cleanly, not inside engine.main._run_article).
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Parallelism cap. 3 is the value proven safe against the rdflib/pyparsing
# SPARQL race (Phase 47.G/O). The process-wide _SPARQL_LOCK serialises the
# actual graph queries; threads still overlap on LLM network I/O.
_MAX_WORKERS = 3

_BAND_RANK = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}


@dataclass
class DeckSummary:
    company_slug: str
    fetched: int = 0
    processed: int = 0
    rejected: int = 0
    critical_published: int = 0
    light_published: int = 0
    approval_rejected: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    # Per-published-article rows so callers (onboard response, reonboard
    # verification) can show what landed without a second DB read.
    published_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_slug": self.company_slug,
            "fetched": self.fetched,
            "processed": self.processed,
            "rejected": self.rejected,
            "critical_published": self.critical_published,
            "light_published": self.light_published,
            "approval_rejected": self.approval_rejected,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "errors": self.errors[:10],
            "published_items": self.published_items,
        }


def _rank_composite(result: Any) -> float:
    """Higher = more deck-worthy. Band dominates, then severity/negativity,
    then score. This is what surfaces 'critical/negative first'."""
    crit = getattr(result, "criticality", None) or {}
    band = (crit.get("band") or "LOW").upper()
    score = float(crit.get("score") or 0.0)
    sent = getattr(getattr(result, "nlp", None), "sentiment", None)
    negativity = 2.0 if (isinstance(sent, (int, float)) and sent < 0) else 0.0
    # Phase 51.L — event severity is a more reliable "serious/negative ESG event"
    # signal than NLP sentiment alone: an enforcement/harm event (heavy_penalty /
    # violation floor 7, criminal indictment / license_revocation floor 8) lifts
    # the rank even when the sentiment classifier read neutral. Kept below the
    # 10-point band gap (max ~3) so it breaks ties WITHIN a band, never overrides
    # the band itself (the band already reflects severity via the event floor).
    event = getattr(result, "event", None)
    floor = 0.0
    if event is not None:
        try:
            floor = float(getattr(event, "score_floor", 0) or 0)
        except (TypeError, ValueError):
            floor = 0.0
    severity = max(0.0, min(1.0, (floor - 3.0) / 7.0)) * 3.0
    return _BAND_RANK.get(band, 0) * 10.0 + severity + negativity + score


def _to_article_dict(article: Any) -> dict[str, Any]:
    """Normalise an IngestedArticle (or dict) into the process_article input."""
    if isinstance(article, dict):
        return article
    return {
        "id": getattr(article, "id", ""),
        "title": getattr(article, "title", ""),
        "content": getattr(article, "content", ""),
        "summary": getattr(article, "summary", ""),
        "source": getattr(article, "source", ""),
        "url": getattr(article, "url", ""),
        "published_at": getattr(article, "published_at", ""),
        # Phase 53 (C) — carry the fetch lane so the pipeline can route an
        # industry/thematic article (company not named) past the cross-entity gate.
        "source_type": getattr(article, "source_type", "") or "",
        "metadata": getattr(article, "metadata", {}) or {},
    }


def _run_stages_1_to_9(article: Any, company: Any) -> Any | None:
    """Stages 1-9 for one article. Returns the PipelineResult, or None when
    the pipeline gate REJECTED it (not an ESG event)."""
    from engine.analysis.pipeline import process_article
    try:
        result = process_article(_to_article_dict(article), company)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deck] Stages 1-9 crashed for %s: %s",
                       getattr(article, "id", "?"), exc)
        return None
    if getattr(result, "rejected", False):
        return None
    return result


def _publish_critical(result: Any, company: Any) -> str:
    """Full Stage 10-12 + lede + Opus approval, then persist if approved.

    Returns "published" | "rejected_stage10" | "rejected_approval" | "error".
    """
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.analysis.unified_analysis import build_unified_analysis
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective
    from engine.analysis.esg_analyst_generator import generate_esg_analyst_perspective
    from engine.analysis.lede_writer import write_lede
    from engine.analysis.approval_gate import approve_analysis_for_display
    from engine.output.writer import write_insight

    insight = generate_deep_insight(result, company)
    if insight is None:
        return "rejected_stage10"

    perspectives: dict[str, Any] = {}
    try:
        perspectives["esg-analyst"] = generate_esg_analyst_perspective(insight, result, company)
    except Exception:  # noqa: BLE001
        perspectives["esg-analyst"] = transform_for_perspective(insight, result, "esg-analyst")
    try:
        perspectives["ceo"] = generate_ceo_narrative_perspective(insight, result, company)
    except Exception:  # noqa: BLE001
        perspectives["ceo"] = transform_for_perspective(insight, result, "ceo")
    perspectives["cfo"] = transform_for_perspective(insight, result, "cfo")

    try:
        recs = generate_recommendations(insight, result, company)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deck] Stage 12 raised for %s: %s", result.article_id, exc)
        recs = None

    # Compose the analysis (+ lede) so the approval LLM reviews the FINAL
    # reader-facing content. write_lede caches per article_id, so the
    # subsequent write_insight call reuses it with zero extra LLM cost.
    try:
        analysis = build_unified_analysis(result, insight, recommendations=recs)
        lede = write_lede(article_id=result.article_id,
                          insight={**insight.to_dict(), "analysis": analysis},
                          result=result, evidence_pack=None)
        if lede and lede.get("text"):
            analysis["lede"] = lede
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deck] analysis compose failed for %s: %s", result.article_id, exc)
        analysis = {}

    approval = approve_analysis_for_display(
        result=result, insight=insight, unified_analysis=analysis,
        recommendations=recs, tier="critical", company=company,
    )
    if not approval.approved:
        return "rejected_approval"

    try:
        write_insight(result, insight, perspectives, recs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deck] write_insight failed for %s: %s", result.article_id, exc)
        return "error"
    return "published"


def _publish_light(result: Any) -> str:
    """Stages 1-9 only → light analysis + deterministic approval → persist."""
    from engine.analysis.unified_analysis import build_light_analysis
    from engine.analysis.approval_gate import approve_analysis_for_display
    from engine.output.writer import write_light_insight

    analysis = build_light_analysis(result)
    approval = approve_analysis_for_display(
        result=result, insight=None, unified_analysis=analysis, tier="light",
    )
    if not approval.approved:
        return "rejected_approval"
    try:
        write_light_insight(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[deck] write_light_insight failed for %s: %s",
                       getattr(result, "article_id", "?"), exc)
        return "error"
    return "published"


def build_company_deck(
    company: Any,
    candidates: list[Any],
    *,
    n_critical: int = 3,
    n_total: int = 10,
) -> DeckSummary:
    """Build the tier-gated deck for one company. See module docstring."""
    t0 = time.monotonic()
    summary = DeckSummary(company_slug=getattr(company, "slug", "?"))
    summary.fetched = len(candidates)
    if not candidates:
        summary.elapsed_seconds = time.monotonic() - t0
        return summary

    # 1. Free heuristic pre-rank → keep a buffer (n_total + headroom for
    #    REJECTED drops + approval rejections).
    from engine.analysis.article_selector import select_top_n_for_pipeline
    buffer_n = min(len(candidates), n_total + 6)
    ranked = select_top_n_for_pipeline(
        candidates, n=buffer_n,
        company_slug=getattr(company, "slug", None),
        primary_industry=getattr(company, "industry", None),
    )

    # 2. Stages 1-9 on the buffer (parallel, SPARQL-lock-serialised).
    processed: list[Any] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=_MAX_WORKERS, thread_name_prefix="deck-s19",
    ) as pool:
        futs = {pool.submit(_run_stages_1_to_9, a, company): a for a in ranked}
        for fut in concurrent.futures.as_completed(futs):
            try:
                r = fut.result(timeout=90)
            except Exception:  # noqa: BLE001
                r = None
            if r is None:
                summary.rejected += 1
            else:
                processed.append(r)
    summary.processed = len(processed)
    if not processed:
        summary.elapsed_seconds = time.monotonic() - t0
        return summary

    # 3. Rank survivors: critical/negative first.
    processed.sort(key=_rank_composite, reverse=True)

    # 4. Top n_critical → CRITICAL (full pipeline + approval). Bounded
    #    backfill: cap the number of EXPENSIVE critical pipeline runs at
    #    n_critical + 3 so a company whose top articles keep failing
    #    approval can't trigger 16 full Opus pipelines (the JSW pathology:
    #    16 runs / 44 min for 1 published). A critical that fails the
    #    approval gate is DEMOTED to the light tier (rebuilt cheaply from
    #    Stages 1-9, no LLM, deterministic approval) so the article still
    #    appears as a quick-read card instead of vanishing — and we never
    #    show its fabricated lede/recs.
    critical_pool = list(processed)
    max_attempts = n_critical + 3
    # Phase 51.D — optional ESG-materiality floor for the CRITICAL tier.
    # DISABLED BY DEFAULT (0.0): an absolute floor can only work once the
    # criticality SCORE cleanly separates genuine ESG events from market noise.
    # The Phase-51.E weight rebalance (materiality-led, not financial-cascade-led)
    # does that separation at the source; the floor is a secondary trim that can
    # be re-enabled via SNOWKAP_CRITICAL_FLOOR once scores are confirmed to
    # spread (e.g. 0.35 under Opus). At 0.30 with the old CFO-financial weights it
    # demoted EVERYTHING — including a ₹600cr fraud — so it ships off.
    critical_floor = float(os.environ.get("SNOWKAP_CRITICAL_FLOOR", "0.0"))
    published_critical = 0
    attempts = 0
    idx = 0
    demoted: list[Any] = []  # criticals failing approval / below floor → light tier
    while published_critical < n_critical and idx < len(critical_pool) and attempts < max_attempts:
        result = critical_pool[idx]
        idx += 1
        crit_score = float((getattr(result, "criticality", None) or {}).get("score") or 0.0)
        if crit_score < critical_floor:
            # Not material enough for the critical tier — show it as a light card
            # rather than force market noise into "critical". Ranked by a
            # band+negativity+score composite (not pure score), so keep scanning.
            demoted.append(result)
            continue
        attempts += 1
        outcome = _publish_critical(result, company)
        if outcome == "published":
            published_critical += 1
            summary.published_items.append({
                "article_id": getattr(result, "article_id", ""),
                "title": (getattr(result, "title", "") or "")[:200],
                "tier": "critical",
                "has_recs": True,
            })
        elif outcome == "rejected_approval":
            summary.approval_rejected += 1
            demoted.append(result)  # show it as light, not nothing
        elif outcome == "rejected_stage10":
            summary.rejected += 1
        else:
            summary.errors.append(f"critical {getattr(result,'article_id','?')}: {outcome}")
    summary.critical_published = published_critical

    # Light tier = approval-demoted criticals + the untried remainder of the
    # pool, capped to fill the deck to n_total.
    light_pool = demoted + critical_pool[idx:]
    light_slots = max(0, n_total - published_critical)

    published_light = 0
    for result in light_pool:
        if published_light >= light_slots:
            break
        outcome = _publish_light(result)
        if outcome == "published":
            published_light += 1
            summary.published_items.append({
                "article_id": getattr(result, "article_id", ""),
                "title": (getattr(result, "title", "") or "")[:200],
                "tier": "light",
                "has_recs": False,
            })
        elif outcome == "rejected_approval":
            summary.approval_rejected += 1
        else:
            summary.errors.append(f"light {getattr(result,'article_id','?')}: {outcome}")
    summary.light_published = published_light

    summary.elapsed_seconds = time.monotonic() - t0
    logger.info(
        "[deck] %s: fetched=%d processed=%d critical=%d light=%d "
        "approval_rejected=%d (%.0fs)",
        summary.company_slug, summary.fetched, summary.processed,
        summary.critical_published, summary.light_published,
        summary.approval_rejected, summary.elapsed_seconds,
    )
    return summary
