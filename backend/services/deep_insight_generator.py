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
    # v2.1 financial calibration context
    market_cap: float | None = None,
    revenue: float | None = None,
    # v2.2 company context for richer analysis
    industry: str | None = None,
    region: str | None = None,
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

    # Guard: coerce empty strings to None (some DB fields may be '' instead of NULL)
    if market_cap is not None and not isinstance(market_cap, (int, float)):
        try:
            market_cap = float(market_cap) if market_cap else None
        except (ValueError, TypeError):
            market_cap = None
    if revenue is not None and not isinstance(revenue, (int, float)):
        try:
            revenue = float(revenue) if revenue else None
        except (ValueError, TypeError):
            revenue = None

    # Track C2: Pre-classify event type for score guard rails
    from backend.services.event_classifier import classify_event, has_financial_quantum, materiality_adjusted_bounds
    event_classification = classify_event(article_title, article_content)
    # Apply market-cap-relative materiality adjustment
    event_classification = materiality_adjusted_bounds(
        event_classification, article_content or article_title,
        market_cap_value=market_cap, revenue_last_fy=revenue,
    )

    # Load specialist personality
    agent_key = CONTENT_TYPE_TO_AGENT.get(content_type or "", "executive")
    personality_path = PERSONALITIES_DIR / f"{agent_key}.md"
    personality = personality_path.read_text(encoding="utf-8") if personality_path.exists() else ""

    article_text = article_content[:6000] if article_content else (article_summary[:1000] if article_summary else article_title)
    fw_list = ", ".join(frameworks[:6]) if frameworks else "general ESG"
    comp_list = ", ".join(competitors[:4]) if competitors else "industry peers"

    # Build context block from v2.0 module outputs
    context_parts = [f"Company: {company_name}"]
    if industry:
        context_parts.append(f"Industry: {industry}")
    if region:
        context_parts.append(f"Region: {region}")
    if competitors:
        context_parts.append(f"Competitors: {comp_list}")
    context_parts.append(f"Frameworks: {fw_list}")
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

    # Financial calibration display values
    market_cap_display = f"₹{market_cap:,.0f} Cr" if market_cap else "Unknown"
    revenue_display = f"₹{revenue:,.0f} Cr" if revenue else "Unknown"

    # Track C2: Build score bound instruction from event classifier
    score_bound_lines = [f"EVENT TYPE DETECTED: {event_classification.event_type}"]
    score_bound_lines.append(f"CALIBRATION: {event_classification.calibration_hint}")
    if event_classification.score_ceiling is not None:
        score_bound_lines.append(f"SCORE CEILING: {event_classification.score_ceiling} — do NOT score above this for this event type")
    if event_classification.score_floor is not None:
        score_bound_lines.append(f"SCORE FLOOR: {event_classification.score_floor} — do NOT score below this for this event type")
    score_bound_block = "\n".join(score_bound_lines)

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

EVENT-SPECIFIC SCORE CONSTRAINT (MANDATORY — override general anchors if they conflict):
{score_bound_block}

Return ONLY valid JSON (no markdown):
{{
  "headline": "One-line impact headline (not the article title — the IMPACT)",
  "impact_score": 0.0-10.0,
  "core_mechanism": "2-3 sentences: what structural shift is happening beneath the surface.",
  "profitability_connection": "1 sentence: how this structural shift directly connects to {company_name}'s revenue, margins, or valuation.",
  "translation": "One-line plain-language summary for {company_name}.",
  "impact_analysis": {{
    "esg_positioning": "2-3 pipe-separated keywords with numbers (e.g., 'ESG score gap -8pts | peer benchmark pressure | index exclusion risk')",
    "capital_allocation": "2-3 keywords (e.g., 'cost of capital +30bps | ₹500Cr green bond blocked | FII outflow risk 3%')",
    "valuation_cashflow": "2-3 keywords (e.g., 'P/E compression 5% | margin erosion 200bps | revenue at risk ₹120Cr')",
    "compliance_regulatory": "2-3 keywords with framework codes (e.g., 'BRSR:P6 non-compliance | SEBI penalty ₹5Cr | filing gap Jun 2027')",
    "supply_chain_transmission": "2-3 keywords or 'N/A - no supply chain nexus'",
    "people_demand": "2-3 keywords or 'N/A' (e.g., 'talent attrition +15% | consumer trust -20% | community opposition')"
  }},
  "financial_timeline": {{
    "immediate": {{
        "headline": "1-line impact headline with ₹ amount (e.g., '₹120 Cr margin pressure from compliance costs')",
        "profitability_pathway": "ESG Event → Business Mechanism → Financial Line Item → ₹ Amount",
        "cost_of_capital_impact": "+Xbps on next bond issuance or 'No direct impact'",
        "margin_pressure": "EBITDA margin compression X-Ybps or 'No margin impact'",
        "cash_flow_impact": "₹X Cr capex/opex or 'No cash flow impact'",
        "revenue_at_risk": "₹X Cr or 'No direct revenue exposure'"
    }},
    "structural": {{
        "headline": "1-line structural shift headline",
        "profitability_pathway": "ESG trend → Market mechanism → Valuation impact",
        "valuation_rerating": "P/E change X-Y% or 'Neutral'",
        "investor_flow_impact": "FII/DII flow ₹X Cr or 'No significant flow impact'",
        "competitive_position": "Market share shift or peer positioning change",
        "credit_rating_risk": "Rating outlook change or 'Stable'"
    }},
    "long_term": {{
        "headline": "1-line secular trajectory headline",
        "profitability_pathway": "Secular trend → Business model impact → Revenue opportunity/risk",
        "secular_trajectory": "Green revenue ₹X Cr by FY29/FY30 or growth direction (use realistic future fiscal years)",
        "stranded_asset_risk": "₹X Cr at risk or 'No stranded asset exposure'",
        "green_revenue_opportunity": "₹X Cr new revenue potential or 'Limited opportunity'",
        "market_share_shift": "X% shift by FY29/FY30 or direction (use realistic future fiscal years)"
    }}
  }},
  "esg_relevance_score": {{
    "environment": {{
      "score": 0-10,
      "rationale": "1 sentence: how this article connects to {company_name}'s environmental exposure"
    }},
    "social": {{
      "score": 0-10,
      "rationale": "1 sentence: social / human capital / community dimension"
    }},
    "governance": {{
      "score": 0-10,
      "rationale": "1 sentence: board oversight, ethics, compliance, control weaknesses"
    }},
    "financial_materiality": {{
      "score": 0-10,
      "rationale": "1 sentence: how directly this flows to P&L, balance sheet or cost of capital"
    }},
    "regulatory_exposure": {{
      "score": 0-10,
      "rationale": "1 sentence: applicable regulatory frameworks, mandates, or enforcement risk"
    }},
    "stakeholder_impact": {{
      "score": 0-10,
      "rationale": "1 sentence: investors, customers, employees, community exposure"
    }}
  }},
  "net_impact_summary": "3-4 sentences: the structural significance. Not just good/bad — what this means for the ESG capital landscape and where {company_name} sits.",
  "decision_summary": {{
    "materiality": "CRITICAL|HIGH|MODERATE|LOW|NON-MATERIAL",
    "action": "ACT|MONITOR|IGNORE — the executive decision signal",
    "verdict": "1 sentence: should {company_name} act? Be direct and decisive. Example: 'Immediate board-level response required — ₹590 Cr at risk' or 'No action needed — include in quarterly monitoring'",
    "financial_exposure": "1 line: the money at risk or opportunity in ₹. Example: '₹590 Cr direct loss + ₹50 Cr compliance cost' or 'No direct financial exposure'",
    "key_risk": "1 line: the single biggest risk. Example: 'SEBI penalty if fraud not disclosed within 30 days'",
    "top_opportunity": "1 line: the strategic opportunity (if any). Example: 'Early disclosure builds investor trust, +10bps cost of capital advantage' or 'None — defensive action only'",
    "timeline": "When action is needed. Example: 'Within 4 weeks' or 'Next quarterly review' or 'No deadline pressure'"
  }},
  "causal_chain": {{
    "event": "What happened — the specific trigger from the article (1 line, grounded in article facts)",
    "mechanism": "How this transmits to {company_name} — the specific business mechanism (e.g., 'energy sector credit risk shifts loan book quality', NOT generic 'affects the company')",
    "company_impact": "What this means for {company_name}'s P&L, operations, or ESG positioning (1 line with ₹/% if possible)",
    "transmission_type": "direct|credit_risk|supply_chain|regulatory|market_sentiment|sector_spillover|competitive"
  }}
}}

FINANCIAL CALIBRATION (CRITICAL):
- Company: {company_name}, Market Cap: {market_cap_display}, Revenue: {revenue_display}
- If Market Cap and Revenue are BOTH "Unknown": Do NOT invent specific ₹ amounts. Use directional language instead (e.g., "margin pressure 50-100bps", "revenue at risk: moderate", "cost of capital: +20-40bps"). Use percentages and basis points, NOT fabricated ₹ figures.
- If Market Cap / Revenue ARE known: All ₹ amounts must be PROPORTIONAL to company size. Express as "X% of FY revenue" or "X% of net worth" for context.

Rules:
- impact_analysis MUST be SHORT KEYWORD PHRASES separated by | pipes. NOT sentences. NOT paragraphs. MAX 15 words per dimension.
- financial_timeline is MANDATORY — never omit it. Use percentages/bps when company financials are unknown; use ₹ amounts only when you can calibrate against known revenue/market cap.
- Ground analysis in the pre-computed context data above
- Reference specific framework codes from the Framework RAG context
- Be specific to {company_name}, not generic ESG advice. Explain HOW the article's subject/event specifically affects {company_name}'s business — through supply chain, regulation, competitive dynamics, market conditions, etc. If the connection is weak, say so honestly.
- If a section is not applicable, set it to "N/A"
- The financial_timeline replaces the old time_horizon — focus on FINANCIAL impact at each time scale
- profitability_connection MUST explain the specific causal mechanism linking the article event to {company_name}'s P&L. Not vague hand-waving."""

    try:
        raw = await llm.chat(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=2400,
            model="gpt-4.1",
            timeout=llm.LONG_TIMEOUT,  # 60s — prevents retry storms on the heaviest call
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        insight = json.loads(raw)
        if not isinstance(insight, dict) or "core_mechanism" not in insight:
            return None

        # Track C2: Post-score enforcement — clamp LLM score to event classification bounds
        from backend.services.event_classifier import enforce_score_bounds
        raw_score = float(insight.get("impact_score", 5.0))
        full_text = article_title + " " + (article_content or "")
        article_has_quantum = has_financial_quantum(full_text)
        adjusted_score, score_warning = enforce_score_bounds(
            raw_score, event_classification, article_has_quantum
        )
        if adjusted_score != raw_score:
            insight["impact_score"] = adjusted_score
            insight["_score_adjusted"] = {
                "original": raw_score,
                "adjusted": adjusted_score,
                "reason": score_warning,
                "event_type": event_classification.event_code,
            }

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

        # Pass through financial_timeline (merged field from v2.1 prompt)
        # Already in LLM output if present — no extra assembly needed

        # Pipeline version stamp — frontend uses this to detect stale old-format data
        insight["_pipeline_version"] = "2.2"

        logger.info(
            "deep_insight_v2_generated",
            article=article_title[:50].encode("ascii", "replace").decode(),
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
        logger.error("deep_insight_failed", error=str(e).encode("ascii", "replace").decode())
        return None
