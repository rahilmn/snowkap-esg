"""REREACT 3-Agent Recommendation Validation Engine (Phase 3).

Pipeline: Generator → Analyzer → Validator

Agent 1 (Generator): Produces 3-5 recommendations using specialist personality
Agent 2 (Analyzer): Evaluates logical consistency, ESG-materiality, financial realism
Agent 3 (Validator): Independent critic — checks confidence, hallucinations, actionability
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from backend.core import llm


def _post_process_recommendations(result: dict, today_str: str) -> dict:
    """Fix past dates and flag weak profitability links — safety net after LLM output."""
    try:
        today = date.fromisoformat(today_str)
    except (ValueError, TypeError):
        today = datetime.now(timezone.utc).date()

    for rec in result.get("validated_recommendations", []):
        # Fix past dates
        deadline = rec.get("deadline", "")
        if deadline:
            try:
                d = date.fromisoformat(str(deadline)[:10])
                if d < today:
                    rec["deadline"] = f"{today.year + 1}-{str(deadline)[5:10]}"
            except (ValueError, TypeError):
                rec["deadline"] = f"{today.year}-12-31"

        # Flag weak profitability links (no numbers)
        pl = rec.get("profitability_link", "")
        if pl and not re.search(r"[₹$%]|\d+\s*(Cr|Lakh|bps|crore)", str(pl), re.IGNORECASE):
            rec["profitability_link"] = pl + f" [Quantify: estimate ₹ impact for {today.year}]"

        # ROI and payback computation (Phase 5D)
        budget_str = rec.get("estimated_budget", "")
        profit_str = rec.get("profitability_link", "")
        budget_num = None
        profit_num = None
        # Try to extract numeric budget (e.g., "₹20 Cr" → 20)
        budget_match = re.search(r"[₹$]\s*(\d+(?:\.\d+)?)", str(budget_str))
        if budget_match:
            budget_num = float(budget_match.group(1))
        # Try to extract numeric profitability (e.g., "₹18 Cr annually" → 18)
        profit_match = re.search(r"[₹$]\s*(\d+(?:\.\d+)?)", str(profit_str))
        if profit_match:
            profit_num = float(profit_match.group(1))
        if budget_num and profit_num and budget_num > 0:
            rec["roi_percentage"] = round((profit_num / budget_num - 1) * 100, 1)
            rec["payback_months"] = round(budget_num / (profit_num / 12), 1)

        # Priority derivation (Phase 5E)
        urgency = rec.get("urgency", "")
        impact = rec.get("estimated_impact", "")
        if urgency == "immediate" or impact == "High":
            rec["priority"] = "CRITICAL"
        elif urgency == "short_term" or impact == "Medium":
            rec["priority"] = "HIGH"
        else:
            rec["priority"] = "MEDIUM"

        # Risk of inaction score (1-10): combines priority level + ROI magnitude
        # CRITICAL priority → base 7; HIGH → base 5; MEDIUM → base 3
        # Boosted by ROI (higher return foregone = higher inaction risk)
        base_inaction = {"CRITICAL": 7, "HIGH": 5, "MEDIUM": 3}.get(rec.get("priority", "MEDIUM"), 3)
        roi = rec.get("roi_percentage")
        if roi is not None:
            if roi > 500:
                base_inaction = min(10, base_inaction + 3)
            elif roi > 200:
                base_inaction = min(10, base_inaction + 2)
            elif roi > 100:
                base_inaction = min(10, base_inaction + 1)
        rec["risk_of_inaction"] = base_inaction

    return result

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


def _load_personality(agent_key: str) -> str:
    path = PERSONALITIES_DIR / f"{agent_key}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


async def rereact_recommendations(
    article_title: str,
    article_content: str | None,
    deep_insight: dict,
    company_name: str,
    frameworks: list[str],
    content_type: str | None,
    user_role: str | None = None,
    competitors: list[str] | None = None,
    market_cap: str | None = None,
    listing_exchange: str | None = None,
    headquarter_country: str | None = None,
) -> dict | None:
    """3-agent REREACT recommendation pipeline.

    Returns validated recommendations or None on failure.
    """
    if not llm.is_configured():
        return None

    article_text = article_content[:1500] if article_content else article_title
    fw_list = ", ".join(frameworks[:5]) if frameworks else "ESG frameworks"
    insight_summary = json.dumps({
        "headline": deep_insight.get("headline", ""),
        "core_mechanism": deep_insight.get("core_mechanism", ""),
        "impact_score": deep_insight.get("impact_score", 0),
        "risk_mapping": deep_insight.get("risk_mapping", {}),
    }, indent=2)

    # === AGENT 1: GENERATOR ===
    agent_key = CONTENT_TYPE_TO_AGENT.get(content_type or "", "executive")
    generator_personality = _load_personality(agent_key)

    comp_list = ", ".join(competitors[:5]) if competitors else "industry peers"

    from datetime import datetime, timezone, date as _date_type
    today = datetime.now(timezone.utc).date().isoformat()
    max_date = (datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 2)).isoformat()

    # Get company context
    _mc = market_cap or "Unknown"
    _le = listing_exchange or "Unknown"
    _hc = headquarter_country or "Unknown"

    generator_prompt = f"""You are analyzing an ESG news event for {company_name}. Produce actionable recommendations.

╔══════════════════════════════════════════════════╗
║  ⚠️  CRITICAL DATE RULE — READ BEFORE ANYTHING   ║
║  Today is {today}. The year is {today[:4]}.              ║
║  EVERY deadline MUST be between {today} and {max_date}. ║
║  Dates before {today[:4]} will CRASH the system.         ║
║  Safe default: {today[:4]}-12-31                        ║
╚══════════════════════════════════════════════════╝

COMPANY PROFILE:
- {company_name} | {_mc} | Listed: {_le} | HQ: {_hc}
- Budget calibration: {"₹10-100 Cr, board-level owners" if "Large" in _mc else "₹1-10 Cr, CXO-level owners" if "Mid" in _mc else "₹10L-1 Cr, department heads"}

ARTICLE: "{article_title}"
COMPETITORS: {comp_list}
FRAMEWORKS: {fw_list}

DEEP INSIGHT:
{insight_summary}

DATA GROUNDING RULES (CRITICAL):
- Every recommendation MUST reference a SPECIFIC fact from the article (a number, name, or event)
- If the article mentions a specific amount (e.g., ₹590 Cr), your recommendation must cite it
- Generic recommendations that could apply to ANY company will be REJECTED by the Validator
- The profitability_link MUST contain specific numbers (₹ amounts, %, bps)
  BAD: "reduces risk" or "improves ESG score"
  GOOD: "reduces cost of capital by 25-50 bps, saving ₹15-30 Cr annually on ₹6,000 Cr debt"
  GOOD: "avoids ₹5 Cr SEBI penalty for BRSR non-compliance"

Return a JSON object:
{{
  "recommendations": [
    {{
      "type": "strategic|financial|esg_positioning|operational|compliance",
      "title": "Action-oriented title referencing specific article data (10 words max)",
      "responsible_party": "Specific role (e.g., 'Chief Risk Officer', 'Audit Committee')",
      "description": "Specific steps grounded in article facts (40 words max)",
      "framework_section": "Exact code (e.g., BRSR:P1:Q5, GRI:205-2)",
      "deadline": "YYYY-MM-DD format, MUST be after {today}, e.g., {today[:4]}-09-30",
      "estimated_budget": "Calibrated for {_mc}: {'₹10-100 Cr' if 'Large' in _mc else '₹1-10 Cr' if 'Mid' in _mc else '₹10L-1 Cr'}",
      "success_criterion": "Measurable outcome with numbers",
      "urgency": "immediate|short_term|ongoing",
      "estimated_impact": "High/Medium/Low",
      "profitability_link": "MUST contain ₹/% — how this saves money or generates revenue (1 sentence)"
    }}
  ]
}}

LANGUAGE RULES:
- BANNED verbs: "enhance", "strengthen", "improve", "develop", "bolster", "foster"
- REQUIRED verbs: "commission", "file", "appoint", "allocate", "disclose", "audit", "terminate"
- Every recommendation needs: named owner + framework code + calendar deadline after {today} + measurable criterion

PEER BENCHMARKING: Reference specific competitor actions when available. Frame as closing or extending gaps.

FRAMEWORK CITATION GUIDE — use ONLY the section that matches the article topic:
┌─────────────────────────────────┬──────────────────────────────────────────────────────┐
│ Article Topic                   │ Correct framework_section                            │
├─────────────────────────────────┼──────────────────────────────────────────────────────┤
│ Fraud / corruption / bribery    │ BRSR:P1:Q5 (ethics & penalties)                      │
│ Employee safety / LTIFR         │ BRSR:P3:Q18 (safety incidents)                       │
│ Parental leave / HR benefits    │ BRSR:P3:Q14 (parental leave retention)               │
│ Worker rights / wages           │ BRSR:P5:Q26 (minimum wages) or BRSR:P5:Q29          │
│ GHG / carbon emissions          │ BRSR:P6:Q38 (Scope 1&2) or BRSR:P6:Q48 (Scope 3)   │
│ Water / effluent discharge      │ BRSR:P6:Q34 / Q36 (withdrawal & discharge)           │
│ Waste / pollution               │ BRSR:P6:Q41 / Q47 (waste & compliance notices)       │
│ Energy / PAT scheme             │ BRSR:P6:Q31 / Q33 (energy & PAT)                    │
│ Consumer complaints / data      │ BRSR:P9:Q61 / Q63 (complaints & cybersecurity)       │
│ Trade finance / policy advocacy │ BRSR:P7:Q50 (policy advocacy positions)              │
│ CSR / community investment      │ BRSR:P8:Q54 / Q59 (CSR projects & spend)             │
│ Greenwashing / ESG disclosure   │ GRI:2-22 (sustainability reporting) or BRSR:SectionB │
│ Climate risk / TCFD             │ TCFD:Strategy or TCFD:Risk_Management                │
│ Scope 3 / supply chain          │ BRSR:P6:Q48 + GRI:305-3                              │
│ Banking / credit risk ESG       │ SASB:FN-CB-410a.2 (ESG integration in lending)       │
│ Regulatory compliance notice    │ BRSR:P1:Q5 + applicable framework penalty section    │
└─────────────────────────────────┴──────────────────────────────────────────────────────┘
RULE: If the topic is NOT in this table, use GRI:2-6 (activities & value chain) as default.
NEVER cite BRSR:P3 for trade/tariff/policy topics. NEVER cite GHG_PROTOCOL for governance events.

"""

    # Inject role-specific framing when user_role is provided
    if user_role:
        from backend.services.role_curation import get_role_profile
        role_profile = get_role_profile(user_role)
        role_guidance = {
            "board_member": (
                "CRITICAL — TARGET AUDIENCE: Board Member / Independent Director\n"
                "REWRITE the impact_analysis through a governance lens:\n"
                "- esg_positioning → reframe as 'fiduciary exposure if board does not act'\n"
                "- capital_allocation → reframe as 'board-level capital allocation decision required'\n"
                "Each recommendation MUST be a governance-level directive (e.g., 'Direct management to...')\n"
                "Type field: use 'governance' instead of generic types\n"
                "Urgency: board members only see 'immediate' or 'next board meeting'\n"
                "Keep descriptions under 25 words — board resolutions are terse"
            ),
            "ceo": (
                "CRITICAL — TARGET AUDIENCE: CEO\n"
                "REWRITE the impact_analysis through a competitive positioning lens:\n"
                "- esg_positioning → reframe as 'competitive narrative shift — who wins/loses'\n"
                "- capital_allocation → reframe as 'market perception and investor story impact'\n"
                "- demand_macro → reframe as 'what this means for our brand vs peers'\n"
                "Each recommendation MUST be a strategic move (e.g., 'Announce...', 'Position...', 'Counter...')\n"
                "Type field: use 'strategic' for all — CEOs don't think in compliance buckets\n"
                "Include competitor names from the data when available\n"
                "Tone: decisive, forward-looking, narrative-aware"
            ),
            "cfo": (
                "CRITICAL — TARGET AUDIENCE: CFO\n"
                "REWRITE the impact_analysis to be purely quantitative:\n"
                "- esg_positioning → reframe as 'ESG discount/premium impact on valuation (basis points)'\n"
                "- capital_allocation → reframe as 'cost of capital movement: +/- X bps'\n"
                "- valuation_cashflow → MUST include specific numbers (₹ crore, %, bps) or say 'data not available'\n"
                "Each recommendation MUST include:\n"
                "  - Estimated cost (₹ crore or 'requires costing')\n"
                "  - Payback period (months/quarters)\n"
                "  - ROI signal (High/Medium/Low with reasoning)\n"
                "Type field: use 'financial' or 'investment' — no soft categories\n"
                "NEVER give a recommendation without attaching a financial framing"
            ),
            "cso": (
                "CRITICAL — TARGET AUDIENCE: Chief Sustainability Officer / Head of ESG\n"
                "REWRITE the impact_analysis through a framework/taxonomy lens:\n"
                "- esg_positioning → reframe as 'ESG score impact: which indices move, by how much'\n"
                "- compliance_regulatory → MUST list exact framework:section codes triggered (BRSR:P6, GRI:305-1, ESRS:E1-5)\n"
                "Each recommendation MUST:\n"
                "  - Reference a specific framework section code\n"
                "  - Include a disclosure deadline or 'next reporting cycle'\n"
                "  - State the current gap vs required disclosure\n"
                "Type field: use 'esg_positioning' or 'compliance' — CSOs think in frameworks\n"
                "Framework field: MUST be specific (BRSR:P6:Q12, not just 'BRSR')\n"
                "Include benchmark comparison to sector peers where data exists"
            ),
            "compliance": (
                "CRITICAL — TARGET AUDIENCE: Compliance Officer / Legal Head\n"
                "REWRITE the impact_analysis as a compliance exposure assessment:\n"
                "- compliance_regulatory → MUST list: regulation name, exact section, filing deadline, penalty amount\n"
                "- All other dimensions → reframe as 'regulatory risk if non-compliant'\n"
                "Each recommendation MUST be a compliance action:\n"
                "  - 'File [specific form/report] under [regulation:section] by [deadline]'\n"
                "  - 'Update [disclosure] in [section] to reflect [new obligation]'\n"
                "Type field: ONLY use 'compliance' — everything is compliance for this role\n"
                "Urgency: map to actual regulatory deadlines, not vague 'short_term'\n"
                "NEVER include strategic or positioning advice — compliance officers execute, not strategize"
            ),
            "supply_chain": (
                "CRITICAL — TARGET AUDIENCE: Supply Chain / Operations Head\n"
                "REWRITE the impact_analysis through an operational disruption lens:\n"
                "- supply_chain_transmission → MUST map: Tier 1 (direct), Tier 2 (indirect), geographic concentration\n"
                "- demand_macro → reframe as 'cost pass-through feasibility and margin compression'\n"
                "Each recommendation MUST be an operational action:\n"
                "  - 'Qualify alternate supplier for [material] in [geography]'\n"
                "  - 'Renegotiate [contract term] with [supplier tier]'\n"
                "  - 'Pre-position buffer stock of [X] at [facility]'\n"
                "Type field: use 'operational' — supply chain doesn't do 'strategic'\n"
                "Reference specific facilities from the data when available\n"
                "NEVER give governance-level or financial advice — stay operational"
            ),
        }
        audience_guidance = role_guidance.get(user_role, "")
        if audience_guidance:
            generator_prompt += f"\n\n{audience_guidance}"
        elif role_profile.get("primary_focus"):
            generator_prompt += (
                f"\n\nTARGET AUDIENCE: {role_profile.get('description', user_role)}\n"
                f"- Emphasize {role_profile['primary_focus']}\n"
                f"- Recommendation style: {role_profile.get('recommendation_style', 'actionable')}"
            )

    generator_prompt += "\n\nReturn ONLY the JSON object."

    try:
        raw_gen = await llm.chat(
            system=generator_personality,
            messages=[{"role": "user", "content": generator_prompt}],
            max_tokens=1200,
            model="gpt-4.1",
        )
        raw_gen = raw_gen.strip()
        if raw_gen.startswith("```"):
            raw_gen = raw_gen.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    except Exception as e:
        logger.error("rereact_generator_failed", error=str(e))
        # BUG-18: Return valid empty structure instead of None
        return {"validated_recommendations": [], "rejected": [], "validation_summary": "Generator unavailable"}

    # === AGENT 2: ANALYZER ===
    analyzer_personality = _load_personality("analytics") + "\n\n" + _load_personality("compliance")

    analyzer_prompt = f"""You are a senior ESG analyst stress-testing recommendations for {company_name} ({market_cap or 'Unknown'} cap, {listing_exchange or 'Unknown'}).

TODAY: {today}. All deadlines must be AFTER {today}.

GENERATOR OUTPUT:
{raw_gen}

ARTICLE: "{article_title}"
FRAMEWORKS: {fw_list}

QUANTITATIVE STRESS TEST:
1. Does the budget make sense for a {market_cap or 'Unknown'} company? (Large Cap: ₹10-100 Cr, Mid: ₹1-10 Cr, Small: ₹10L-1 Cr)
2. Is the timeline realistic? (forensic audit = 3-6 months, board restructuring = 6-12 months, not 2 weeks)
3. Does EVERY profitability_link have SPECIFIC NUMBERS (₹, %, bps)?
   - REJECT if generic: "reduces risk" or "improves ESG score"
   - REQUIRE: "saves ₹X Cr annually" or "reduces CoC by Y bps" or "avoids ₹Z Cr penalty"
4. Does the framework code match the recommendation? (BRSR:P6 = environmental, not governance)
5. Is the responsible_party the RIGHT person? (CRO for risk, CFO for finance, not generic "CEO")
6. Does each recommendation reference SPECIFIC data from the article? (amounts, names, events)
7. Is EVERY deadline after {today}? Fix any that aren't.

Remove weak recommendations. Quantify vague profitability links. Fix wrong framework codes.

Return the IMPROVED JSON (same recommendations array structure)."""

    try:
        raw_analyzed = await llm.chat(
            system=analyzer_personality,
            messages=[{"role": "user", "content": analyzer_prompt}],
            max_tokens=1000,
            model="gpt-4.1-mini",
        )
        raw_analyzed = raw_analyzed.strip()
        if raw_analyzed.startswith("```"):
            raw_analyzed = raw_analyzed.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    except Exception as e:
        logger.error("rereact_analyzer_failed", error=str(e))
        raw_analyzed = raw_gen  # Fall back to generator output

    # === AGENT 3: VALIDATOR ===
    validator_personality = _load_personality("validator")

    validator_prompt = f"""You are an independent ESG recommendation validator. Critically validate these recommendations.

RECOMMENDATIONS TO VALIDATE:
{raw_analyzed}

ARTICLE: "{article_title}"
COMPANY: {company_name}

TODAY: {today}. The year is {today[:4]}.

For each recommendation, check ALL of these:
1. Is data grounding solid? (does it reference verifiable facts from the article?)
2. Is it actionable within the stated timeline?
3. Is the confidence level appropriate?
4. Are there any hallucinated facts or fabricated figures?
5. Does it name a SPECIFIC responsible party (not "management" or "the company")?
6. Does it include a framework:section code (e.g., BRSR:P1, GRI:205-2)?
7. Is the deadline a YYYY-MM-DD date AFTER {today}? REJECT if before {today[:4]}.
8. Does it have a measurable success criterion?
9. Does the description use specific action verbs (not "enhance", "strengthen")?
10. Does profitability_link contain SPECIFIC NUMBERS (₹, %, bps)? If generic, mark LOW confidence.

REJECT any recommendation that:
- Has a deadline before {today}
- Uses vague language ("enhance governance", "strengthen controls")
- Lacks a named responsible party or framework code
- Has a profitability_link without numbers (₹, %, bps)

For surviving recommendations, assign confidence: HIGH/MEDIUM/LOW.

Return JSON:
{{
  "validated_recommendations": [
    {{
      "type": "...",
      "title": "...",
      "responsible_party": "...",
      "description": "...",
      "framework_section": "...",
      "deadline": "...",
      "estimated_budget": "...",
      "success_criterion": "...",
      "urgency": "...",
      "confidence": "HIGH|MEDIUM|LOW",
      "profitability_link": "...",
      "validation_notes": "Why this passed validation"
    }}
  ],
  "rejected": ["Titles of rejected recommendations with reason"],
  "validation_summary": "Overall assessment in 1-2 sentences"
}}"""

    try:
        raw_validated = await llm.chat(
            system=validator_personality or "You are an independent ESG recommendation validator.",
            messages=[{"role": "user", "content": validator_prompt}],
            max_tokens=1200,
            model="gpt-4.1-mini",
        )
        raw_validated = raw_validated.strip()
        if raw_validated.startswith("```"):
            raw_validated = raw_validated.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_validated)
        # BUG-19: Validate required keys exist in parsed JSON
        if not isinstance(result, dict) or "validated_recommendations" not in result:
            result = {"validated_recommendations": [], "rejected": [], "validation_summary": "Invalid validator response"}
        # Post-process: fix any remaining past dates and flag weak profitability links
        result = _post_process_recommendations(result, today)

        # Generate suggested questions (Phase 5B)
        top_risks = deep_insight.get("risk_matrix", {}).get("top_risks", [])
        top_risk_name = top_risks[0].get("category_name", "risk") if top_risks else "risk"
        result["suggested_questions"] = [
            f"What's the total cost of inaction on {top_risk_name}?",
            "Which recommendation has the highest ROI?",
            f"How do these risks compare to {company_name}'s industry peers?",
        ]

        logger.info(
            "rereact_completed",
            article=article_title[:50],
            recommendations=len(result.get("validated_recommendations", [])),
            rejected=len(result.get("rejected", [])),
        )
        return result
    except Exception as e:
        logger.error("rereact_validator_failed", error=str(e))
        # Fall back to analyzed recommendations without validation
        try:
            recs = json.loads(raw_analyzed)
            return {"validated_recommendations": recs if isinstance(recs, list) else [], "rejected": [], "validation_summary": "Validation failed — showing unvalidated recommendations"}
        except Exception:
            return None
