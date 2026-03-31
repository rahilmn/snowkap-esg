"""Deep ESG Insight Generator — v2.0 Output Template (Module 8).

For articles with relevance_score ≥ 7, generates the full v2.0 intelligence brief:
1. Core Mechanism — structural driver beneath the surface
2. Impact Analysis — 6 dimensions (ESG positioning, capital, valuation, compliance, supply chain, demand)
3. Time Horizon — short/medium/long term
4. Net Impact Summary — structural synthesis

Pre-computed v2.0 module data (NLP, themes, frameworks, risk matrix, geo)
is passed in and assembled into the brief alongside the LLM-generated sections.

Uses specialist agent personality based on content_type.
"""

import json
from pathlib import Path

import structlog

from backend.core import llm

logger = structlog.get_logger()

PERSONALITIES_DIR = Path(__file__).parent.parent / "agent" / "personalities"

CONTENT_TYPE_TO_AGENT: dict[str, str] = {
    "regulatory": "compliance",
    "financial": "executive",
    "operational": "supply_chain",
    "reputational": "stakeholder",
    "technical": "analytics",
    "narrative": "content",
    "data_release": "analytics",
}


async def generate_deep_insight(
    article_title: str,
    article_content: str | None,
    article_summary: str | None,
    company_name: str,
    frameworks: list[str],
    sentiment_score: float | None,
    urgency: str | None,
    content_type: str | None,
    esg_pillar: str | None,
    competitors: list[str] | None = None,
    # v2.0 pre-computed module data (passed through from pipeline)
    nlp_extraction: dict | None = None,
    esg_themes: dict | None = None,
    framework_matches: list[dict] | None = None,
    risk_matrix: dict | None = None,
    geographic_signal: dict | None = None,
) -> dict | None:
    """Generate v2.0 deep insight brief with all module data assembled.

    Only called for articles with relevance_score ≥ 7.
    LLM generates: headline, core_mechanism, impact_analysis (6 dims),
    time_horizon, net_impact_summary.
    Pre-computed module data (NLP, themes, frameworks, risk, geo) is
    passed through and assembled into the final brief dict.
    """
    if not llm.is_configured():
        return None

    # Load specialist personality
    agent_key = CONTENT_TYPE_TO_AGENT.get(content_type or "", "executive")
    personality_path = PERSONALITIES_DIR / f"{agent_key}.md"
    personality = personality_path.read_text(encoding="utf-8") if personality_path.exists() else ""

    article_text = article_content[:2500] if article_content else (article_summary[:500] if article_summary else article_title)
    fw_list = ", ".join(frameworks[:6]) if frameworks else "general ESG"
    comp_list = ", ".join(competitors[:4]) if competitors else "industry peers"

    # Build context block from v2.0 module outputs
    context_parts = [f"Company: {company_name}", f"Frameworks: {fw_list}", f"Competitors: {comp_list}"]
    if nlp_extraction:
        s = nlp_extraction.get("sentiment", {})
        t = nlp_extraction.get("tone", {})
        n = nlp_extraction.get("narrative_arc", {})
        context_parts.append(
            f"NLP: Sentiment={s.get('label','?')} ({s.get('score',0)}), "
            f"Tone={t.get('primary','?')}, "
            f"Core claim: {n.get('core_claim','?')}, "
            f"Temporal: {n.get('temporal_framing','?')}"
        )
    if esg_themes:
        pt = esg_themes.get("primary_theme", "?")
        context_parts.append(f"ESG Themes: Primary={pt}, Secondary={[s.get('theme','') for s in esg_themes.get('secondary_themes',[])]}")
    if framework_matches:
        fm_list = [f"{m.get('framework_id','')}:{','.join(m.get('triggered_sections',[])[:2])}" for m in framework_matches[:4]]
        context_parts.append(f"Framework RAG: {'; '.join(fm_list)}")
    if geographic_signal:
        context_parts.append(f"Geo: {geographic_signal.get('locations',[])} → Jurisdictions: {geographic_signal.get('regulatory_jurisdictions',[])}")
    if risk_matrix:
        top = risk_matrix.get("top_risks", [])[:3]
        context_parts.append(f"Top Risks: {[r.get('name','') + '=' + str(r.get('score',0)) for r in top]}")

    context_block = "\n".join(f"- {p}" for p in context_parts)

    system_prompt = f"""{personality}

## Analysis Mode: v2.0 Deep Structured Insight
You are generating the core analysis sections of a SNOWKAP ESG Intelligence Brief v2.0.
Pre-computed NLP, themes, frameworks, risk, and geographic data are provided as context.
Your job is to generate the ANALYTICAL sections — core mechanism, 6-dimension impact, time horizon, synthesis.
This is NOT a summary — it's deep structural analysis for institutional decision-makers."""

    user_prompt = f"""Analyze this article and produce a structured JSON insight.

ARTICLE: "{article_title}"
CONTENT: {article_text}

CONTEXT:
{context_block}

IMPACT SCORE CALIBRATION (use these anchors — score MUST reflect financial materiality):
- 9-10: Existential threat or transformation (>20% revenue/valuation impact). Examples: criminal indictment of CEO, license revocation, mega-merger.
- 7-8: Material, requires board/CXO attention (5-20% impact). Examples: major regulatory change, large-scale fraud, significant M&A.
- 5-6: Notable, departmental action needed (1-5% impact). Examples: new compliance requirement, mid-size capital raise, operational incident.
- 3-4: Awareness item, monitor quarterly (<1% impact). Examples: routine expansion, minor policy update, industry trend report.
- 1-2: Noise, no action required. Examples: generic commentary, non-material announcements.

A systemic regulatory change affecting an entire sector (e.g., RBI climate disclosure pause) should score 8+.
Routine capex or expansion announcements should score 3-5, not 7-8.

Return ONLY valid JSON (no markdown):
{{
  "headline": "One-line impact headline (not the article title — the IMPACT)",
  "impact_score": 0.0-10.0,
  "core_mechanism": "2-3 sentences: what structural shift is happening beneath the surface.",
  "translation": "One-line plain-language summary for {company_name}.",
  "impact_analysis": {{
    "esg_positioning": "How this shifts {company_name}'s relative ESG attractiveness (1-2 sentences)",
    "capital_allocation": "Effect on institutional capital flows, cost of capital, equity risk premium (1-2 sentences)",
    "valuation_cashflow": "Impact on P/E, EV/EBITDA, margin structure, demand effects (1-2 sentences)",
    "compliance_regulatory": "Specific frameworks triggered, disclosure obligations. Cite codes from context. (1-2 sentences)",
    "supply_chain_transmission": "Tier 1/2/3 impact pathway, amplification or dampening (1-2 sentences)",
    "people_demand": "Employee, customer, community impact. Consumer demand trajectory. (1-2 sentences)"
  }},
  "time_horizon": {{
    "short_term": "0-6 months: immediate effects",
    "medium_term": "6-24 months: structural shifts, compliance timelines",
    "long_term": "2-5+ years: secular trends, market redefinition"
  }},
  "net_impact_summary": "3-4 sentences: the structural significance. Not just good/bad — what this means for the ESG capital landscape and where {company_name} sits."
}}

Rules:
- Ground analysis in the pre-computed context data above
- Reference specific framework codes from the Framework RAG context
- Include quantified impacts where the article provides numbers
- Be specific to {company_name}, not generic ESG advice
- Distinguish direct vs. indirect impact explicitly
- If a section is not applicable, set it to null"""

    try:
        raw = await llm.chat(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=1500,
            model="gpt-4o",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        insight = json.loads(raw)
        if not isinstance(insight, dict) or "core_mechanism" not in insight:
            return None

        # Assemble the full v2.0 brief by merging LLM output with pre-computed module data
        if nlp_extraction:
            insight["nlp_extraction"] = nlp_extraction
        if esg_themes:
            insight["esg_themes"] = esg_themes
        if framework_matches:
            insight["framework_alignment"] = framework_matches
        if risk_matrix:
            insight["risk_matrix"] = risk_matrix
        if geographic_signal:
            insight["geographic_signal"] = geographic_signal

        # Backward compat: keep old field names that downstream code uses
        if "impact_analysis" in insight:
            ia = insight["impact_analysis"]
            insight["esg_impact_analysis"] = {
                "environmental": ia.get("esg_positioning"),
                "social": ia.get("people_demand"),
                "governance": ia.get("compliance_regulatory"),
            }
            insight["financial_valuation_impact"] = {
                "cost_of_capital": ia.get("capital_allocation"),
                "investor_flows": ia.get("capital_allocation"),
                "demand_effects": ia.get("valuation_cashflow"),
            }
            insight["compliance_regulatory_impact"] = {
                "frameworks_triggered": frameworks,
                "disclosure_pressure": ia.get("compliance_regulatory"),
                "regulatory_timeline": None,
            }
            insight["risk_mapping"] = risk_matrix or {}
            insight["final_synthesis"] = insight.get("net_impact_summary", "")

        logger.info(
            "deep_insight_v2_generated",
            article=article_title[:50],
            company=company_name,
            impact_score=insight.get("impact_score"),
            specialist=agent_key,
            has_nlp=bool(nlp_extraction),
            has_themes=bool(esg_themes),
            has_rag=bool(framework_matches),
            has_risk=bool(risk_matrix),
        )
        return insight
    except Exception as e:
        logger.error("deep_insight_failed", error=str(e))
        return None
