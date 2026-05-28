"""Phase 32 — Single unified analysis composer.

Replaces the per-role split (CFO / CEO / ESG Analyst) with one
horizontally-consumable brief built from four news-flow bullets:

  1. **what_changed**      — the event itself
  2. **why_it_matters**    — industry/company materiality + ₹ stakes
  3. **what_it_triggers**  — concrete obligations (frameworks, deadlines, actions)
  4. **what_to_watch**     — forward signal (trajectory, lead indicators, benchmarks)

Plus a `methodology` block that the per-bullet `(i)` icon scopes into.

Pure-Python. Composes already-computed engine outputs (PipelineResult +
DeepInsight + perspectives + RecommendationResult). Never calls the LLM
— every field is derived from existing pipeline state.

Wired into `engine/output/writer.py::write_insight()`. Result is stamped
on the persisted insight payload at ``insight.analysis`` and the
schema_version bumps to ``3.2-template-hardened`` so existing on-disk
files re-enrich on next view via the on_demand path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bullet 1 — what_changed
# ---------------------------------------------------------------------------


def _build_what_changed(result: Any, insight: Any) -> dict[str, Any]:
    """The event itself — pulled from DeepInsight.headline + Stage-3 event."""
    headline = ""
    polarity = "neutral"
    event_type = ""

    if insight is not None:
        headline = (getattr(insight, "headline", "") or "")[:240]
        polarity = getattr(insight, "event_polarity", "neutral") or "neutral"

    if result is not None and getattr(result, "event", None) is not None:
        event = result.event
        event_type = getattr(event, "event_id", "") or getattr(event, "event_type", "") or ""

    # Fall back to article title if no LLM headline (e.g. SECONDARY tier with
    # only Stages 1–9 run).
    if not headline and result is not None:
        headline = (getattr(result, "title", "") or "")[:240]

    return {
        "headline": headline,
        "event_type": event_type,
        "polarity": polarity,
        "source": getattr(result, "source", "") if result else "",
        "published_at": getattr(result, "published_at", "") if result else "",
        "url": getattr(result, "url", "") if result else "",
    }


# ---------------------------------------------------------------------------
# Bullet 2 — why_it_matters
# ---------------------------------------------------------------------------


_COMPONENT_LABELS = {
    "materiality": "industry materiality",
    "financial_magnitude": "rupee impact size",
    "actionability": "deadline urgency",
    "painpoint_match": "painpoint match",
    "recency": "freshness",
    "source_authority": "source authority",
    "sentiment_trajectory": "sentiment trajectory",
}


def _dominant_signal(components: dict[str, Any]) -> str:
    """Return the highest-scoring criticality component name."""
    best_name, best_val = "", -1.0
    for k in _COMPONENT_LABELS:
        v = components.get(k)
        if isinstance(v, (int, float)) and float(v) > best_val:
            best_name, best_val = k, float(v)
    return best_name


def _financial_exposure_block(insight: Any) -> dict[str, Any]:
    """Extract the canonical ₹ exposure from decision_summary + financial_timeline.

    Returns a dict with ``amount_cr``, ``kind``, ``source``. Empty when no
    figure is available.
    """
    if insight is None:
        return {}

    decision = getattr(insight, "decision_summary", None) or {}
    if isinstance(decision, dict):
        exposure = decision.get("financial_exposure")
        if exposure and str(exposure).strip().lower() not in {"n/a", "none", "null", ""}:
            # Try to parse out the rupee amount in Cr for downstream rendering.
            amount = _parse_inr_cr(str(exposure))
            return {
                "amount_cr": amount,
                "kind": "exposure",
                "source": "engine_estimate",
                "label": str(exposure)[:140],
            }

    # Fall back to financial_timeline.immediate.inr_cr (deterministic from the
    # primitive engine, computed BEFORE the LLM ran).
    ft = getattr(insight, "financial_timeline", None) or {}
    if isinstance(ft, dict):
        immediate = ft.get("immediate") or {}
        if isinstance(immediate, dict):
            inr = immediate.get("inr_cr")
            if isinstance(inr, (int, float)) and inr > 0:
                return {
                    "amount_cr": float(inr),
                    "kind": "immediate",
                    "source": "primitive_engine",
                    "label": immediate.get("headline") or f"~₹{inr:.0f} Cr",
                }

    return {}


_INR_PATTERN_CR = None


def _parse_inr_cr(text: str) -> float | None:
    """Best-effort: extract the first ₹X Cr figure from a free-form string.

    Tolerates ranges ("₹100-150 Cr" → 150), commas, "lakh crore" notations.
    Returns ``None`` when nothing parseable is present.
    """
    global _INR_PATTERN_CR
    if _INR_PATTERN_CR is None:
        import re
        _INR_PATTERN_CR = re.compile(
            r"₹\s*([\d,]+(?:\.\d+)?)\s*(?:[\-–to]+\s*([\d,]+(?:\.\d+)?))?\s*(?:lakh\s+)?(?:Cr|crore)",
            re.IGNORECASE,
        )
    if not text:
        return None
    m = _INR_PATTERN_CR.search(text)
    if not m:
        return None
    try:
        # Take the upper bound when a range is present (conservative for risk).
        upper = m.group(2) or m.group(1)
        return float(upper.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _stakes_text(insight: Any) -> str:
    """Plain-English stakes paragraph from personal_stakes_generator."""
    if insight is None:
        return ""
    stakes = getattr(insight, "stakes_for_company", None) or {}
    if not isinstance(stakes, dict):
        return ""
    para = stakes.get("personal_stakes_paragraph") or ""
    return str(para)[:480]


def _build_why_it_matters(
    result: Any, insight: Any, sasb_warning: str | None = None,
) -> dict[str, Any]:
    """Industry/company materiality bullet."""
    criticality = getattr(insight, "criticality", None) or {} if insight else {}
    components = criticality.get("components") or {}
    band = (criticality.get("band") or "MEDIUM").upper()
    dominant = _dominant_signal(components)

    # Phase 47.P — LLM band escalation (mirrors writer.write_insight).
    # When Stage 10's decision_summary.materiality rates higher than the
    # deterministic engine band, escalate so the reader's UI shows the
    # LLM's view. The LLM has seen article body + company context;
    # engine penalties (staleness > 30 days, polarity drift on
    # positive transition events) can mute the score artificially.
    _LLM_TO_ENGINE = {
        "CRITICAL": "CRITICAL", "HIGH": "HIGH",
        "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW",
    }
    _BAND_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    if insight is not None:
        ds = getattr(insight, "decision_summary", None)
        if isinstance(ds, dict):
            llm_mat = (ds.get("materiality") or "").strip().upper()
            llm_band = _LLM_TO_ENGINE.get(llm_mat)
            if llm_band and _BAND_RANK.get(llm_band, 0) > _BAND_RANK.get(band, 0):
                band = llm_band

    # Materiality weight is the float 0.0–1.0 the ontology returns from
    # `query_materiality_weight(topic, industry)`. The old code read
    # `esg_correlation` (an int 0–2 across 5 dimensions), which produced
    # values like 2.0 that violated the [0, 1] contract.
    materiality_weight: float | None = None
    if result is not None and getattr(result, "relevance", None) is not None:
        rel = result.relevance
        raw = getattr(rel, "materiality_weight", None)
        if isinstance(raw, (int, float)):
            materiality_weight = float(raw)
    if materiality_weight is None:
        m = components.get("materiality")
        if isinstance(m, (int, float)):
            materiality_weight = float(m)
    # Defensive clamp — neither upstream caller should ever exceed 1.0,
    # but a clamp here makes downstream renderers safe.
    if materiality_weight is not None:
        materiality_weight = max(0.0, min(1.0, materiality_weight))

    summary = (getattr(insight, "criticality_summary", "") if insight else "") or ""
    exposure = _financial_exposure_block(insight)
    stakes = _stakes_text(insight)
    # Phase 45.H — defensive fallback when insight_generator's role_explainer
    # block silently failed (caught by its try/except, leaving the
    # criticality_summary field as the dataclass default ""). The reader
    # MUST see a non-empty sentence here — it's the first thing they
    # read after the headline. Recompute inline from the criticality
    # block + exposure + band using the same logic build_criticality_summary
    # uses. Defensive — only runs when the upstream stamp is missing.
    if not summary:
        try:
            from engine.analysis.role_explainer import build_criticality_summary
            # Build the dict shape build_criticality_summary expects: it
            # reads .criticality.components, .decision_summary, .event_polarity.
            decision_for_summary: dict[str, Any] = {}
            if insight is not None:
                ds = getattr(insight, "decision_summary", None)
                if isinstance(ds, dict):
                    decision_for_summary = ds
            recovered = build_criticality_summary({
                "criticality": criticality,
                "decision_summary": decision_for_summary,
                "event_polarity": getattr(insight, "event_polarity", "") if insight else "",
            })
            if recovered:
                summary = recovered
        except Exception as exc:  # noqa: BLE001
            logger.warning("criticality_summary fallback failed (%s)", exc)
            # Last-resort literal: never let the field be empty so the
            # downstream UI + validation contract holds.
            band_prefix = {
                "CRITICAL": "Critical",
                "HIGH": "High priority",
                "MEDIUM": "Worth reviewing",
                "LOW": "Low priority",
            }.get(band, "Worth reviewing")
            summary = f"{band_prefix} — multiple signals point to ESG materiality for this article."
    # Fall back to a deterministic stakes sentence when the LLM stamp is
    # empty. The "For you · " paragraph on the article sheet is one of the
    # three things the reader actually scans; never leave it blank.
    if not stakes:
        stakes = _fallback_stakes(insight, exposure, band)

    return {
        "materiality_band": band,
        "materiality_weight": (
            round(float(materiality_weight), 3) if materiality_weight is not None else None
        ),
        "dominant_signal": dominant,
        "criticality_summary": summary[:280],
        "stakes_for_company": stakes,
        "financial_exposure": exposure,
        "warning": sasb_warning,  # Phase 3 sets "sasb_unmapped" when applicable
    }


def _fallback_stakes(insight: Any, exposure: dict[str, Any], band: str) -> str:
    """Deterministic one-sentence stakes paragraph used when the LLM
    didn't stamp `personal_stakes_paragraph`. Anchors on the polarity
    + financial exposure + band so it reads as actionable, not generic.
    """
    if insight is None:
        return ""
    polarity = (getattr(insight, "event_polarity", "") or "neutral").lower()
    amount = (exposure or {}).get("amount_cr")
    amount_str = ""
    if isinstance(amount, (int, float)) and amount > 0:
        if amount >= 100:
            amount_str = f"₹{amount:,.0f} Cr"
        else:
            amount_str = f"₹{amount:.1f} Cr"

    if polarity == "positive":
        if amount_str:
            return (
                f"This is a tailwind worth roughly {amount_str} for your company — "
                f"surface it in the next investor touchpoint and the board narrative."
            )
        return (
            "This is a tailwind for your company — surface it in the next "
            "investor touchpoint and the board narrative."
        )
    if polarity == "negative":
        if amount_str:
            return (
                f"This puts roughly {amount_str} at risk for your company — "
                f"plan a response narrative and brief the board within the next cycle."
            )
        return (
            "This carries downside risk for your company — plan a response "
            "narrative and brief the board within the next cycle."
        )
    # neutral / disclosure event
    if band in ("CRITICAL", "HIGH"):
        return (
            "This is a disclosure-grade event you'll need to address in the next "
            "reporting cycle — verify your framework mapping and assign an owner."
        )
    return (
        "Worth tracking for your watchlist — no immediate action required, "
        "but the underlying topic is material to your industry."
    )


# ---------------------------------------------------------------------------
# Bullet 3 — what_it_triggers
# ---------------------------------------------------------------------------


def _build_what_it_triggers(
    result: Any, recommendations: Any,
) -> dict[str, Any]:
    """Concrete obligations: top frameworks + top 3 recommended actions."""
    frameworks_out: list[dict[str, Any]] = []
    if result is not None:
        for fm in (getattr(result, "frameworks", []) or [])[:3]:
            entry: dict[str, Any] = {
                "code": getattr(fm, "framework_id", "") or getattr(fm, "code", "") or "",
                "section": "",
                "is_mandatory": bool(getattr(fm, "is_mandatory", False)),
            }
            # Triggered sections come as a list — pick the first as the headline.
            sections = (
                getattr(fm, "triggered_sections", None)
                or getattr(fm, "sections", None)
                or []
            )
            if isinstance(sections, list) and sections:
                first = sections[0]
                entry["section"] = (
                    first.get("code") if isinstance(first, dict)
                    else str(first)
                )[:48]
            deadline_days = getattr(fm, "deadline_days", None)
            if isinstance(deadline_days, (int, float)) and deadline_days > 0:
                entry["deadline_days"] = int(deadline_days)
            frameworks_out.append(entry)

    actions_out: list[dict[str, Any]] = []
    if recommendations is not None:
        recs = getattr(recommendations, "recommendations", None) or []
        for r in recs[:3]:
            raw_title = (getattr(r, "title", "") or "")[:140]
            deadline = (getattr(r, "deadline", "") or getattr(r, "timeline", "") or "")[:80]
            # Strip any "by YYYY-MM-DD" / "by FY2X" / "by Q3 2026" suffix
            # that the LLM tucked into the title — otherwise the frontend
            # renders the deadline twice ("Title by 2026-06-30 (by 2026-06-30)").
            cleaned_title = _strip_inline_deadline(raw_title)
            # Phase 35 — surface richer rec metadata so the email + UI can
            # show cost band + payback + ROI inline. The payload already
            # carried these fields from the recommendation engine; we just
            # weren't passing them through to the unified-analysis block.
            actions_out.append({
                "title": cleaned_title,
                "deadline": deadline,
                "owner": (getattr(r, "owner", "") or getattr(r, "responsible_party", "") or "")[:60],
                "budget": getattr(r, "estimated_budget", None) or getattr(r, "budget", None),
                "framework_section": (getattr(r, "framework_section", "") or "")[:48],
                "type": getattr(r, "type", "") or "",
                # Phase 35 additions:
                "payback_months": getattr(r, "payback_months", None),
                "roi_pct": getattr(r, "roi_percentage", None),
                "estimated_impact": (getattr(r, "estimated_impact", "") or "")[:24],
            })

    return {
        "frameworks": frameworks_out,
        "recommended_actions": actions_out,
    }


_INLINE_DEADLINE_PATTERN = None


def _strip_inline_deadline(title: str) -> str:
    """Remove a trailing 'by <deadline>' clause from a recommendation title.

    Catches the common LLM patterns: ISO dates, fiscal-year codes, quarter
    refs, month-name dates. Leaves the title untouched if nothing matches.
    """
    global _INLINE_DEADLINE_PATTERN
    if _INLINE_DEADLINE_PATTERN is None:
        import re
        _INLINE_DEADLINE_PATTERN = re.compile(
            r"\s+by\s+("
            r"\d{4}-\d{2}-\d{2}"                          # ISO date 2026-06-30
            r"|FY\s*\d{2,4}"                              # FY26 / FY2026
            r"|Q[1-4]\s*(?:FY)?\s*\d{2,4}"                # Q3 2026 / Q3 FY26
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,4}(?:[,\s]+\d{4})?"
            r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
            r"|end\s+of\s+\d{4}"                          # end of 2026
            r")\s*\.?\s*$",
            re.IGNORECASE,
        )
    if not title:
        return ""
    return _INLINE_DEADLINE_PATTERN.sub("", title).rstrip(" -—,;:.").strip()


# ---------------------------------------------------------------------------
# Bullet 4 — what_to_watch
# ---------------------------------------------------------------------------


def _build_what_to_watch(
    result: Any, insight: Any, benchmarks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Forward signal: trajectory + top risk categories + benchmarks."""
    trajectory_block: dict[str, Any] = {}
    if insight is not None:
        traj = getattr(insight, "sentiment_trajectory", None) or {}
        if isinstance(traj, dict):
            horizons = traj.get("horizons") or {}
            h3 = _horizon_direction(horizons.get("3m"))
            h6 = _horizon_direction(horizons.get("6m"))
            h12 = _horizon_direction(horizons.get("12m"))
            confidence = _horizon_confidence(horizons)
            # Only ship the block when at least one horizon has a real
            # direction. Empty-string fields downstream confuse the UI
            # ("flat" is a real signal; "" is missing data).
            if any([h3, h6, h12]):
                trajectory_block = {
                    "horizon_3m": h3,
                    "horizon_6m": h6,
                    "horizon_12m": h12,
                    "confidence": confidence,
                }

    # Top TEMPLES risk categories by score — but ONLY surface them when
    # the event is risk-flavoured. For positive events (contract wins,
    # rating upgrades, ESG certifications, partnerships) the TEMPLES
    # fallback returns generic Legal/Political/Social tags that have no
    # contextual relationship to the story — better to surface nothing
    # than to surface noise.
    top_risks: list[str] = []
    polarity = (
        getattr(insight, "event_polarity", "") or "neutral"
    ).lower() if insight is not None else "neutral"
    if polarity != "positive" and result is not None and getattr(result, "risk", None) is not None:
        risk_obj = result.risk
        temples = getattr(risk_obj, "temples_risks", None) or []
        try:
            sorted_temples = sorted(
                temples,
                key=lambda r: float(getattr(r, "score", 0) or 0),
                reverse=True,
            )
            # Filter to risks that actually scored above the noise floor
            # (probability × exposure > 0). The fallback Legal/Political/
            # Social trio is what slips through with score 0.
            top_risks = [
                (getattr(r, "category", "") or "")
                for r in sorted_temples[:3]
                if getattr(r, "category", None)
                and float(getattr(r, "score", 0) or 0) > 0
            ]
        except (TypeError, ValueError):
            top_risks = []

    # Lead indicators come from the ontology via risk_assessor; for now
    # surface the first 2 risk descriptions as proxies. Same polarity
    # gate as top_risks so we don't ship generic indicators on positive
    # events.
    lead_indicators: list[str] = []
    if polarity != "positive" and result is not None and getattr(result, "risk", None) is not None:
        temples = getattr(result.risk, "temples_risks", None) or []
        for r in temples[:2]:
            score_v = float(getattr(r, "score", 0) or 0)
            if score_v <= 0:
                continue
            desc = getattr(r, "description", "") or getattr(r, "lead_indicator", "")
            if desc:
                lead_indicators.append(str(desc)[:140])

    # Next decision window — should hold a label like "BRSR FY24 filing"
    # or "Board review" + a real by_date, NOT the financial exposure
    # headline (that lives on `why_it_matters.financial_exposure`).
    # When no real window is computable, leave the block empty so the
    # UI can hide it.
    next_window = _build_next_decision_window(insight)

    return {
        "sentiment_trajectory": trajectory_block,
        "top_risk_categories": top_risks,
        "lead_indicators": lead_indicators,
        # Phase 4 wires this up via engine.analysis.benchmarks.get_benchmarks_for_company.
        # Hidden in the UI when empty (DECISION 4.1).
        "benchmarks": benchmarks or [],
        "next_decision_window": next_window,
    }


def _build_next_decision_window(insight: Any) -> dict[str, Any]:
    """Pull a real decision window from the recommendations or framework
    deadlines. Never returns the financial-exposure headline.
    """
    if insight is None:
        return {}
    # Prefer the earliest-deadline recommendation as the "next decision".
    recs = getattr(insight, "rereact_recommendations", None) or []
    if not recs:
        # Some pipelines stamp it under `recommendations`.
        recs = getattr(insight, "recommendations", None) or []
    if isinstance(recs, dict):
        recs = recs.get("recommendations") or []
    if isinstance(recs, list) and recs:
        # Find the earliest dated rec.
        best_label = ""
        best_date = ""
        for r in recs:
            if not isinstance(r, dict):
                continue
            d = (r.get("deadline") or r.get("timeline") or "").strip()
            t = (r.get("title") or "").strip()
            if not t:
                continue
            if d and (not best_date or d < best_date):
                best_label = t[:120]
                best_date = d[:32]
            elif not best_label:
                best_label = t[:120]
        if best_label:
            return {"label": best_label, "by_date": best_date}
    return {}


def _horizon_direction(horizon: Any) -> str:
    if not isinstance(horizon, dict):
        return ""
    return str(horizon.get("direction") or "")[:20]


def _horizon_confidence(horizons: dict[str, Any]) -> str:
    """Aggregate confidence across the 3 horizons (pick the dominant level)."""
    if not isinstance(horizons, dict):
        return ""
    levels = []
    for k in ("3m", "6m", "12m"):
        h = horizons.get(k)
        if isinstance(h, dict):
            c = h.get("confidence")
            if c:
                levels.append(str(c).lower())
    if not levels:
        return ""
    # Most-common wins; ties broken by "high" > "medium" > "low".
    from collections import Counter
    counts = Counter(levels)
    return counts.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Methodology block — per-bullet "(i)" icon scopes
# ---------------------------------------------------------------------------


def _build_methodology_block(insight: Any) -> dict[str, dict[str, Any]]:
    """Per-bullet methodology entries.

    Phase 33 — calls the 4 dedicated per-bullet builders in
    ``methodology_provenance.py`` (``what_changed``, ``why_it_matters``,
    ``what_it_triggers``, ``what_to_watch``). Each builder reads the
    article's actual computed values and emits article-specific,
    plain-English explanation. Generic fallback content (the formula-heavy
    pre-Phase-33 strings) is gone — if a builder fails we surface a clear
    "methodology unavailable" stub so the UI doesn't render misleading
    generic boilerplate.
    """
    try:
        from engine.analysis.methodology_provenance import build_panel_methodology
        insight_dict = insight.to_dict() if insight is not None and hasattr(insight, "to_dict") else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis: methodology import failed (%s)", exc)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for bullet_id in ("what_changed", "why_it_matters", "what_it_triggers", "what_to_watch"):
        try:
            block = build_panel_methodology(insight_dict, bullet_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("unified_analysis: %s methodology failed (%s)", bullet_id, exc)
            block = None
        if block is None:
            block = {
                "metric": bullet_id,
                "source": "(methodology unavailable for this bullet)",
                "simple_logic": "Methodology unavailable for this bullet — please refresh the article.",
                "formula_human": "",
                "ontology_anchors": [],
                "your_inputs": {},
            }
        out[bullet_id] = block
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_unified_analysis(
    result: Any,
    insight: Any,
    *,
    recommendations: Any = None,
    sasb_warning: str | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose the unified 4-bullet analysis block.

    Args:
        result: ``engine.analysis.pipeline.PipelineResult`` — Stages 1–9 outputs.
        insight: ``engine.analysis.insight_generator.DeepInsight`` — Stage 10
            output. May be ``None`` for SECONDARY-tier articles that didn't
            run Stage 10.
        recommendations: ``engine.analysis.recommendation_engine.RecommendationResult``.
        sasb_warning: Phase 3 — pass ``"sasb_unmapped"`` when the company's
            sasb_category isn't in the materiality TTL.
        benchmarks: Phase 4 — list of ``{source, metric, value, as_of}`` dicts
            from ``company_benchmarks``. Hidden in the UI when empty.

    Returns:
        Dict with 5 top-level keys: ``what_changed``, ``why_it_matters``,
        ``what_it_triggers``, ``what_to_watch``, ``methodology``.

    Never raises — partial inputs produce partial output (empty strings /
    empty lists) rather than blocking the write path.
    """
    try:
        what_changed = _build_what_changed(result, insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_changed failed (%s)", exc)
        what_changed = {}

    try:
        why_it_matters = _build_why_it_matters(result, insight, sasb_warning)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.why_it_matters failed (%s)", exc)
        why_it_matters = {}

    try:
        what_it_triggers = _build_what_it_triggers(result, recommendations)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_it_triggers failed (%s)", exc)
        what_it_triggers = {}

    try:
        what_to_watch = _build_what_to_watch(result, insight, benchmarks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_to_watch failed (%s)", exc)
        what_to_watch = {}

    try:
        methodology = _build_methodology_block(insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.methodology failed (%s)", exc)
        methodology = {}

    # Surface the headline-only flag on the analysis block so the
    # frontend can render a transparency cue without needing to walk
    # the full insight payload. The pipeline stamps `headline_only=True`
    # in insight_generator when article body < 300 chars (publisher
    # paywall / scraper 403 / JS-rendered SPA). When set, every
    # ₹ figure + specific recommendation in the analysis is engine
    # extrapolation — not article-grounded.
    headline_only = bool(getattr(insight, "headline_only", False)) if insight is not None else False
    body_char_count = int(getattr(insight, "body_char_count", 0) or 0) if insight is not None else 0

    return {
        "what_changed": what_changed,
        "why_it_matters": why_it_matters,
        "what_it_triggers": what_it_triggers,
        "what_to_watch": what_to_watch,
        "methodology": methodology,
        "headline_only": headline_only,
        "body_char_count": body_char_count,
    }


# ---------------------------------------------------------------------------
# POW-2 — Industry-shared vs per-company split.
# ---------------------------------------------------------------------------


def split_analysis(unified: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split the unified 4-bullet payload into industry-shared + per-company.

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.1 — "Where each piece lands".

      - ``what_changed`` (event facts, frameworks, polarity, source) is
        industry-shared. Every reader on a story gets the same block.
      - ``why_it_matters`` (criticality_summary, stakes_for_company,
        financial_exposure, dominant_signal) is per-company.
      - ``what_it_triggers`` (recommended_actions, framework citations
        triggered for the company) is per-company.
      - ``what_to_watch`` (sentiment_trajectory, top_risk_categories,
        next_decision_window, benchmarks) is per-company.
      - ``methodology`` is split keyed-by-bullet so the "i" icon drawer
        on each bullet still sees its own block.

    Returns:
        ``(shared, personalised)`` — two dicts that combined re-form the
        original ``unified`` payload (minus any keys not in the schema).
    """
    if not isinstance(unified, dict):
        return {}, {}

    methodology = unified.get("methodology") or {}

    shared: dict[str, Any] = {
        "what_changed": unified.get("what_changed") or {},
        "methodology": {
            "what_changed": methodology.get("what_changed") or {},
        },
    }

    personalised: dict[str, Any] = {
        "why_it_matters": unified.get("why_it_matters") or {},
        "what_it_triggers": unified.get("what_it_triggers") or {},
        "what_to_watch": unified.get("what_to_watch") or {},
        # Phase 47.P — `lede` (Phase 39 editorial opener) is article-
        # specific and per-company because the LLM grounds it in the
        # caller's persona + painpoints. It belongs in the personalised
        # half so the frontend's shared+personalised merge picks it up.
        # Prior to this fix, lede was dropped on the floor at split
        # time even though the writer stamped it on disk — `/now/feed`
        # then served decks with no lede text.
        "lede": unified.get("lede") or {},
        "methodology": {
            "why_it_matters": methodology.get("why_it_matters") or {},
            "what_it_triggers": methodology.get("what_it_triggers") or {},
            "what_to_watch": methodology.get("what_to_watch") or {},
        },
    }

    return shared, personalised
