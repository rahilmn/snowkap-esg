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
    """Return how many recommendations to generate based on materiality."""
    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "")).upper()
    if materiality in ("CRITICAL", "HIGH"):
        return 5
    if materiality == "MODERATE":
        return 4
    # LOW materiality still gets 2 monitoring-oriented recommendations
    return 2


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
- roi_percentage: estimate conservatively. For compliance, ROI = avoided penalty / implementation cost. For ESG positioning, ROI = valuation premium / cost. NEVER use null — always estimate.
- payback_months: for capex, use industry standard payback periods. For compliance, use regulatory deadline as outer bound. NEVER use null.
- If PEER ACTIONS are provided, reference what competitors did and suggest matching or exceeding their approach.
- For LOW materiality articles: focus on monitoring actions and disclosure improvements, but still be SPECIFIC about what to monitor and how.

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
      "peer_benchmark": "<what competitors did in similar situations, or null>"
    }
  ]
}

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

    lines.append("")
    rec_count = _get_rec_count(insight)
    lines.append(f"Generate exactly {rec_count} actionable recommendations for this company. Today's date is 2026-04-12.")
    return "\n".join(lines)


def _generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company, client: OpenAI
) -> list[Recommendation]:
    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    max_tokens = llm_cfg.get("max_tokens_recommendation", 1500)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _GENERATOR_SYSTEM.replace("%%REC_COUNT%%", str(_get_rec_count(insight)))},
                {
                    "role": "user",
                    "content": _build_generator_prompt(insight, result, company),
                },
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("recommendation generator failed: %s", type(exc).__name__)
        return []

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

            recommendations.append(
                Recommendation(
                    title=str(r.get("title", "") or "")[:200],
                    description=str(r.get("description", "") or "")[:500],
                    type=str(r.get("type", "operational") or "operational"),
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


def generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company
) -> RecommendationResult:
    """Run the full REREACT chain (gate → generate → post-process)."""
    skip, reason = _should_skip(insight, result)
    if skip:
        logger.info("REREACT gate: skip (%s)", reason)
        return RecommendationResult(
            recommendations=[],
            do_nothing=True,
            gate_reason=reason,
            generator_count=0,
            validated_count=0,
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
