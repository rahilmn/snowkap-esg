"""Deep insight generator — single OpenAI gpt-4.1 call that synthesizes
the pipeline context into a structured 9-section JSON insight.

Inputs come from :class:`engine.analysis.pipeline.PipelineResult` — NLP,
themes, frameworks, risk, causal chains, company profile. Event
classification score bounds are enforced after the LLM response.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.analysis.pipeline import PipelineResult
from engine.config import Company, get_openai_api_key, load_settings
from engine.nlp.event_classifier import enforce_score_bounds

logger = logging.getLogger(__name__)


@dataclass
class DeepInsight:
    headline: str
    impact_score: float  # 0-10
    core_mechanism: str
    profitability_connection: str
    translation: str
    impact_analysis: dict[str, str] = field(default_factory=dict)
    financial_timeline: dict[str, Any] = field(default_factory=dict)
    esg_relevance_score: dict[str, Any] = field(default_factory=dict)
    net_impact_summary: str = ""
    decision_summary: dict[str, Any] = field(default_factory=dict)
    causal_chain: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SYSTEM_PROMPT = """You are an executive ESG intelligence analyst. Your job is to synthesize pre-computed ESG pipeline outputs into a structured insight brief suitable for C-suite consumption.

You receive NLP extractions, ESG themes, frameworks, risk assessments, and causal chains. You must NOT invent facts. Every claim must trace back to the article content, company profile, or ontology outputs.

Respond with a single JSON object using this exact schema:
{
  "headline": "<single sentence, max 120 chars, captures the business impact>",
  "impact_score": <float 0-10>,
  "core_mechanism": "<2-3 sentences describing the structural shift driving impact>",
  "profitability_connection": "<1-2 sentences linking event to P&L or valuation>",
  "translation": "<plain-language summary for non-experts>",
  "impact_analysis": {
    "esg_positioning": "<ESG score gap, peer pressure, index exclusion>",
    "capital_allocation": "<cost of capital impact, bond spread, FII outflow>",
    "valuation_cashflow": "<P/E compression, margin erosion, revenue at risk>",
    "compliance_regulatory": "<framework codes, regulatory deadlines, penalties>",
    "supply_chain_transmission": "<Tier 1/2/3 effects or 'N/A'>",
    "people_demand": "<talent, consumer, community impact or 'N/A'>"
  },
  "financial_timeline": {
    "immediate": {
      "headline": "<₹X Cr one-line impact>",
      "profitability_pathway": "<ESG event → business mechanism → ₹ amount>",
      "margin_pressure": "<bps or 'N/A'>",
      "revenue_at_risk": "<₹X Cr or 'N/A'>"
    },
    "structural": {
      "valuation_rerating": "<P/E direction + reason>",
      "competitive_position": "<gain/loss vs peers>"
    },
    "long_term": {
      "secular_trajectory": "<3-5 year view>",
      "green_revenue_opportunity": "<₹X Cr or 'N/A'>"
    }
  },
  "esg_relevance_score": {
    "environment": {"score": <0-10>, "rationale": "<text>"},
    "social": {"score": <0-10>, "rationale": "<text>"},
    "governance": {"score": <0-10>, "rationale": "<text>"},
    "financial_materiality": {"score": <0-10>, "rationale": "<text>"},
    "regulatory_exposure": {"score": <0-10>, "rationale": "<text>"},
    "stakeholder_impact": {"score": <0-10>, "rationale": "<text>"}
  },
  "net_impact_summary": "<3-4 sentence structural significance>",
  "decision_summary": {
    "materiality": "<CRITICAL | HIGH | MODERATE | LOW | NON-MATERIAL>",
    "action": "<ACT | MONITOR | IGNORE>",
    "verdict": "<1 sentence executive decision>",
    "financial_exposure": "<₹X Cr at risk, or 'N/A'>",
    "key_risk": "<single biggest risk>",
    "top_opportunity": "<strategic opportunity or 'None'>",
    "timeline": "<within X weeks or 'next quarterly review'>"
  },
  "causal_chain": {
    "event": "<what happened>",
    "mechanism": "<how this transmits to the company>",
    "company_impact": "<what it means for P&L>",
    "transmission_type": "<direct | supply_chain | regulatory | market_sentiment | sector_spillover | competitive>"
  }
}

CRITICAL RULES:
- If the pipeline shows NON-MATERIAL or LOW materiality, materiality MUST be LOW or NON-MATERIAL and action MUST be MONITOR or IGNORE.
- "Do nothing" is a valid verdict for macro signals with no company-specific transmission.
- Stay within event classification score bounds.

SPECIFICITY RULES — NO VAGUE OUTPUT:
- NEVER write "N/A" for ANY field. Instead, write "No supply chain transmission" or "No environmental dimension" — always explain WHY it's not applicable.
- financial_exposure: ALWAYS separate KNOWN amounts from SPECULATIVE. Format: "₹50.38 Cr direct demand + ₹50-150 Cr precedent risk (speculative)" — never blend into one vague range like "₹50-200 Cr".
- top_opportunity: name the SPECIFIC action with ₹ amounts. "Issue ₹500 Cr green bond" not "ESG narrative differentiation".
- key_risk: name the SPECIFIC risk with ₹ amounts and precedents. "₹50.38 Cr GST contingent liability + precedent risk" not "regulatory risk".

FINANCIAL ACCURACY RULES — SCALE TO COMPANY SIZE:
- For Large Cap banks (ICICI, revenue ~₹50,000 Cr), a ₹50 Cr event = ~0.1% of revenue = ~1 bps margin impact, NOT 8-12 bps.
- margin_pressure: calculate as (event ₹ amount / annual revenue) × 10,000 bps. A ₹50 Cr event on ₹50,000 Cr revenue = 1 bps. NEVER inflate.
- P/E compression: for single isolated events on Large Cap, use 0.0-0.1x. Reserve 0.2-0.5x for systemic/recurring issues only.
- revenue_at_risk: distinguish DIRECT revenue loss from INDIRECT (precedent, contagion). Format: "₹50 Cr direct + ₹X Cr indirect (if precedent established)".
- If CAUSAL PRIMITIVES CONTEXT provides β elasticity, use it to COMPUTE the impact: Δ = β × Δsource × base. Show the computation.

FRAMEWORK ACCURACY RULES — MATCH EVENT TYPE:
- ESRS E1 = Climate Change ONLY. For tax/governance events, use ESRS G1 (Business Conduct).
- ESRS E2 = Pollution. ESRS E3 = Water. ESRS E4 = Biodiversity. ESRS E5 = Resource use.
- ESRS S1 = Own workforce. ESRS S2 = Value chain workers. ESRS S3 = Affected communities. ESRS S4 = Consumers.
- GRI 207 = Tax. GRI 205 = Anti-corruption. GRI 305 = Emissions. GRI 303 = Water. GRI 403 = H&S.
- NEVER cite a framework section that doesn't match the event type. A GST demand triggers GRI:207 and ESRS G1, NOT ESRS E1.
- When citing framework sections, use the MOST SPECIFIC code available (e.g., BRSR:P5:Q12 not just "BRSR").

PERSPECTIVE ACCURACY RULES:
- esg_relevance_score dimensions: if a dimension is truly 0 (e.g., environment for a tax event), score it 0/10 and explain: "No environmental dimension; event is purely governance/tax related."
- what_matters bullets must be DIFFERENT across CFO/CEO/ESG Analyst perspectives. CFO = ₹ impact + margin + cost of capital. CEO = competitive position + strategic opportunity + board action. ESG Analyst = framework gaps + compliance deadlines + stakeholder risk.

- impact_analysis fields must name ₹ amounts, specific frameworks, named competitors, and concrete mechanisms.
- financial_timeline.immediate.headline must include a ₹ figure.
- financial_timeline.structural.competitive_position: ALWAYS name 1-2 competitors and compare.
- core_mechanism must explain the SPECIFIC transmission chain with named entities.
- net_impact_summary must include at least one ₹ figure and one framework reference.
- headline must capture WHAT happened and WHY it matters financially (max 120 chars).
- Every claim must trace to article content or pipeline context. Do not invent facts, but DO extrapolate reasonable ₹ estimates from company scale.

- Return ONLY the JSON object, no markdown, no preamble."""


def _build_user_prompt(result: PipelineResult, company: Company) -> str:
    nlp = result.nlp
    themes = result.themes
    relevance = result.relevance
    event = result.event
    frameworks = result.frameworks[:6]
    risk = result.risk
    causal = result.causal_chains[:3]

    lines: list[str] = []
    lines.append("=== ARTICLE ===")
    lines.append(f"Title: {result.title}")
    lines.append(f"Source: {result.source} (credibility tier {nlp.source_credibility_tier})")
    lines.append(f"Published: {result.published_at}")
    lines.append(f"URL: {result.url}")
    lines.append("")

    lines.append("=== COMPANY PROFILE ===")
    lines.append(f"Name: {company.name}")
    lines.append(f"Industry: {company.industry} (SASB: {company.sasb_category})")
    lines.append(f"Market cap: {company.market_cap}")
    lines.append(
        f"HQ: {company.headquarter_city}, {company.headquarter_country} ({company.headquarter_region})"
    )
    # Phase 14: Peer comparison context
    try:
        from engine.ontology.intelligence import query_competitors
        competitors = query_competitors(company.slug)
        if competitors:
            lines.append(f"Key competitors: {', '.join(competitors)}")
    except Exception:
        pass
    lines.append("")

    lines.append("=== NLP EXTRACTION ===")
    lines.append(f"Sentiment: {nlp.sentiment} (tone: {', '.join(nlp.tone)})")
    lines.append(f"Core claim: {nlp.narrative_core_claim}")
    lines.append(f"Causation: {nlp.narrative_implied_causation}")
    lines.append(f"Stakeholders: {nlp.narrative_stakeholder_framing}")
    lines.append(f"Entities: {', '.join(nlp.entities[:8])}")
    if nlp.financial_signal and nlp.financial_signal.get("amount"):
        lines.append(
            f"Financial signal: {nlp.financial_signal['amount']} {nlp.financial_signal.get('unit', '')}"
            f" — {nlp.financial_signal.get('context', '')}"
        )
    if nlp.regulatory_references:
        lines.append(f"Regulatory refs: {', '.join(nlp.regulatory_references)}")
        # Phase 14: Penalty precedent context for regulatory articles
        try:
            from engine.ontology.intelligence import query_penalty_precedents
            penalties = query_penalty_precedents("India")
            if penalties:
                lines.append("Penalty precedents (from ontology):")
                for p in penalties[:3]:
                    lines.append(f"  - {p.label}: {p.median_fine_range} ({p.regulator})")
        except Exception:
            pass
    lines.append("")

    lines.append("=== ESG THEMES ===")
    lines.append(f"Primary: {themes.primary_theme} ({themes.primary_pillar}) — confidence {themes.confidence}")
    lines.append(f"Sub-metrics: {', '.join(themes.primary_sub_metrics)}")
    if themes.secondary_themes:
        secondary = ", ".join(s.get("theme", "") for s in themes.secondary_themes)
        lines.append(f"Secondary: {secondary}")
    lines.append("")

    lines.append("=== RELEVANCE SCORING (from ontology) ===")
    lines.append(
        f"Total: {relevance.total}/10 (adjusted {relevance.adjusted_total}, tier {relevance.tier})"
    )
    lines.append(
        f"Materiality weight (ontology): {relevance.materiality_weight} for "
        f"{themes.primary_theme} × {company.industry}"
    )
    lines.append("")

    lines.append("=== EVENT CLASSIFICATION (hard constraints) ===")
    lines.append(
        f"Type: {event.label} (floor={event.score_floor}, ceiling={event.score_ceiling})"
    )
    if event.has_financial_quantum:
        lines.append(f"Financial quantum: ₹{event.financial_amount_cr} Cr detected in text")
    lines.append(f"Transmission: {event.financial_transmission}")
    lines.append("")

    if frameworks:
        lines.append("=== FRAMEWORKS TRIGGERED (from ontology) ===")
        for fm in frameworks:
            tag = " [MANDATORY]" if fm.is_mandatory else ""
            lines.append(
                f"- {fm.framework_label}{tag} (relevance {fm.relevance:.2f}): "
                f"{fm.profitability_link[:140]}"
            )
        lines.append("")

    if causal:
        lines.append("=== CAUSAL CHAINS (from ontology BFS) ===")
        for cp in causal:
            lines.append(
                f"- [{cp.relationship_type}, {cp.hops} hops, impact {cp.impact_score}] {cp.explanation}"
            )
        lines.append("")

    if risk and risk.top_risks:
        lines.append("=== TOP RISKS (ESG + TEMPLES, industry-weighted) ===")
        for r in risk.top_risks[:5]:
            lines.append(
                f"- {r.category}: {r.level} (P={r.probability} × E={r.exposure} × w={r.industry_weight} = {r.adjusted_score})"
            )
        lines.append("")

    # Phase 17: Causal Primitives context — quantitative cascade parameters
    try:
        from engine.ontology.intelligence import query_cascade_context
        # Use event_id (e.g. "event_heavy_penalty") not label ("Heavy Regulatory Penalty")
        event_id = event.event_id if event and hasattr(event, "event_id") else ""
        if event_id:
            cascade_ctx = query_cascade_context(event_id)
            if cascade_ctx:
                lines.append(f"=== {cascade_ctx} ===")
                lines.append("")
            else:
                logger.warning(
                    "No cascade context for event '%s' — possible unmapped event type",
                    event_id,
                )
                lines.append(
                    "=== NOTE: No causal primitives mapped for this event type. "
                    "Estimate financial impact conservatively using company scale and "
                    "industry benchmarks. Do not claim precision without edge parameters. ==="
                )
                lines.append("")
    except Exception:
        pass

    lines.append("=== INSTRUCTIONS ===")
    lines.append(
        "Produce the structured JSON insight now. Remember: stay within event score bounds. "
        "Do not invent numbers. Respect the do-nothing rule for LOW/NON-MATERIAL events. "
        "If CAUSAL PRIMITIVES CONTEXT is provided, use the β elasticities and lag windows "
        "to compute financial_exposure instead of guessing ranges."
    )
    return "\n".join(lines)


def generate_deep_insight(
    result: PipelineResult, company: Company
) -> DeepInsight | None:
    """Run the single-call OpenAI gpt-4.1 synthesis.

    Returns ``None`` for REJECTED articles (caller should skip).
    """
    if result.rejected:
        return None

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")
    max_tokens = llm_cfg.get("max_tokens_insight", 2400)
    temperature = llm_cfg.get("temperature", 0.2)

    client = OpenAI(api_key=get_openai_api_key())
    user_prompt = _build_user_prompt(result, company)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning(
            "insight_generator LLM failed (%s) — returning minimal fallback",
            type(exc).__name__,
        )
        return DeepInsight(
            headline=result.title[:120],
            impact_score=float(result.relevance.adjusted_total),
            core_mechanism="LLM synthesis unavailable; see pipeline outputs.",
            profitability_connection="",
            translation="",
            warnings=[f"llm_error: {type(exc).__name__}"],
        )

    # Clamp impact score to event classification bounds
    raw_score = float(parsed.get("impact_score", result.relevance.adjusted_total) or 0)
    clamped_score, warning = enforce_score_bounds(raw_score, result.event)
    warnings = [warning] if warning else []

    return DeepInsight(
        headline=str(parsed.get("headline", "") or result.title)[:200],
        impact_score=clamped_score,
        core_mechanism=str(parsed.get("core_mechanism", "") or ""),
        profitability_connection=str(parsed.get("profitability_connection", "") or ""),
        translation=str(parsed.get("translation", "") or ""),
        impact_analysis=dict(parsed.get("impact_analysis", {}) or {}),
        financial_timeline=dict(parsed.get("financial_timeline", {}) or {}),
        esg_relevance_score=dict(parsed.get("esg_relevance_score", {}) or {}),
        net_impact_summary=str(parsed.get("net_impact_summary", "") or ""),
        decision_summary=dict(parsed.get("decision_summary", {}) or {}),
        causal_chain=dict(parsed.get("causal_chain", {}) or {}),
        warnings=warnings,
    )
