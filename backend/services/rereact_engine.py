"""REREACT 3-Agent Recommendation Validation Engine (Phase 3).

Pipeline: Generator → Analyzer → Validator

Agent 1 (Generator): Produces 3-5 recommendations using specialist personality
Agent 2 (Analyzer): Evaluates logical consistency, ESG-materiality, financial realism
Agent 3 (Validator): Independent critic — checks confidence, hallucinations, actionability
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

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    generator_prompt = f"""You are analyzing an ESG news event for {company_name}. Produce a 6-dimension impact analysis followed by 3-5 actionable recommendations.

TODAY'S DATE: {today}
ALL DEADLINES MUST BE AFTER {today}. Never use dates before {today[:4]}.

ARTICLE: "{article_title}"
COMPANY: {company_name}
COMPETITORS: {comp_list}
FRAMEWORKS: {fw_list}

DEEP INSIGHT:
{insight_summary}

Return a JSON object with exactly this structure:
{{
  "impact_analysis": {{
    "esg_positioning": "How this shifts the company's relative ESG attractiveness (1-2 sentences)",
    "capital_allocation": "Effect on institutional capital flows, cost of capital, equity risk premium (1-2 sentences)",
    "valuation_cashflow": "Impact on P/E, EV/EBITDA, margin structure, demand effects (1-2 sentences)",
    "compliance_regulatory": "Specific frameworks triggered or modified, disclosure obligations (1-2 sentences)",
    "supply_chain_transmission": "Tier 1/2/3 impact pathway, amplification or dampening (1-2 sentences)",
    "demand_macro": "Second-order effects on consumer demand, market sizing (1-2 sentences)"
  }},
  "recommendations": [
    {{
      "type": "strategic|financial|esg_positioning|operational|compliance",
      "title": "Action-oriented title (10 words max)",
      "responsible_party": "Specific role (e.g., 'Chief Risk Officer', 'Audit Committee', 'Head of Sustainability')",
      "description": "Specific steps with timeline (40 words max)",
      "framework_section": "Exact framework:section code (e.g., BRSR:P1:Q5, GRI:205-2, ESRS:G1-3, IFRS:S2:para18)",
      "deadline": "MUST be after {today}. Use format YYYY-MM-DD (e.g., '{today[:4]}-09-30'). NEVER use dates before {today[:4]}. NEVER use 'Q2' or 'short_term'.",
      "estimated_budget": "Budget range if applicable (e.g., '₹2-5 Cr for forensic audit') or 'Internal resources only'",
      "success_criterion": "Measurable outcome (e.g., 'Zero material findings in next BRSR assurance', 'Green portfolio reaches 7% by FY27')",
      "urgency": "immediate|short_term|ongoing",
      "estimated_impact": "High/Medium/Low"
    }}
  ]
}}

RECOMMENDATION LANGUAGE RULES:
- DO NOT use vague verbs: "enhance", "strengthen", "improve", "develop", "bolster", "foster"
- USE specific verbs: "commission", "file", "appoint", "allocate", "disclose", "audit", "terminate", "reclassify", "ring-fence"
- Every recommendation MUST name the responsible party, a framework section code, and a calendar deadline
- Recommendations without measurable success criteria will be rejected by the Validator agent

PEER BENCHMARKING: Recommendations MUST reference specific competitor actions when available.
Instead of generic advice, cite what named competitors are doing and frame the recommendation
as closing or extending the gap. Example: "SBI targets 10% green portfolio by 2030 — allocate
₹500 Cr to green lending to close the current 7% gap by FY28."

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
            max_tokens=800,
            model="gpt-4o",
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

    analyzer_prompt = f"""You are a senior ESG analyst stress-testing recommendations for {company_name}.

GENERATOR OUTPUT:
{raw_gen}

ARTICLE: "{article_title}"
FRAMEWORKS: {fw_list}

Perform a rigorous review:

1. VERIFY CAUSAL CHAINS: Is each impact pathway logically sound? Are there leaps in reasoning?
2. CHECK MISSING DIMENSIONS: Are there second-order effects missed? Overlooked stakeholders or geographies?
3. VALIDATE PROPORTIONALITY: Is the assessed impact magnitude appropriate? Flag overstatement or understatement.
4. ENRICH WITH CONTEXT: Add relevant precedents, comparable events, sector benchmarks, or regulatory timelines.
5. CHALLENGE FRAMING: If the analysis conflates direct vs indirect impact, correct it.
6. REFINE RECOMMENDATIONS: Make them more specific, time-bound, and role-appropriate.

If the generator output contains "impact_analysis", review it for accuracy.
Improve weak recommendations. Remove any that lack substance.

Return the IMPROVED JSON (same structure as input — recommendations array, optionally with impact_analysis)."""

    try:
        raw_analyzed = await llm.chat(
            system=analyzer_personality,
            messages=[{"role": "user", "content": analyzer_prompt}],
            max_tokens=800,
            model="gpt-4o",
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

For each recommendation, check ALL of these:
1. Is data grounding solid? (does it reference verifiable facts from the article?)
2. Is it actionable within the stated timeline?
3. Is the confidence level appropriate?
4. Are there any hallucinated facts or fabricated figures?
5. Does it name a SPECIFIC responsible party (not "management" or "the company")?
6. Does it include a framework:section code (e.g., BRSR:P1, GRI:205-2)?
7. Does it have an absolute calendar deadline (not "Q2" or "short_term")?
8. Does it have a measurable success criterion?
9. Does the description use specific action verbs (not "enhance", "strengthen", "improve")?

REJECT any recommendation that:
- Uses vague language ("enhance governance", "strengthen controls")
- Lacks a named responsible party
- Lacks a framework section code
- Has no measurable success criterion

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
            max_tokens=1000,
            model="gpt-4o",
        )
        raw_validated = raw_validated.strip()
        if raw_validated.startswith("```"):
            raw_validated = raw_validated.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_validated)
        # BUG-19: Validate required keys exist in parsed JSON
        if not isinstance(result, dict) or "validated_recommendations" not in result:
            result = {"validated_recommendations": [], "rejected": [], "validation_summary": "Invalid validator response"}
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
