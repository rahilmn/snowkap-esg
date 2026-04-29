"""REREACT 3-agent recommendation chain.

1. Materiality gate — NON-MATERIAL/LOW + IGNORE/MONITOR → empty list
   (honours the "do nothing is valid ESG output" rule).
2. Generator — 3-5 recommendations via OpenAI gpt-4.1-mini.
3. Analyst — validates each recommendation for logical consistency.
4. Validator — independent hallucination check.
5. Post-processing — deadline shift, ROI sanity, priority, risk-of-inaction.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.pipeline import PipelineResult
from engine.config import Company, get_openai_api_key, load_settings

logger = logging.getLogger(__name__)


@dataclass
class Recommendation:
    title: str
    description: str
    type: str  # strategic | financial | esg_positioning | operational | compliance
    responsible_party: str
    framework_section: str
    deadline: str  # ISO date
    estimated_budget: str
    profitability_link: str
    priority: str  # CRITICAL | HIGH | MEDIUM | LOW
    urgency: str  # immediate | short_term | medium_term | long_term
    estimated_impact: str  # High | Medium | Low
    risk_of_inaction: int  # 1-10
    roi_percentage: float | None = None
    payback_months: float | None = None
    peer_benchmark: str | None = None
    # Phase 3: surfaces ROI cap hit so UI can render tooltip
    roi_capped: bool = False
    roi_cap_reason: str = ""
    # Phase 13 S1: per-recommendation audit trail. Each entry maps a
    # claim in the recommendation back to its source in the ontology /
    # primitive cascade / article body so a CFO can ask "why this rec?"
    # and get a concrete answer. Items have shape
    #   {"source": "ontology|article|primitive|peer|precedent|benchmark",
    #    "ref": "BRSR:P6:Q14" | "P2::SC→OX" | etc.,
    #    "value": "human-readable evidence string"}
    audit_trail: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationResult:
    recommendations: list[Recommendation]
    do_nothing: bool
    gate_reason: str
    generator_count: int
    validated_count: int
    priority_matrix: dict[str, list[dict[str, Any]]] | None = None
    recommendation_rankings: dict[str, list[int]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendations": [r.to_dict() for r in self.recommendations],
            "do_nothing": self.do_nothing,
            "gate_reason": self.gate_reason,
            "generator_count": self.generator_count,
            "validated_count": self.validated_count,
            "priority_matrix": self.priority_matrix,
            "recommendation_rankings": self.recommendation_rankings,
        }


# ---------------------------------------------------------------------------
# Materiality gate
# ---------------------------------------------------------------------------


def _should_skip(insight: DeepInsight, result: PipelineResult) -> tuple[bool, str]:
    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "")).upper()
    action = str(decision.get("action", "")).upper()

    if materiality in ("NON-MATERIAL", "NONMATERIAL") and action == "IGNORE":
        return True, "Non-material + ignore — no action required"
    if insight.impact_score <= 1.5 and action == "IGNORE":
        return True, f"Very low impact score {insight.impact_score} + ignore"
    return False, ""


def _get_rec_count(insight: DeepInsight) -> int:
    """Return how many recommendations to generate based on materiality AND impact score.

    Uses the HIGHER of materiality-based and score-based count to prevent
    the LLM underrating materiality from suppressing recommendations.
    """
    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "")).upper()

    # Materiality-based count
    if materiality in ("CRITICAL", "HIGH"):
        mat_count = 5
    elif materiality == "MODERATE":
        mat_count = 4
    else:
        mat_count = 2

    # Impact-score-based count (override if LLM underrates materiality)
    score = insight.impact_score or 0
    if score >= 7:
        score_count = 5
    elif score >= 5:
        score_count = 4
    elif score >= 3:
        score_count = 3
    else:
        score_count = 2

    return max(mat_count, score_count)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


_GENERATOR_SYSTEM = """You are an ESG action generator. Produce %%REC_COUNT%% actionable, company-specific recommendations grounded in the pipeline context provided.

RULES:
- Every recommendation MUST reference a specific framework section (e.g. BRSR:P6:Q14, GRI:305-1).
- Every profitability_link MUST include a ₹ amount or % quantification. NEVER write vague text like "potential savings". Write "₹15-30 Cr annual compliance cost avoided" or "₹200 Cr green bond opportunity at 50bps discount".
- Deadlines must be future dates in YYYY-MM-DD format.
- Budgets must be calibrated to company market cap and the specific event. A ₹50 Cr GST demand requires ₹2-5 Cr budget for legal challenge, not ₹50 Lakh.
- No generic advice like "improve ESG practices" or "enhance disclosure". Every recommendation must name the SPECIFIC action: "File GST appellate tribunal appeal within 30 days citing ABC precedent" or "Commission third-party BRSR assurance for FY26 filing".
- title must be a SPECIFIC action verb phrase: "File GST appeal at CESTAT" not "Address regulatory compliance".
- description must name ₹ amounts, specific frameworks, specific deadlines, and specific responsible parties.
- roi_percentage: estimate conservatively. For compliance, ROI = avoided penalty / implementation cost. For ESG positioning, ROI = valuation premium / cost. NEVER use null — always estimate. IMPORTANT: ROI values are capped at 500% (compliance), 400% (strategic/ESG), 300% (financial), 200% (operational). Do NOT cite higher ROI in the description or profitability_link text — use capped values.
- payback_months: for capex, use industry standard payback periods. For compliance, use regulatory deadline as outer bound. NEVER use null.
- If PEER ACTIONS are provided, reference what competitors did and suggest matching or exceeding their approach.
- For LOW materiality articles: focus on monitoring actions and disclosure improvements, but still be SPECIFIC about what to monitor and how.
- ALWAYS include at least 1 MONITORING recommendation with specific threshold triggers: "Monitor X metric; escalate to ACT if Y exceeds Z threshold within N months".
- If CAUSAL PRIMITIVES context provides threshold categories (τ values), include them in monitoring recommendations.

FRAMEWORK ACCURACY:
- Match framework sections to the event type. Tax events → GRI:207, ESRS G1. Climate → GRI:305, ESRS E1. H&S → GRI:403, ESRS S1.
- NEVER cite ESRS E1 for a non-climate event. NEVER cite GRI:305 for a tax/governance event.

PERSPECTIVE-AWARE RECOMMENDATIONS:
- CFO-relevant: focus on ₹ exposure quantification, cost avoidance, margin protection, ROI maximization.
- CEO-relevant: focus on strategic positioning, competitive advantage, board-level decisions.
- ESG Analyst-relevant: focus on framework compliance gaps, disclosure deadlines, stakeholder engagement.
- Include a mix of types so each perspective has relevant recommendations.
- CRITICAL: Every recommendation must be SPECIFIC to THIS article's event. Do NOT generate generic ESG recommendations (like "enhance CDP disclosure" or "file BRSR") unless the article specifically triggers those frameworks. For positive events (analyst upgrades, ESG awards), recommend LEVERAGING the momentum (green bond timing, investor communication, competitive positioning). For negative events (penalties, violations), recommend REMEDIATION and PREVENTION.

Return a JSON object:
{
  "recommendations": [
    {
      "title": "<action title, max 10 words>",
      "type": "<strategic|financial|esg_positioning|operational|compliance>",
      "description": "<1-2 sentences, specific to the company>",
      "responsible_party": "<specific role>",
      "framework_section": "<BRSR:P6, GRI:305-1, etc.>",
      "deadline": "<YYYY-MM-DD>",
      "estimated_budget": "<₹X-Y Cr>",
      "profitability_link": "<how this saves/makes money with numbers>",
      "urgency": "<immediate|short_term|medium_term|long_term>",
      "estimated_impact": "<High|Medium|Low>",
      "roi_percentage": <estimated ROI % over 3 years, or null>,
      "payback_months": <months to break even, or null>,
      "peer_benchmark": "<what competitors did in similar situations, or null>",
      "audit_trail": [
        {"source": "ontology|article|primitive|peer|precedent|benchmark",
         "ref": "<framework section, primitive edge id, peer name, etc.>",
         "value": "<the specific evidence anchoring the recommendation, in 1 line>"}
      ]
    }
  ]
}

CRITICAL: every recommendation MUST include audit_trail with 1-3 entries
linking the rec back to: (a) framework citations from the FRAMEWORKS block,
(b) ₹ figures from the article or primitive cascade, or (c) named precedents
from the PRECEDENTS block. A recommendation without traceable evidence is
unverifiable; the verifier will flag it.

Return ONLY the JSON, no preamble."""


# Phase 14.3 — Dedicated POSITIVE-event generator system prompt.
#
# Background: even with the Phase 13 archetype routing's polarity warning,
# the default `_GENERATOR_SYSTEM` prompt above is implicitly oriented
# toward defensive remediation framing (see "REMEDIATION and PREVENTION"
# language). On positive events (contract wins, capacity adds, ESG cert
# upgrades, green-finance milestones) the LLM consistently injected
# fictional "₹10-50 Cr SEBI penalty" risks even though the article had
# no regulatory failure to remediate.
#
# This prompt rewrites the rules with positive-event semantics:
#   - "leverage the upside" replaces "remediate risk"
#   - "investor-comms / capital deployment / pipeline momentum" replaces
#     "compliance / monitoring / assurance"
#   - explicit ban on inventing penalty risks unless the article describes
#     a concrete regulatory action
#
# Dispatcher (in _generate_recommendations) routes to this prompt when
# `is_positive_event(event_id)` returns True.
_POSITIVE_GENERATOR_SYSTEM = """You are an ESG action generator. The article describes a POSITIVE event for the company (contract win, capacity addition, ESG certification, green-finance milestone, etc). Produce %%REC_COUNT%% actionable, company-specific recommendations that LEVERAGE the upside.

RULES:
- Recommendations must extract value from the event: investor communication, capacity scaling, capital deployment, pipeline momentum, premium pricing, framework-tier advancement.
- Every recommendation MUST reference a specific framework section (e.g. BRSR:P6:Q14, EU Taxonomy Article 8, GRI:305-1) — used for transparency / disclosure leverage, NOT compliance remediation.
- Every profitability_link MUST include a ₹ amount or % quantification. Frame as upside: "₹500 Cr green bond at 50 bps coupon save" or "₹200 Cr revenue uplift FY26 once commissioning ramps".
- Deadlines must be future dates in YYYY-MM-DD format.
- Budgets must be calibrated to company market cap and the specific event. Investor-comms ₹0.5-1 Cr; framework-tier advancement ₹1-3 Cr; capacity scaling ₹50-500 Cr depending on the event.
- title must be a SPECIFIC action verb phrase: "Issue ₹500 Cr Green Bond by Sep 2026" not "Pursue green finance".
- roi_percentage: estimate the upside capture. For investor comms, ROI = valuation premium / cost. For capital deployment, ROI = revenue uplift / capex. ROI caps: 500% (compliance — rarely applies here), 400% (strategic/ESG), 300% (financial), 200% (operational).
- payback_months: for capex use industry-standard payback. For investor comms / framework advancement, 3-12 mo. NEVER use null.

CRITICAL — POSITIVE-EVENT POLARITY GUARDRAILS:
- DO NOT recommend "engage SEBI / engage regulator" UNLESS the article explicitly mentions a regulatory action against the company.
- DO NOT cite "₹X-Y Cr SEBI penalty per violation" or "regulatory enforcement risk" — there is no enforcement event in this article.
- DO NOT recommend "third-party BRSR assurance" as a defensive measure — only recommend it as a credibility-amplification step IF the company is announcing certification.
- DO NOT recommend "monitor and escalate if X exceeds Y" as a generic risk-monitor on a positive event. If you include monitoring, frame it as KPI tracking for the new asset/contract/certification.
- The "key_risk" framing belongs in NEGATIVE-event prompts, not here. Frame this as opportunity capture.

GOOD POSITIVE-EVENT REC SHAPES (pick from these archetypes for ≥80% of the rec set):
  • Investor communication — IR roadshow, earnings-call narrative refresh, ESG-fund pitch deck update
  • Capacity / order ramp — utilization plan, supply-chain readiness, workforce mobilisation
  • Capital deployment — green bond / SLL issuance timing, refinance optionality
  • Framework advancement — DJSI inclusion, MSCI ESG upgrade pathway, CDP A-list pursuit
  • Premium-pricing capture — ESG / quality differentiation in B2B procurement positioning
  • Co-marketing — case-study publication, partnership amplification

Return a JSON object with the same schema as the negative-event prompt:
{
  "recommendations": [
    {
      "title": "<action title, max 10 words>",
      "type": "<strategic|financial|esg_positioning|operational|compliance>",
      "description": "<1-2 sentences, specific to the company>",
      "responsible_party": "<specific role>",
      "framework_section": "<BRSR:P6, EU Taxonomy Art 8, GRI:305-1, etc.>",
      "deadline": "<YYYY-MM-DD>",
      "estimated_budget": "<₹X-Y Cr>",
      "profitability_link": "<upside quantified with ₹ or % numbers>",
      "urgency": "<immediate|short_term|medium_term|long_term>",
      "estimated_impact": "<High|Medium|Low>",
      "roi_percentage": <estimated ROI % over 3 years>,
      "payback_months": <months to capture the upside>,
      "peer_benchmark": "<comparable competitor move, or null>",
      "audit_trail": [
        {"source": "ontology|article|primitive|peer|precedent|benchmark",
         "ref": "<framework section, primitive edge id, peer name, etc.>",
         "value": "<the specific evidence anchoring the recommendation>"}
      ]
    }
  ]
}

Every recommendation MUST include audit_trail with 1-3 entries linking back
to ontology / article / primitive / precedent / peer / benchmark sources.

Return ONLY the JSON, no preamble."""


def _build_generator_prompt(
    insight: DeepInsight, result: PipelineResult, company: Company
) -> str:
    lines: list[str] = []
    lines.append(f"COMPANY: {company.name} ({company.industry}, {company.market_cap})")
    lines.append(f"ARTICLE: {result.title}")
    lines.append(f"HEADLINE: {insight.headline}")
    lines.append(f"IMPACT SCORE: {insight.impact_score}")
    lines.append(f"MATERIALITY: {insight.decision_summary.get('materiality', '')}")
    lines.append(f"VERDICT: {insight.decision_summary.get('verdict', '')}")
    lines.append(f"KEY RISK: {insight.decision_summary.get('key_risk', '')}")
    lines.append(f"TOP OPPORTUNITY: {insight.decision_summary.get('top_opportunity', '')}")
    if result.frameworks:
        lines.append("FRAMEWORKS:")
        for fm in result.frameworks[:5]:
            tag = " [MANDATORY]" if fm.is_mandatory else ""
            lines.append(f"  - {fm.framework_label}{tag}: {fm.profitability_link[:120]}")
    if result.risk and result.risk.top_risks:
        lines.append("TOP RISKS:")
        for r in result.risk.top_risks[:3]:
            lines.append(f"  - {r.category} ({r.level})")

    # Phase 14: Peer actions for benchmarked recommendations
    primary_theme = result.themes.primary_theme if result.themes else ""
    if primary_theme:
        try:
            from engine.ontology.intelligence import query_peer_actions
            peer_actions = query_peer_actions(primary_theme)
            if peer_actions:
                lines.append("PEER ACTIONS (what competitors did):")
                for pa in peer_actions[:3]:
                    lines.append(f"  - {pa.company}: {pa.action} → {pa.outcome}")
        except Exception:
            pass

    # Phase 3: Real-world precedents — authored library, LLM cites by reference
    try:
        from engine.ontology.intelligence import query_precedents_for_event
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            precedents = query_precedents_for_event(event_id, company.industry, limit=3)
            if precedents:
                lines.append("NAMED PRECEDENTS (cite these by company + year + ₹ cost; do NOT invent new precedents):")
                for p in precedents:
                    lines.append(f"  - {p.as_citation()}")
                    if p.recovery_path:
                        lines.append(f"    Recovery: {p.recovery_path[:180]}")
    except Exception:
        pass

    # Phase 14: ROI benchmarks
    try:
        from engine.ontology.intelligence import query_industry_roi_benchmarks
        benchmark = query_industry_roi_benchmarks(company.industry)
        if benchmark:
            lines.append(f"ROI BENCHMARK for {company.industry}: typical ROI {benchmark.typical_roi}, payback {benchmark.typical_payback}")
    except Exception:
        pass

    # Phase 14: Compliance deadlines
    try:
        from engine.ontology.intelligence import query_compliance_deadlines
        deadlines = query_compliance_deadlines("India")
        if deadlines:
            lines.append("REGULATORY DEADLINES:")
            for d in deadlines[:3]:
                lines.append(f"  - {d.label}: {d.deadline_date} ({d.framework})")
    except Exception:
        pass

    # Phase 17: Causal Primitives context for quantitative recommendation grounding
    try:
        from engine.ontology.intelligence import query_cascade_context, query_thresholds_for_primitive, query_primitives_for_event
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            prims = query_primitives_for_event(event_id)
            if prims:
                primary = prims[0]
                lines.append(f"CAUSAL PRIMITIVES FOR RECOMMENDATIONS:")
                lines.append(f"  Primary affected: {primary.label} ({primary.slug})")
                # Get thresholds for monitoring recommendations
                thresholds = query_thresholds_for_primitive(primary.slug)
                if thresholds:
                    lines.append("  Threshold monitors (recommend tracking these):")
                    for t in thresholds[:3]:
                        lines.append(f"    - {t['label']}: τ = {t['range']} ({t['unit']})")
                lines.append("  Actionable levers: reduce β (efficiency investment), hedge exposure, diversify inputs")
    except Exception:
        pass

    # Phase 13 B1 — event-archetype routing. Inject event-appropriate
    # recommendation categories so the LLM picks levers that fit the event
    # type instead of defaulting to a one-size template (file BRSR + monitor
    # + assurance + capex). Live verified on the Waaree contract-win article
    # (2026-04-24): pre-fix produced 5 disclosure-shaped recs for a positive
    # business event; post-fix picks operational-readiness + investor-comms +
    # pipeline-momentum archetypes appropriate to a contract win.
    try:
        from engine.analysis.recommendation_archetypes import (
            get_archetypes_for_event,
            is_positive_event,
        )
        event_id_for_arch = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        # Phase 17 — pass sentiment so ambiguous events (quarterly_results etc.)
        # route by tone rather than defaulting to negative-event archetypes.
        nlp_sent_arch = getattr(result.nlp, "sentiment", 0) if result.nlp else 0
        archetypes = get_archetypes_for_event(event_id_for_arch)
        # Phase 17 — fallback: if no event-specific archetypes (event_id empty
        # or unmapped), pick archetypes from the primary theme so the LLM
        # doesn't fall through to the generic 5-rec disclosure template.
        if not archetypes:
            from engine.analysis.recommendation_archetypes import get_archetypes_for_theme
            primary_theme = (
                result.themes.primary_theme if result.themes and hasattr(result.themes, "primary_theme") else ""
            )
            archetypes = get_archetypes_for_theme(primary_theme)
        if archetypes:
            lines.append("")
            lines.append(
                f"EVENT-SPECIFIC GUIDANCE (event={event_id_for_arch}). "
                f"Pick {min(len(archetypes), 5)} of these archetypes — pick distinct ones, "
                f"not five variants of a single category. Do NOT default to "
                f"'file BRSR + monitor compliance + third-party assurance' "
                f"unless the article explicitly describes a regulatory or "
                f"disclosure failure."
            )
            for label, desc in archetypes:
                lines.append(f"  • {label} — {desc}")
            if is_positive_event(event_id_for_arch, sentiment=nlp_sent_arch):
                lines.append("")
                lines.append(
                    "POLARITY: this is a POSITIVE event. Recommendations should "
                    "leverage the upside (growth / pricing / signal value), not "
                    "remediate a fabricated crisis. Avoid 'engage regulator', "
                    "'remediate violation', and 'governance review' framing "
                    "unless the article itself raises that concern."
                )
    except Exception:
        # Archetype routing is additive; never block recommendation generation.
        pass

    lines.append("")
    rec_count = _get_rec_count(insight)
    lines.append(f"Generate exactly {rec_count} actionable recommendations for this company. Today's date is 2026-04-13.")
    return "\n".join(lines)


def _repair_truncated_json(raw: str) -> dict:
    """Phase 13 hotfix — salvage a partially-truncated LLM JSON response.

    When the LLM hits max_tokens mid-array, the response looks like:
        {"recommendations": [{"title": "...", ...}, {"title": "...", "ty
    Classic JSON parsers reject this with `Unterminated string starting at`.
    This helper finds the last fully-closed object inside the recommendations
    array and returns `{"recommendations": [<those complete objects>]}` so
    the pipeline keeps the salvageable recs instead of returning zero.

    Returns `{"recommendations": []}` if no complete object can be salvaged.
    """
    if not raw or "recommendations" not in raw:
        return {"recommendations": []}
    # Find the start of the recommendations array
    start_idx = raw.find('"recommendations"')
    if start_idx < 0:
        return {"recommendations": []}
    bracket_idx = raw.find("[", start_idx)
    if bracket_idx < 0:
        return {"recommendations": []}

    # Walk forward inside the array, tracking brace nesting + string state.
    # When depth returns to 1 (top of array, between objects), record the
    # last successful close. Salvage by truncating at that close + a "]}".
    depth = 0
    in_string = False
    escape = False
    last_complete_close = -1
    for i in range(bracket_idx, len(raw)):
        c = raw[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                last_complete_close = i  # complete object inside array
    if last_complete_close < 0:
        return {"recommendations": []}
    # Build a salvageable string: everything up through the last complete
    # close, then close the array + outer object.
    salvaged = raw[: last_complete_close + 1] + "]}"
    try:
        return json.loads(salvaged)
    except json.JSONDecodeError:
        return {"recommendations": []}


def _generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company, client: OpenAI
) -> list[Recommendation]:
    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    # Phase 13 hotfix — bump token budget from 1500 → 3000 because the new
    # `audit_trail` field (S1) adds ~150-300 tokens per rec. The 1500-cap
    # was being hit mid-JSON, producing JSONDecodeError and empty rec
    # lists for HIGH-materiality articles (caught by the 2026-04-27 fuzz
    # run at 70% pass rate vs 90% baseline).
    max_tokens = llm_cfg.get("max_tokens_recommendation", 3000)

    # Phase 14.3 — dispatch to a dedicated positive-event prompt when the
    # event_id is in our POSITIVE_EVENTS set. Eliminates the "₹10-50 Cr SEBI
    # penalty" defensive injection on contract-win / certification articles.
    raw_content = ""
    try:
        from engine.analysis.recommendation_archetypes import is_positive_event
        event_id_for_dispatch = (
            result.event.event_id
            if result.event and hasattr(result.event, "event_id")
            else ""
        )
        # Phase 17 — sentiment-aware routing for ambiguous events
        # (event_quarterly_results +1 sentiment → positive prompt path).
        nlp_sent_disp = getattr(result.nlp, "sentiment", 0) if result.nlp else 0
        system_prompt_template = (
            _POSITIVE_GENERATOR_SYSTEM
            if is_positive_event(event_id_for_dispatch, sentiment=nlp_sent_disp)
            else _GENERATOR_SYSTEM
        )
    except Exception:
        # Failsafe: archetype routing is additive, never block generation
        system_prompt_template = _GENERATOR_SYSTEM
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt_template.replace("%%REC_COUNT%%", str(_get_rec_count(insight)))},
                {
                    "role": "user",
                    "content": _build_generator_prompt(insight, result, company),
                },
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw_content = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)
    except (APIError, APITimeoutError, IndexError) as exc:
        logger.warning("recommendation generator failed (api): %s", type(exc).__name__)
        return []
    except json.JSONDecodeError as exc:
        # Phase 13 hotfix — defensive JSON repair on token-truncated outputs.
        # If the response was cut mid-list, salvage whatever complete recs
        # we got rather than returning zero. The Phase 13 audit_trail field
        # makes truncation more likely until token caps stabilise.
        parsed = _repair_truncated_json(raw_content)
        if not parsed.get("recommendations"):
            logger.warning(
                "recommendation generator failed (json decode at line %d col %d) "
                "and repair yielded no recs",
                exc.lineno, exc.colno,
            )
            return []
        logger.warning(
            "recommendation generator: salvaged %d rec(s) from truncated JSON "
            "(error at line %d col %d)",
            len(parsed.get("recommendations") or []), exc.lineno, exc.colno,
        )

    raw_recs = parsed.get("recommendations", []) or []
    recommendations: list[Recommendation] = []
    for r in raw_recs:
        try:
            # Phase 14: Parse roi_percentage, payback_months, peer_benchmark
            _roi = r.get("roi_percentage")
            roi = float(_roi) if _roi and _roi != "null" else None
            _payback = r.get("payback_months")
            payback = float(_payback) if _payback and _payback != "null" else None
            _peer = r.get("peer_benchmark")
            peer = str(_peer)[:300] if _peer and _peer != "null" else None

            # Phase 17c: Clamp ROI to reasonable bounds per recommendation type
            rec_type = str(r.get("type", "operational") or "operational")
            roi_caps = {
                "compliance": 500.0,     # Max: avoid ₹50 Cr fine with ₹2 Cr = 2400%, cap at 500%
                "financial": 300.0,      # Max: cost of capital reduction, hedging
                "strategic": 400.0,      # Max: market positioning, green bond access
                "esg_positioning": 400.0,
                "operational": 200.0,    # Max: monitoring, process improvement
            }
            max_roi = roi_caps.get(rec_type, 300.0)
            roi_was_capped = False
            roi_cap_reason = ""
            if roi is not None and roi > max_roi:
                logger.info("ROI clamped: %s from %.0f%% to %.0f%%", r.get("title", "")[:40], roi, max_roi)
                roi_was_capped = True
                roi_cap_reason = (
                    f"Capped at {max_roi:.0f}% ({rec_type} ceiling). "
                    f"Raw estimate was {roi:.0f}%; cap prevents over-claim."
                )
                roi = max_roi

            # Phase 13 S1 — pull audit_trail from LLM JSON. Defensive parse:
            # accept the field whether it's a list of dicts (canonical),
            # a single dict (LLM occasionally produces that shape), or
            # missing (older prompts / partial LLM output).
            raw_trail = r.get("audit_trail")
            audit_trail: list[dict[str, str]] = []
            if isinstance(raw_trail, list):
                for entry in raw_trail[:5]:
                    if isinstance(entry, dict):
                        audit_trail.append({
                            "source": str(entry.get("source", "") or "")[:30],
                            "ref": str(entry.get("ref", "") or "")[:120],
                            "value": str(entry.get("value", "") or "")[:300],
                        })
            elif isinstance(raw_trail, dict):
                audit_trail.append({
                    "source": str(raw_trail.get("source", "") or "")[:30],
                    "ref": str(raw_trail.get("ref", "") or "")[:120],
                    "value": str(raw_trail.get("value", "") or "")[:300],
                })

            recommendations.append(
                Recommendation(
                    title=str(r.get("title", "") or "")[:200],
                    description=str(r.get("description", "") or "")[:500],
                    type=rec_type,
                    responsible_party=str(r.get("responsible_party", "") or ""),
                    framework_section=str(r.get("framework_section", "") or ""),
                    deadline=str(r.get("deadline", "") or ""),
                    estimated_budget=str(r.get("estimated_budget", "") or ""),
                    profitability_link=str(r.get("profitability_link", "") or "")[:500],
                    priority="MEDIUM",
                    urgency=str(r.get("urgency", "medium_term") or "medium_term"),
                    estimated_impact=str(r.get("estimated_impact", "Medium") or "Medium"),
                    risk_of_inaction=0,
                    roi_percentage=roi,
                    payback_months=payback,
                    peer_benchmark=peer,
                    roi_capped=roi_was_capped,
                    roi_cap_reason=roi_cap_reason,
                    audit_trail=audit_trail,  # Phase 13 S1
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Malformed recommendation skipped: %s", exc)
    return recommendations


# ---------------------------------------------------------------------------
# Post-processing (Analyst + Validator + quant math)
# ---------------------------------------------------------------------------


def _fix_deadline(deadline: str) -> str:
    today = date.today()
    if not deadline:
        return (today + timedelta(days=90)).isoformat()
    try:
        d = date.fromisoformat(deadline)
    except ValueError:
        return (today + timedelta(days=90)).isoformat()
    if d < today:
        # shift to next year same month/day
        try:
            return d.replace(year=today.year + 1).isoformat()
        except ValueError:
            return (today + timedelta(days=365)).isoformat()
    return d.isoformat()


def _derive_priority(rec: Recommendation) -> str:
    """Derive priority from ontology-sourced rules (urgency × impact matrix)."""
    from engine.ontology.intelligence import query_priority_rules

    rules = query_priority_rules()
    for rule in rules:
        if rule.urgency == rec.urgency and rule.impact == rec.estimated_impact:
            return rule.priority
    # Fallback: if no exact match, try urgency-only match
    for rule in rules:
        if rule.urgency == rec.urgency:
            return rule.priority
    return "MEDIUM"


def _compute_risk_of_inaction(rec: Recommendation) -> int:
    """Compute risk-of-inaction score using ontology-sourced config."""
    from engine.ontology.intelligence import query_risk_of_inaction_config

    config = query_risk_of_inaction_config()
    base = config.base_scores.get(rec.priority, 3)
    base += config.type_bonuses.get(rec.type, 0)
    lowered = rec.profitability_link.lower()
    if any(k in lowered for k in config.escalation_keywords):
        base += 1
    return max(1, min(10, base))


def _post_process(recs: list[Recommendation]) -> list[Recommendation]:
    for rec in recs:
        rec.deadline = _fix_deadline(rec.deadline)
        rec.priority = _derive_priority(rec)
        rec.risk_of_inaction = _compute_risk_of_inaction(rec)
    # Sort by priority + risk of inaction
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    recs.sort(key=lambda r: (priority_order.get(r.priority, 4), -r.risk_of_inaction))
    return recs


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _build_monitoring_recommendation(
    insight: DeepInsight, result: PipelineResult, company: Company, reason: str
) -> Recommendation:
    """Emit a single monitoring rec for SECONDARY / non-material articles.

    Phase 3: removes the silent-drop that left CFOs wondering if the system
    crashed. A do-nothing article still gets a tracked recommendation — "what
    to monitor and when to escalate" — so the UI never shows an empty panel.
    """
    theme = (result.themes.primary_theme if result.themes else "") or "general ESG signal"
    horizon_days = 30  # default review cadence
    trigger = "materiality escalation (event severity ↑ or ₹ figure > ₹10 Cr)"

    # Pull a threshold from the causal primitives ontology if available.
    try:
        from engine.ontology.intelligence import query_primitives_for_event, query_thresholds_for_primitive
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            prims = query_primitives_for_event(event_id)
            if prims:
                thr = query_thresholds_for_primitive(prims[0].slug)
                if thr:
                    trigger = f"{thr[0]['label']}: τ = {thr[0]['range']} ({thr[0]['unit']})"
    except Exception:  # noqa: BLE001 — best-effort threshold
        pass

    from datetime import datetime, timedelta, timezone

    deadline = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).date().isoformat()

    return Recommendation(
        title=f"Monitor — {theme} (no action required yet)",
        description=(
            f"Track this signal for materiality drift. Reason for monitor-only "
            f"stance: {reason}. Escalation trigger: {trigger}. "
            f"If crossed, run full analysis and revisit within 14 days."
        ),
        type="operational",
        responsible_party="ESG / Risk team",
        framework_section="BRSR:P1:Q1 (stakeholder review cycle)",
        deadline=deadline,
        estimated_budget="₹0 Cr (internal monitoring)",
        profitability_link=(
            "Prevents materiality drift surprise — no active cost, only watch-list inclusion. "
            "Cost-of-inaction: learning-lag if signal escalates without observation."
        ),
        priority="LOW",
        urgency="short_term",
        estimated_impact="Low",
        risk_of_inaction=3,
        roi_percentage=None,
        payback_months=None,
        peer_benchmark="Standard practice — all 7 target companies run monthly materiality reviews",
    )


def generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company
) -> RecommendationResult:
    """Run the full REREACT chain (gate → generate → post-process).

    Phase 3 change: non-material / low-impact articles no longer return empty.
    They get a single monitoring recommendation so the UI never shows silence.
    The `do_nothing` flag stays True so callers can distinguish active vs monitor.
    """
    skip, reason = _should_skip(insight, result)
    if skip:
        logger.info("REREACT gate: monitor-only (%s)", reason)
        monitor_rec = _build_monitoring_recommendation(insight, result, company, reason)
        return RecommendationResult(
            recommendations=[monitor_rec],
            do_nothing=True,
            gate_reason=reason,
            generator_count=1,
            validated_count=1,
        )

    client = OpenAI(api_key=get_openai_api_key())
    raw_recs = _generate_recommendations(insight, result, company, client)
    validated = _post_process(raw_recs)

    # Phase 14: Build priority matrix (urgency × impact)
    priority_matrix = _build_priority_matrix(validated)

    # Phase 14: Perspective-specific recommendation rankings
    rankings = _build_perspective_rankings(validated)

    return RecommendationResult(
        recommendations=validated,
        do_nothing=False,
        gate_reason="",
        generator_count=len(raw_recs),
        validated_count=len(validated),
        priority_matrix=priority_matrix,
        recommendation_rankings=rankings,
    )


def _build_priority_matrix(
    recs: list[Recommendation],
) -> dict[str, list[dict[str, Any]]]:
    """Compute urgency × impact 2×2 matrix for visual display."""
    matrix: dict[str, list[dict[str, Any]]] = {
        "immediate_high": [],
        "immediate_low": [],
        "deferred_high": [],
        "deferred_low": [],
    }
    for i, rec in enumerate(recs):
        urgency_bucket = (
            "immediate" if rec.urgency in ("immediate", "short_term") else "deferred"
        )
        impact_bucket = "high" if rec.estimated_impact == "High" else "low"
        matrix[f"{urgency_bucket}_{impact_bucket}"].append({
            "index": i,
            "title": rec.title,
            "type": rec.type,
            "roi": rec.roi_percentage,
            "budget": rec.estimated_budget,
        })
    return matrix


def _build_perspective_rankings(
    recs: list[Recommendation],
) -> dict[str, list[int]]:
    """Re-rank recommendations per perspective lens using ontology sort keys."""
    from engine.ontology.intelligence import query_perspective_ranking_keys

    indices = list(range(len(recs)))
    if not indices:
        return {"cfo": [], "ceo": [], "esg-analyst": []}

    urgency_order = {"immediate": 0, "short_term": 1, "medium_term": 2, "long_term": 3}
    impact_order = {"High": 0, "Medium": 1, "Low": 2}

    def _sort_value(rec: Recommendation, key: str, direction: str) -> float:
        """Get a numeric sort value for a recommendation field."""
        if key == "roi_percentage":
            val = rec.roi_percentage or 0
        elif key == "payback_months":
            val = rec.payback_months or 999
        elif key == "estimated_impact":
            val = float(impact_order.get(rec.estimated_impact, 1))
        elif key == "urgency":
            val = float(urgency_order.get(rec.urgency, 2))
        elif key == "type":
            val = 0.0 if rec.type == "compliance" else 1.0
        else:
            val = 0.0
        return -val if direction == "DESC" else val

    result: dict[str, list[int]] = {}
    for perspective in ("cfo", "ceo", "esg-analyst"):
        sort_keys = query_perspective_ranking_keys(perspective)
        if sort_keys:
            ranked = sorted(
                indices,
                key=lambda i: tuple(
                    _sort_value(recs[i], sk.sort_key, sk.sort_direction)
                    for sk in sort_keys
                ),
            )
        else:
            ranked = list(indices)
        result[perspective] = ranked
    return result
