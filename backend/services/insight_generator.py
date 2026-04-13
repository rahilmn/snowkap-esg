"""SME-driven insights for ESG news articles.

Fix 1+2: Routes to the RELEVANT specialist agent personality based on
article content_type and esg_pillar, instead of a single generic CXO prompt.
Generates domain-specific analysis, not just executive summaries.
"""

from pathlib import Path

import structlog

from backend.core import llm

logger = structlog.get_logger()

# Map content_type → specialist agent personality
CONTENT_TYPE_TO_AGENT: dict[str, str] = {
    "regulatory": "compliance",
    "financial": "executive",
    "operational": "supply_chain",
    "reputational": "stakeholder",
    "technical": "analytics",
    "narrative": "content",
    "data_release": "analytics",
}

# Fallback by ESG pillar
PILLAR_TO_AGENT: dict[str, str] = {
    "E": "supply_chain",
    "S": "stakeholder",
    "G": "compliance",
}

PERSONALITIES_DIR = Path(__file__).parent.parent / "agent" / "personalities"


def _load_specialist_prompt(agent_key: str) -> str:
    """Load a specialist agent's personality for insight generation."""
    md_path = PERSONALITIES_DIR / f"{agent_key}.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    return ""


async def generate_executive_insight(
    article_title: str,
    article_summary: str,
    company_name: str,
    relationship_type: str,
    causal_hops: int,
    frameworks: list[str],
    sentiment_score: float | None,
    urgency: str | None,
    content_type: str | None,
    article_content: str | None = None,
    esg_pillar: str | None = None,
) -> str | None:
    """Generate a specialist SME insight using the relevant agent personality.

    Routes to compliance, supply_chain, analytics, executive, stakeholder,
    or content agent based on article content_type and esg_pillar.
    """
    if not llm.is_configured():
        return None

    # Route to specialist
    agent_key = CONTENT_TYPE_TO_AGENT.get(content_type or "", "")
    if not agent_key and esg_pillar:
        # Strip multi-pillar values like "E|S|G"
        primary_pillar = esg_pillar.split("|")[0].strip() if esg_pillar else ""
        agent_key = PILLAR_TO_AGENT.get(primary_pillar, "executive")
    if not agent_key:
        agent_key = "executive"

    personality = _load_specialist_prompt(agent_key)

    fw_list = ", ".join(frameworks[:5]) if frameworks else "general ESG"
    sent_label = (
        "very negative" if (sentiment_score or 0) < -0.5
        else "negative" if (sentiment_score or 0) < -0.1
        else "neutral" if (sentiment_score or 0) < 0.3
        else "positive"
    )
    rel_type_readable = relationship_type.replace("_", " ") if relationship_type else "direct"

    # Use full article content if available, not just summary
    article_text = article_content[:2000] if article_content else (article_summary[:500] if article_summary else "No content available.")

    system_prompt = f"""{personality}

## Current Analysis Task
You are analyzing a specific news article for {company_name}. Provide a specialist insight
grounded in your domain expertise. This is NOT a generic executive summary — provide
real SME-level analysis with specific, actionable intelligence.

## Article Data
Title: "{article_title}"
Content: {article_text}

Company: {company_name}
Impact: via {rel_type_readable} ({causal_hops} hops)
Frameworks: {fw_list}
Sentiment: {sent_label} ({sentiment_score})
Urgency: {urgency or 'medium'}
Content Type: {content_type or 'general'}
ESG Pillar: {esg_pillar or 'unclassified'}"""

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    # Detect macro/sentiment signals — these should not recommend "immediate action"
    _article_lower = (article_content or article_title or "").lower()
    _is_macro = any(kw in _article_lower for kw in [
        "ceasefire", "geopolit", "commodity price", "gold price", "silver price",
        "oil price", "crude", "brent", "wti", "market rally", "market sentiment",
        "inflation worr", "tariff", "trade war", "sector rotation",
        "nifty", "sensex", "equity rally", "stock market",
        "bond yield", "interest rate", "fed rate", "rbi rate",
    ])
    _macro_qualifier = ""
    if _is_macro:
        _macro_qualifier = (
            "\n\nCRITICAL — MACRO/SENTIMENT SIGNAL DETECTED:\n"
            "This article is about a macro-level event (geopolitical, commodity, sentiment).\n"
            "- Do NOT recommend 'immediate action' — this is a monitoring signal only\n"
            "- Do NOT force compliance/disclosure actions for indirect market signals\n"
            "- If the signal doesn't directly affect the company's operations, say so clearly\n"
            "- Use qualifying language: 'potential', 'directional', 'if sustained'\n"
            "- The correct conclusion may be: 'Monitor quarterly; no ESG action triggered'\n"
        )

    user_prompt = f"""Analyze this article as a {agent_key.replace('_', ' ')} specialist.

TODAY'S DATE: {today}. All dates/deadlines in your response MUST be after {today}.

Write 3-4 sentences of expert insight:
1. What is the SPECIFIC business impact on {company_name}? (not generic ESG advice)
2. Which framework obligations are affected? Use CORRECT framework codes — match the topic precisely:
   BRSR principles: P1=Ethics, P2=Sustainable goods, P3=Employee wellbeing, P4=Stakeholders, P5=Human rights, P6=Environment, P7=Policy advocacy, P8=Inclusive growth, P9=Consumer responsibility
   GRI: 201-2=financial climate risk, 302=energy, 305=emissions, 3-3=material topics
   TCFD sections: Strategy, Risk_Management, Metrics_Targets
   DO NOT default to BRSR:P6 unless the article is specifically about environmental/pollution topics.
3. What is the recommended action with timeline?
{_macro_qualifier}
Rules:
- Read the full article content above, not just the headline
- Reference specific data points from the article
- If the article mentions financial figures, include them
- Be specific to {company_name}'s situation
- DO NOT use vague verbs: "enhance", "strengthen", "improve". Use: "commission", "file", "appoint", "allocate", "audit"
- NEVER mention specific company names (e.g., "Oil India", "ONGC") unless they appear in the article text above. Use "sector peers" or "industry" instead.
- Maximum 120 words"""

    try:
        insight = await llm.chat(
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=300,
            model="gpt-4.1-nano",
        )
        insight = insight.strip()
        if len(insight) < 30:
            return None
        logger.info(
            "specialist_insight_generated",
            article=article_title[:50],
            company=company_name,
            specialist=agent_key,
            insight_len=len(insight),
        )
        return insight
    except Exception as e:
        logger.error("insight_generation_failed", error=str(e), specialist=agent_key)
        return None
