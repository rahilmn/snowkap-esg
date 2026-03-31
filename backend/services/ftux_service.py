"""First-Time User Experience (FTUX) Service — Module 11 (v2.0).

Handles the 15-30 minute activation window for new tenants:
1. Serve general sustainability content (ESG market trends, educational explainers)
2. Provide app walkthrough metadata (HOME vs FEED, scores, role views, theme filters)
3. Track FTUX progress per tenant
4. Pre-populate with sector-default HIGH IMPACT stories
"""

import structlog
from datetime import datetime, timezone

logger = structlog.get_logger()

# Sector-default story templates for pre-population during FTUX
SECTOR_DEFAULT_STORIES: dict[str, list[dict]] = {
    "banking": [
        {
            "title": "RBI Climate Risk Disclosure Framework — What Banks Must Report",
            "summary": "Overview of RBI's evolving climate risk disclosure requirements for Indian banks.",
            "content_type": "regulatory",
            "esg_pillar": "E",
            "relevance_score": 8,
            "priority_level": "HIGH",
        },
        {
            "title": "Green Bond Market in India — 2025 Outlook",
            "summary": "India's green bond market growth trajectory and implications for banking sector ESG positioning.",
            "content_type": "financial",
            "esg_pillar": "E",
            "relevance_score": 7,
            "priority_level": "HIGH",
        },
        {
            "title": "BRSR Core Compliance — Value Chain Extension for Top 250 Listed Entities",
            "summary": "SEBI's mandate requiring top 250 companies to extend BRSR Core to value chain partners.",
            "content_type": "regulatory",
            "esg_pillar": "G",
            "relevance_score": 9,
            "priority_level": "HIGH",
        },
    ],
    "manufacturing": [
        {
            "title": "Scope 3 Emissions Reporting — Supply Chain Challenges for Indian Manufacturers",
            "summary": "How Scope 3 reporting requirements impact manufacturing supply chains in India.",
            "content_type": "regulatory",
            "esg_pillar": "E",
            "relevance_score": 8,
            "priority_level": "HIGH",
        },
        {
            "title": "CBAM Impact on Indian Exports — What Manufacturers Need to Know",
            "summary": "EU Carbon Border Adjustment Mechanism and its implications for Indian exporters.",
            "content_type": "regulatory",
            "esg_pillar": "E",
            "relevance_score": 9,
            "priority_level": "HIGH",
        },
    ],
    "default": [
        {
            "title": "ESG Reporting Landscape in India — BRSR, CSRD, and Beyond",
            "summary": "Overview of ESG reporting frameworks applicable to Indian companies.",
            "content_type": "regulatory",
            "esg_pillar": "G",
            "relevance_score": 8,
            "priority_level": "HIGH",
        },
        {
            "title": "Climate Physical Risk in India — Regional Exposure Assessment",
            "summary": "How climate physical risks vary across Indian states and impact corporate operations.",
            "content_type": "operational",
            "esg_pillar": "E",
            "relevance_score": 7,
            "priority_level": "HIGH",
        },
    ],
}

# Walkthrough steps
WALKTHROUGH_STEPS = [
    {
        "id": "home_vs_feed",
        "title": "HOME vs FEED",
        "description": "HOME shows the top 3-5 highest-impact ESG stories for your company. FEED shows everything else, sorted by relevance.",
        "target": "news_page",
    },
    {
        "id": "relevance_scores",
        "title": "Relevance Scores",
        "description": "Every story has a relevance score (0-10) based on ESG correlation, financial impact, compliance risk, supply chain impact, and people impact. Scores ≥7 make it to HOME.",
        "target": "article_card",
    },
    {
        "id": "role_views",
        "title": "Role-Based Views",
        "description": "Your designation determines how recommendations are framed. A CEO sees competitive positioning; a CFO sees financial impact tables; a CSO sees framework gap analysis.",
        "target": "settings",
    },
    {
        "id": "esg_themes",
        "title": "ESG Theme Filters",
        "description": "Filter news by 21 ESG themes across Environmental (8), Social (7), and Governance (6) categories. Each article is tagged with primary and secondary themes.",
        "target": "insights_page",
    },
    {
        "id": "agent_chat",
        "title": "AI Agent Chat",
        "description": "Ask the AI agent about any article. It uses 9 specialist personalities (supply chain, compliance, analytics, etc.) and tailors responses to your role.",
        "target": "agent_button",
    },
    {
        "id": "risk_matrix",
        "title": "Risk Assessment",
        "description": "Every high-impact story includes a 10-category risk matrix with probability × exposure scores. This helps you prioritize what actually needs attention.",
        "target": "article_detail",
    },
]

# Educational content for the activation window
EDUCATIONAL_CONTENT = [
    {
        "id": "what_is_esg",
        "title": "What is ESG Intelligence?",
        "body": "ESG Intelligence goes beyond reporting. It monitors global news, maps events to your company's exposure through causal chains, and delivers risk-quantified recommendations tailored to your role.",
        "duration_seconds": 60,
    },
    {
        "id": "frameworks_101",
        "title": "ESG Frameworks That Matter",
        "body": "BRSR (India), TCFD (Climate), ISSB/IFRS S1-S2 (Global), CSRD/ESRS (EU), GRI (Stakeholders). Each framework has specific disclosure requirements that Snowkap maps automatically.",
        "duration_seconds": 60,
    },
    {
        "id": "how_scoring_works",
        "title": "How We Score News",
        "body": "Every article passes through NLP extraction, ESG theme tagging, framework RAG mapping, geographic intelligence, and a 10-category risk matrix before you see it. Only validated intelligence reaches your HOME screen.",
        "duration_seconds": 45,
    },
]


def get_ftux_state(tenant_config: dict | None) -> dict:
    """Get the current FTUX state for a tenant.

    Returns progress info: which steps are complete, whether FTUX is active, etc.
    """
    if not tenant_config:
        return {
            "is_active": True,
            "completed_steps": [],
            "current_step": 0,
            "total_steps": len(WALKTHROUGH_STEPS),
            "estimated_minutes": 15,
        }

    ftux = tenant_config.get("ftux", {})
    completed = ftux.get("completed_steps", [])
    is_complete = ftux.get("completed", False)

    return {
        "is_active": not is_complete,
        "completed_steps": completed,
        "current_step": len(completed),
        "total_steps": len(WALKTHROUGH_STEPS),
        "estimated_minutes": max(0, 15 - len(completed) * 2),
        "completed_at": ftux.get("completed_at"),
    }


def get_walkthrough() -> list[dict]:
    """Return the full walkthrough steps."""
    return WALKTHROUGH_STEPS


def get_educational_content() -> list[dict]:
    """Return educational content for the activation window."""
    return EDUCATIONAL_CONTENT


def get_sector_defaults(industry: str | None) -> list[dict]:
    """Return pre-populated sector-default stories for FTUX."""
    sector_key = "default"
    if industry:
        industry_lower = industry.lower()
        if any(k in industry_lower for k in ["bank", "financial", "insurance"]):
            sector_key = "banking"
        elif any(k in industry_lower for k in ["manufactur", "industrial", "auto"]):
            sector_key = "manufacturing"

    return SECTOR_DEFAULT_STORIES.get(sector_key, SECTOR_DEFAULT_STORIES["default"])


def mark_step_complete(tenant_config: dict, step_id: str) -> dict:
    """Mark a walkthrough step as complete. Returns updated config."""
    ftux = tenant_config.setdefault("ftux", {"completed_steps": [], "completed": False})
    completed = ftux.setdefault("completed_steps", [])

    if step_id not in completed:
        completed.append(step_id)

    if len(completed) >= len(WALKTHROUGH_STEPS):
        ftux["completed"] = True
        ftux["completed_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("ftux_step_completed", step=step_id, total=len(completed))
    return tenant_config


def mark_ftux_complete(tenant_config: dict) -> dict:
    """Force-complete FTUX (user skipped). Returns updated config."""
    ftux = tenant_config.setdefault("ftux", {})
    ftux["completed"] = True
    ftux["completed_at"] = datetime.now(timezone.utc).isoformat()
    ftux["skipped"] = True
    logger.info("ftux_skipped")
    return tenant_config
