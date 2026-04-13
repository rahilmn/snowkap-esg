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


def _post_process_recommendations(result: dict, today_str: str, user_role: str | None = None) -> dict:
    """Fix past dates and flag weak profitability links — safety net after LLM output."""
    try:
        today = date.fromisoformat(today_str)
    except (ValueError, TypeError):
        today = datetime.now(timezone.utc).date()

    # ESG analyst recs use score/rating metrics — broader acceptable pattern
    _is_analyst = user_role in ("data_entry_analyst",)
    _profitability_pattern = (
        r"[₹$%]|\d+\s*(Cr|Lakh|bps|crore|pts?|point|score|tier|rank|index|coverage)",
        re.IGNORECASE,
    ) if _is_analyst else (
        r"[₹$%]|\d+\s*(Cr|Lakh|bps|crore)",
        re.IGNORECASE,
    )

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
        if pl and not re.search(_profitability_pattern[0], str(pl), _profitability_pattern[1]):
            suffix = f" [Quantify: ESG score/rating impact for {today.year}]" if _is_analyst else f" [Quantify: estimate ₹ impact for {today.year}]"
            rec["profitability_link"] = pl + suffix

        # ROI and payback computation (Phase 5D)
        # Formula: annualized return rate = (annual_benefit / one_time_investment) × 100
        # Example: invest ₹2 Cr once, save ₹2 Cr/yr → 100% annual return, 12-month payback
        # Example: invest ₹5 Cr once, save ₹15 Cr/yr → 300% annual return, 4-month payback
        budget_str = rec.get("estimated_budget", "")
        profit_str = rec.get("profitability_link", "")
        budget_num = None
        profit_num = None
        # Extract the LARGEST numeric value in budget (handles "₹10-20 Cr" ranges by taking lower bound)
        budget_matches = re.findall(r"[₹$]\s*(\d+(?:\.\d+)?)", str(budget_str))
        if budget_matches:
            budget_num = float(budget_matches[0])  # first = lower bound of range
        # Extract the LARGEST numeric benefit from profitability_link
        profit_matches = re.findall(r"[₹$]\s*(\d+(?:\.\d+)?)", str(profit_str))
        if profit_matches:
            # Take max value — profitability_link often says "saves ₹10-20 Cr", take higher end
            profit_num = max(float(m) for m in profit_matches)
        if budget_num and profit_num and budget_num > 0:
            # Annualized ROI: how much annual benefit per unit invested (100% = full cost recovery each year)
            raw_roi = round((profit_num / budget_num) * 100, 1)
            # ROI sanity cap: disclosure/audit tasks rarely exceed 200%, operational max 500%
            rec_type = rec.get("type", "")
            if rec_type in ("esg_positioning", "compliance") and raw_roi > 200:
                raw_roi = 200.0
            elif raw_roi > 500:
                raw_roi = 500.0
            rec["roi_percentage"] = raw_roi
            rec["payback_months"] = round((budget_num / profit_num) * 12, 1)
        elif not budget_num and profit_str:
            # Budget unknown but benefit mentioned — flag as high ROI (low cost, meaningful benefit)
            rec["roi_percentage"] = None
            rec["roi_type"] = "non_financial"

        # Priority derivation (Phase 5E)
        urgency = rec.get("urgency", "")
        impact = rec.get("estimated_impact", "")
        rec_type = rec.get("type", "")
        if urgency == "immediate" or impact == "High":
            rec["priority"] = "CRITICAL"
        elif urgency == "short_term" or impact == "Medium":
            rec["priority"] = "HIGH"
        else:
            rec["priority"] = "MEDIUM"

        # Risk of inaction score (1-10): combines priority + type + ROI magnitude + keyword signals
        # CRITICAL priority → base 7; HIGH → base 5; MEDIUM → base 3
        base_inaction = {"CRITICAL": 7, "HIGH": 5, "MEDIUM": 3}.get(rec.get("priority", "MEDIUM"), 3)

        # Type-based boost: compliance/regulatory failures carry structural penalty risk
        if rec_type == "compliance":
            base_inaction = min(10, base_inaction + 2)
        elif rec_type == "esg_positioning":
            base_inaction = min(10, base_inaction + 1)

        # Keyword signal: explicit penalty / enforcement language → higher urgency
        pl_lower = rec.get("profitability_link", "").lower()
        if any(kw in pl_lower for kw in ["penalty", "fine", "enforcement", "notice", "litigation", "recall"]):
            base_inaction = min(10, base_inaction + 1)

        # ROI-magnitude boost: high foregone return = high cost of inaction
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
    revenue: float | None = None,
    industry: str | None = None,
) -> dict | None:
    """3-agent REREACT recommendation pipeline.

    Returns validated recommendations or None on failure.
    Respects materiality gate: LOW/NON-MATERIAL articles with MONITOR/IGNORE
    signal get a "no action" response instead of forced recommendations.
    """
    if not llm.is_configured():
        return None

    # ── MATERIALITY GATE ──────────────────────────────────────────────────
    # Core ESG principle: materiality drives action.
    # Rule: if the article doesn't warrant active response, return "do nothing."
    # Rule: MONITOR articles with macro/sentiment drivers → no compliance actions.
    decision = deep_insight.get("decision_summary", {})
    materiality = (decision.get("materiality") or "").upper()
    action_signal = (decision.get("action") or "").upper()
    impact_score = deep_insight.get("impact_score", 0) or 0

    # Classify signal type from content_type and deep_insight
    _macro_sentiment_types = {"narrative", "data_release", "financial"}
    _operational_types = {"regulatory", "operational", "reputational", "technical"}
    _is_macro_signal = (content_type or "") in _macro_sentiment_types
    # Also check core_mechanism for macro keywords
    _core = (deep_insight.get("core_mechanism") or "").lower()
    if any(kw in _core for kw in [
        "geopolit", "ceasefire", "crude oil", "oil price", "brent crude", "wti crude",
        "tariff war", "trade war", "equity market rally", "stock market rally",
        "commodity price surge", "commodity market", "gold price", "silver price",
        "nifty", "sensex", "fed rate", "rbi rate",
    ]):
        _is_macro_signal = True

    _skip_recs = False
    _skip_reason = ""

    # Gate 1: Explicit low materiality + no-action
    if materiality in ("NON-MATERIAL", "LOW") and action_signal in ("IGNORE", "MONITOR"):
        _skip_recs = True
        _skip_reason = f"Materiality={materiality}, Action={action_signal}"
    # Gate 2: Impact score is noise
    elif impact_score <= 2 and action_signal != "ACT":
        _skip_recs = True
        _skip_reason = f"Impact score {impact_score}/10 (noise level)"
    # Gate 3: MONITOR signal + macro/sentiment driver → no action needed
    # ESG principle: macro sentiment shifts don't trigger compliance/disclosure actions
    elif action_signal == "MONITOR" and _is_macro_signal:
        _skip_recs = True
        _skip_reason = f"Action=MONITOR with macro/sentiment signal (content_type={content_type})"
    # Gate 4: Any materiality level but IGNORE signal → definitely skip
    elif action_signal == "IGNORE":
        _skip_recs = True
        _skip_reason = f"Action=IGNORE"

    if _skip_recs:
        logger.info("rereact_materiality_gate_skip", reason=_skip_reason,
                     article=article_title[:60], company=company_name)
        return {
            "validated_recommendations": [],
            "rejected": [],
            "validation_summary": (
                f"No recommendations generated — {_skip_reason}. "
                f"Include in quarterly monitoring only. "
                f"Materiality does not warrant active response."
            ),
            "materiality_gate": True,
        }

    article_text = article_content[:2500] if article_content else article_title
    fw_list = ", ".join(frameworks[:5]) if frameworks else "ESG frameworks"
    insight_summary = json.dumps({
        "headline": deep_insight.get("headline", ""),
        "core_mechanism": deep_insight.get("core_mechanism", ""),
        "impact_score": deep_insight.get("impact_score", 0),
        "risk_mapping": deep_insight.get("risk_mapping", {}),
    }, indent=2)

    # === AGENT 1: GENERATOR (gpt-4.1-mini — fast, quality held by combined Agent 2) ===
    agent_key = CONTENT_TYPE_TO_AGENT.get(content_type or "", "executive")
    generator_personality = _load_personality(agent_key)

    comp_list = ", ".join(competitors[:5]) if competitors else "industry peers"

    from datetime import datetime, timezone, date as _date_type
    today = datetime.now(timezone.utc).date().isoformat()
    max_date = (datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year + 2)).isoformat()

    # Guard: coerce empty strings to None
    if revenue is not None and not isinstance(revenue, (int, float)):
        try:
            revenue = float(revenue) if revenue else None
        except (ValueError, TypeError):
            revenue = None

    # Get company context
    _mc = market_cap or "Unknown"
    _le = listing_exchange or "Unknown"
    _hc = headquarter_country or "Unknown"
    _rev = f"₹{revenue:,.0f} Cr" if revenue else "Unknown"
    _ind = industry or "Unknown"

    generator_prompt = f"""You are analyzing an ESG news event for {company_name}. Produce actionable recommendations.

╔══════════════════════════════════════════════════╗
║  ⚠️  CRITICAL DATE RULE — READ BEFORE ANYTHING   ║
║  Today is {today}. The year is {today[:4]}.              ║
║  EVERY deadline MUST be between {today} and {max_date}. ║
║  Dates before {today[:4]} will CRASH the system.         ║
║  Safe default: {today[:4]}-12-31                        ║
╚══════════════════════════════════════════════════╝

COMPANY PROFILE:
- {company_name} | {_mc} | Revenue: {_rev} | Industry: {_ind} | Listed: {_le} | HQ: {_hc}
- Budget calibration: {"₹10-100 Cr, board-level owners" if "Large" in _mc else "₹1-10 Cr, CXO-level owners" if "Mid" in _mc else "₹10L-1 Cr, department heads"}

ARTICLE: "{article_title}"
COMPETITORS: {comp_list}
FRAMEWORKS: {fw_list}

DECISION SIGNAL FROM DEEP INSIGHT:
- Materiality: {materiality or 'UNKNOWN'}
- Action: {action_signal or 'UNKNOWN'}
- Verdict: {decision.get('verdict', 'N/A')}
⚠️  CRITICAL: Your recommendations MUST be consistent with the decision signal above.
  - If Action=MONITOR → recommendations should be 'short_term' or 'ongoing', NOT 'immediate'
  - If Materiality=MODERATE → limit to 2-3 recommendations, no CRITICAL urgency
  - If the verdict says "No action needed" → you may return ZERO recommendations (empty array)
  - NEVER contradict the decision signal with inflated urgency or artificial compliance actions

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

ANTI-FABRICATION RULES (CRITICAL — violations will cause rejection):
- Do NOT mention ANY company, person, or organisation name that does not appear in the ARTICLE text above.
- Do NOT invent competitor actions. Only reference competitors if their names appear in the article.
- If no specific competitors are mentioned, say "industry peers" — NEVER fabricate names like "notably X and Y".
- Every ₹ figure in profitability_link must be derivable from article data or the company profile above. Do not invent amounts.
- Do NOT reference specific regulatory penalties unless the article explicitly mentions them.

"""

    # Inject industry-specific recommendation constraints (applies to any company with a populated industry field)
    _industry_constraints = {
        "Financials": (
            "INDUSTRY CONSTRAINT — BANKING / FINANCIAL SERVICES:\n"
            "- For commodity/energy price articles: recommend CREDIT RISK REVIEW of sectoral lending book, "
            "STRESS-TEST of oil & gas / energy exposure, and SECTORAL EXPOSURE DISCLOSURE — NOT commodity hedging.\n"
            "- Banks do NOT hedge commodity positions directly. Their exposure is through LENDING.\n"
            "- For regulatory articles: recommend COMPLIANCE FILING and DISCLOSURE updates.\n"
            "- Budget must reflect advisory/audit costs (₹1-5 Cr), NOT capital expenditure.\n"
            "- Responsible parties: CRO for credit risk, CFO for exposure, CCO for compliance, Head of ESG for disclosures.\n"
            "- Framework codes: use TCFD:Risk_Management or GRI:201-2 for financial risk — NEVER GRI:305 for price/market risk.\n"
        ),
        "Infrastructure": (
            "INDUSTRY CONSTRAINT — INFRASTRUCTURE / POWER:\n"
            "- For energy articles: recommend FUEL MIX REVIEW, CAPEX REALLOCATION toward cleaner sources, "
            "and TRANSITION PATHWAY PLANNING.\n"
            "- For regulatory articles: recommend EMISSION REDUCTION targets and COMPLIANCE ROADMAPS.\n"
            "- Budget should reflect operational capex (₹10-100 Cr for Large Cap).\n"
        ),
        "Renewable Resources": (
            "INDUSTRY CONSTRAINT — RENEWABLE ENERGY:\n"
            "- For energy articles: recommend MARKET POSITIONING analysis, ORDER BOOK impact assessment, "
            "and POLICY ADVOCACY.\n"
            "- For supply chain articles: recommend COMPONENT SOURCING DIVERSIFICATION.\n"
            "- Frame opportunities positively — renewables benefit from fossil fuel disruption.\n"
        ),
    }
    _ind_constraint = ""
    if industry:
        for key, constraint in _industry_constraints.items():
            if key.lower() in industry.lower() or industry.lower() in key.lower():
                _ind_constraint = constraint
                break

    generator_prompt += f"""{_ind_constraint}

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

PEER BENCHMARKING: Only reference competitors if their names appear in the ARTICLE text. Otherwise say "industry peers".

FRAMEWORK CITATION GUIDE — match the article topic to the MOST SPECIFIC section:
┌──────────────────────────────────────────┬──────────────────────────────────────────────────────────┐
│ Article Topic                            │ Correct framework_section                                │
├──────────────────────────────────────────┼──────────────────────────────────────────────────────────┤
│ Fraud / corruption / bribery             │ BRSR:P1:Q5 (ethics & penalties) + GRI:205-2              │
│ Employee safety / LTIFR / accidents      │ BRSR:P3:Q18 (safety incidents) + GRI:403-9               │
│ Parental leave / HR benefits             │ BRSR:P3:Q14 (parental leave retention)                   │
│ Worker rights / wages / unions           │ BRSR:P5:Q26 (minimum wages) or BRSR:P5:Q29              │
│ GHG / carbon emissions / net-zero        │ BRSR:P6:Q38 (Scope 1&2) or BRSR:P6:Q48 (Scope 3)        │
│ Water / effluent / discharge             │ BRSR:P6:Q34 / Q36 (withdrawal & discharge)               │
│ Waste / pollution / plastic              │ BRSR:P6:Q41 / Q47 (waste & compliance notices)           │
│ Energy / PAT / renewable targets         │ BRSR:P6:Q31 / Q33 (energy & PAT)                        │
│ Consumer complaints / product quality    │ BRSR:P9:Q61 (consumer complaints) + GRI:416-1            │
│ Cybersecurity / data privacy / IT risk   │ BRSR:P9:Q63 + GRI:418-1 (data & customer privacy)       │
│ Trade finance / policy advocacy          │ BRSR:P7:Q50 (policy advocacy positions)                  │
│ CSR / community investment               │ BRSR:P8:Q54 / Q59 (CSR projects & spend)                 │
│ Sector / market / macroeconomic risk     │ GRI:3-3 + GRI:201-2 (material risk mgmt & implications)  │
│ IT sector / tech disruption / fintech    │ GRI:3-3 + BRSR:P9:Q63 (material IT topics)               │
│ Material topic mgmt / risk disclosure    │ GRI:3-3 (management of material topics)                  │
│ Financial risk / capital markets risk    │ GRI:201-2 (financial implications & opportunities)        │
│ Materiality matrix / ESG gap analysis    │ GRI:3-1 + GRI:3-2 (materiality determination & topics)   │
│ Strategy statement / sustainability rpt  │ GRI:2-22 (ONLY: governance body strategy statement)       │
│ Annual report / integrated report        │ GRI:2-14 + BRSR:SectionA (oversight & disclosures)        │
│ Climate risk / TCFD alignment            │ TCFD:Strategy or TCFD:Risk_Management + IFRS:S2           │
│ Scope 3 / supply chain emissions         │ BRSR:P6:Q48 + GRI:305-3                                  │
│ Banking / credit / lending ESG           │ SASB:FN-CB-410a.2 (ESG integration in credit decisions)  │
│ Fintech / digital banking / NBFC risk    │ SASB:FN-CB-230a.1 + BRSR:P9:Q63                          │
│ Capital raise / QIP / bond issuance      │ SEBI:LODR:Reg30 + GRI:2-14 (material disclosure)         │
│ Energy price / commodity risk (banks)    │ TCFD:Risk_Management + GRI:201-2 (NOT GRI:305)            │
│ Credit risk / loan book / lending        │ SASB:FN-CB-410a.2 + BRSR:P2 (credit risk mgmt)           │
│ Sectoral lending / portfolio exposure    │ GRI:201-2 + TCFD:Strategy (portfolio risk assessment)     │
│ Oil & gas price / crude / petroleum      │ TCFD:Risk_Management + GRI:201-2 (financial risk)         │
│ Biodiversity / land use / TNFD           │ GRI:304-2 + TNFD:Exposure (biodiversity impacts)         │
│ Human rights / community impact          │ GRI:411-1 + BRSR:P5 (human rights due diligence)          │
│ Regulatory compliance notice / penalty   │ BRSR:P1:Q5 + applicable framework penalty section        │
└──────────────────────────────────────────┴──────────────────────────────────────────────────────────┘
RULE: If the topic is NOT in this table, use GRI:3-3 (management of material topics) as default.
CRITICAL: GRI:2-22 is ONLY for a governance body's formal sustainability strategy statement.
  DO NOT use GRI:2-22 for: risk disclosure, sector analysis, market risk, or general ESG reporting.
  For those, use GRI:3-3 + GRI:201-2 instead.
CRITICAL: GRI:305 (GHG Emissions) is ONLY for articles about actual greenhouse gas emissions.
  DO NOT use GRI:305 for: energy PRICES, oil MARKET movements, commodity TRADING, or financial risk.
  For energy price/commodity articles, use TCFD:Risk_Management + GRI:201-2.
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
            "data_entry_analyst": (
                "CRITICAL — TARGET AUDIENCE: ESG Analyst (strategist-level, not data entry)\n"
                "\n"
                "STEP ZERO — CLASSIFY THE ARTICLE SIGNAL TYPE:\n"
                "  A) COMPLIANCE TRIGGER: new regulation, penalty, notice, mandate → focus on disclosure/filing gaps\n"
                "  B) MARKET SIGNAL: transition trend, demand shift, commodity move, sector development → focus on\n"
                "     system constraints, strategic positioning, and ESG score implications\n"
                "  C) INCIDENT: accident, breach, scandal → focus on risk response and disclosure obligations\n"
                "\n"
                "FOR MARKET SIGNALS (type B) — the MOST COMMON case:\n"
                "- DO NOT default to 'file this disclosure' — ask 'what real-world system constraint\n"
                "  determines whether the company can CAPTURE this signal?'\n"
                "- Include at least 1 recommendation on SYSTEM CONSTRAINTS:\n"
                "  Grid capacity, transmission bottlenecks, storage infra, supply chain readiness,\n"
                "  DISCOM economics, policy incentives, workforce readiness\n"
                "- Include at least 1 recommendation on STRATEGIC POSITIONING:\n"
                "  Market share capture, capex alignment, technology readiness, competitive moat\n"
                "- Framework citation is SECONDARY — cite only when genuinely triggered\n"
                "\n"
                "FOR COMPLIANCE TRIGGERS (type A):\n"
                "- Lead with exact framework:section codes triggered\n"
                "- Include disclosure deadline and current gap\n"
                "- Benchmark vs sector peers\n"
                "\n"
                "GENERAL RULES:\n"
                "- Type field: use 'esg_positioning' for strategic, 'compliance' only for actual compliance gaps,\n"
                "  'operational' for system constraint actions\n"
                "- profitability_link: can use ESG score pts, index coverage %, rating tier, ₹ amounts, or bps\n"
                "- URGENCY MUST match the decision signal: if the article is 'MONITOR' with no immediate action,\n"
                "  recommendations should be 'short_term' or 'ongoing' — NOT 'immediate'\n"
                "- Include infrastructure/system readiness analysis (grid, storage, supply chain, workforce)\n"
                "- Include at least 1 recommendation that asks 'Can the company actually capture this?'\n"
                "  (e.g., 'Audit renewable capacity vs projected EV-driven demand gap by Q3')\n"
                "- NEVER give financial hedging or capital allocation advice — stay in ESG/operational domain\n"
                "- Avoid over-indexing on tangential risks (e.g., don't cite 'Chinese investment restrictions'\n"
                "  as a risk for an EV growth article unless it's the core story)"
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
            model="gpt-4.1-mini",  # downgraded from gpt-4.1 — quality held by combined analyzer+validator
        )
        raw_gen = raw_gen.strip()
        if raw_gen.startswith("```"):
            raw_gen = raw_gen.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    except Exception as e:
        logger.error("rereact_generator_failed", error=str(e))
        # BUG-18: Return valid empty structure instead of None
        return {"validated_recommendations": [], "rejected": [], "validation_summary": "Generator unavailable"}

    # === AGENT 2: COMBINED ANALYZE + VALIDATE (single call, saves one full round trip) ===
    # Merges the old separate Analyzer and Validator into one prompt.
    # gpt-4.1-mini handles both stress-testing AND confidence assignment in one pass.
    combined_personality = _load_personality("analytics") + "\n\n" + _load_personality("validator")

    # Role-aware validator context: ESG analyst uses score/rating metrics, not ₹ amounts
    _is_analyst_role = user_role in ("data_entry_analyst",)
    _profitability_rule = (
        "profitability_link has no measurable outcome (ESG score pts, index coverage %, rating impact, capacity gap, or ₹/bps)"
        if _is_analyst_role else
        "profitability_link has no numbers (₹, %, bps)"
    )
    _profitability_stress = (
        "3. Every profitability_link references a MEASURABLE outcome (ESG score points, index inclusion %, "
        "rating tier change, capacity gap MW/GW, market share %, peer rank, or ₹/bps)? Quantify any that don't."
        if _is_analyst_role else
        "3. Every profitability_link has SPECIFIC NUMBERS (₹, %, bps)? Quantify any that don't."
    )
    _profitability_field_hint = (
        "measurable outcome: ESG score, capacity gap, market share %, rating tier, or ₹/bps"
        if _is_analyst_role else
        "must contain ₹/% numbers"
    )
    _responsible_role_hint = (
        "CSO/ESG Head for strategy, VP Operations for system constraints, ESG Analyst for benchmarking"
        if _is_analyst_role else
        "CRO for risk, CFO for finance, CISO for cyber, CSO for ESG"
    )

    # ESG analyst validator: extra rules for signal-type awareness
    _analyst_extra_rules = ""
    if _is_analyst_role:
        _analyst_extra_rules = """
ANALYST-SPECIFIC VALIDATION (CRITICAL):
8. SIGNAL TYPE CHECK: Is the article a MARKET SIGNAL (trend/demand shift) or COMPLIANCE TRIGGER (regulation/penalty)?
   - For MARKET SIGNALS: at least 1 recommendation MUST address SYSTEM CONSTRAINTS (grid capacity, infra readiness,
     supply chain bottleneck, workforce gap, technology readiness). Pure disclosure recs are INSUFFICIENT.
   - For MARKET SIGNALS: at least 1 recommendation MUST ask "Can the company actually capture this opportunity?"
   - REJECT recommendations that are pure "file this disclosure" for market signal articles.
9. URGENCY CONSISTENCY: If the article's decision signal is "MONITOR" (no immediate action needed),
   recommendations MUST use 'short_term' or 'ongoing' urgency — NOT 'immediate'.
10. NOISE FILTER: REJECT recommendations that cite tangential risks not central to the article.
    (e.g., "Chinese investment restrictions" in an EV growth article — unless that IS the core story)
11. REJECT pure compliance recs if they could apply to ANY article for ANY company (generic "update BRSR" recs).
"""

    combined_prompt = f"""You are a senior ESG analyst AND independent validator for {company_name} ({market_cap or 'Unknown'} cap, {listing_exchange or 'Unknown'}).
In ONE pass: stress-test the generator's recommendations, fix weak ones, reject bad ones, assign confidence.

TODAY: {today}. All deadlines MUST be after {today}.

GENERATOR OUTPUT:
{raw_gen}

ARTICLE: "{article_title}"
FRAMEWORKS: {fw_list}

STEP 1 — STRESS TEST (fix in-place, do not output):
1. Budget realistic for {market_cap or 'Unknown'} company? (Large: ₹10-100 Cr, Mid: ₹1-10 Cr, Small: ₹10L-1 Cr)
2. Timeline realistic? (forensic audit = 3-6 months, not 2 weeks)
{_profitability_stress}
4. Framework code is PRECISE and CORRECT for this topic:
   - GRI:2-22 → ONLY for governance body's formal sustainability strategy statement. NOT for risk disclosure, sector analysis, or market risk. Replace with GRI:3-3 + GRI:201-2 for risk/sector topics.
   - GRI:3-3 → management of material topics (correct for risk disclosure, sector risk, IT risk)
   - GRI:201-2 → financial implications of risks and opportunities (financial risk disclosure)
   - BRSR:P9:Q63 + GRI:418-1 → cybersecurity/data privacy/IT risk (not GRI:2-22)
   - BRSR:P6 → environment ONLY, never governance or IT
   - TCFD:Strategy or TCFD:Risk_Management → climate risk and scenario analysis
5. Responsible party is the RIGHT role? ({_responsible_role_hint})
6. Each recommendation references SPECIFIC article data (amounts, names, events)?
7. Every deadline is after {today}? Fix any past dates to {today[:4]}-12-31.
{_analyst_extra_rules}
STEP 2 — REJECT if ANY of:
- Deadline before {today}
- Vague language ("enhance governance", "strengthen controls")
- Missing named responsible party or framework code
- {_profitability_rule}
- References company/person names NOT found in the ARTICLE text (hallucination — reject immediately)
- Profitability_link ₹ figures wildly disproportionate to company size (e.g., ₹10,000 Cr for a Mid Cap)

STEP 3 — OUTPUT the surviving, fixed recommendations as JSON:
{{
  "validated_recommendations": [
    {{
      "type": "strategic|financial|esg_positioning|operational|compliance",
      "title": "...",
      "responsible_party": "Specific named role",
      "description": "...",
      "framework_section": "exact code e.g. BRSR:P6:Q38",
      "deadline": "YYYY-MM-DD after {today}",
      "estimated_budget": "...",
      "success_criterion": "measurable outcome with numbers",
      "urgency": "immediate|short_term|ongoing",
      "confidence": "HIGH|MEDIUM|LOW",
      "profitability_link": "{_profitability_field_hint}",
      "validation_notes": "1 sentence why this passed"
    }}
  ],
  "rejected": ["Title — reason for rejection"],
  "validation_summary": "1-2 sentence overall assessment"
}}"""

    try:
        raw_validated = await llm.chat(
            system=combined_personality or "You are a senior ESG analyst and validator.",
            messages=[{"role": "user", "content": combined_prompt}],
            max_tokens=1400,
            model="gpt-4.1-mini",
            timeout=llm.LONG_TIMEOUT,
        )
        raw_validated = raw_validated.strip()
        if raw_validated.startswith("```"):
            raw_validated = raw_validated.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw_validated)
        if not isinstance(result, dict) or "validated_recommendations" not in result:
            result = {"validated_recommendations": [], "rejected": [], "validation_summary": "Invalid validator response"}
        # Post-process: fix remaining past dates, compute ROI/payback, assign priority
        result = _post_process_recommendations(result, today, user_role=user_role)

        # Generate suggested questions
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
        logger.error("rereact_combined_agent_failed", error=str(e))
        # Fall back: try to return raw generator output
        try:
            recs = json.loads(raw_gen)
            raw_list = recs.get("recommendations", recs) if isinstance(recs, dict) else recs
            return {
                "validated_recommendations": raw_list if isinstance(raw_list, list) else [],
                "rejected": [],
                "validation_summary": "Validation unavailable — showing draft recommendations",
            }
        except Exception:
            return None
