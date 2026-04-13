"""Role-based news curation profiles and relevance scoring.

Phase 2B: Each role has a profile defining what type of news matters most.
The compute_role_relevance() function scores how well an article matches
a given role profile (0-100).
"""

import structlog

logger = structlog.get_logger()

# Static role profiles — what type of news matters to each role
ROLE_PROFILES: dict[str, dict] = {
    # Module 6 spec: 6 stakeholder roles with distinct focus areas
    "board_member": {
        "description": "Board: strategic risk, fiduciary exposure, governance oversight",
        "primary_focus": "Strategic risk, fiduciary exposure",
        "secondary_focus": "ESG reputation, long-term positioning",
        "recommendation_style": "Governance-level, decision-oriented",
        "priority_pillars": {"E": 0.8, "S": 0.8, "G": 1.0},
        "priority_frameworks": ["TCFD", "BRSR", "CSRD"],
        "content_types": ["regulatory", "reputational", "financial"],
        "content_depth": "brief",
        "alert_threshold": 85,
        "boost": 1.3,
    },
    "ceo": {
        "description": "CEO: competitive positioning, stakeholder narrative, capital market perception",
        "primary_focus": "Competitive positioning, stakeholder narrative",
        "secondary_focus": "Capital market perception",
        "recommendation_style": "Strategic, narrative-aware",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": ["TCFD", "IFRS_S1", "IFRS_S2"],
        "content_types": ["financial", "reputational", "regulatory"],
        "content_depth": "brief",
        "alert_threshold": 80,
        "boost": 1.2,
    },
    "cfo": {
        "description": "CFO: valuation impact, cost of capital, cash flow, compliance cost",
        "primary_focus": "Valuation impact, cost of capital, cash flow",
        "secondary_focus": "Compliance cost, capex implications",
        "recommendation_style": "Quantitative, ROI-framed",
        "priority_pillars": {"E": 0.8, "S": 0.6, "G": 1.0},
        "priority_frameworks": ["TCFD", "IFRS_S1", "IFRS_S2", "SASB"],
        "content_types": ["financial", "regulatory", "data_release"],
        "content_depth": "detailed",
        "alert_threshold": 75,
        "boost": 1.1,
    },
    "cso": {
        "description": "CSO/Head of ESG: ESG scoring, framework alignment, disclosure gaps, benchmark shifts",
        "primary_focus": "ESG scoring impact, framework alignment",
        "secondary_focus": "Disclosure gaps, benchmark shifts",
        "recommendation_style": "Technical ESG, taxonomy-mapped",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": ["BRSR", "GRI", "ESRS", "CSRD", "CDP", "TCFD"],
        "content_types": ["regulatory", "operational", "technical", "data_release"],
        "content_depth": "detailed",
        "alert_threshold": 60,
        "boost": 1.0,
    },
    "compliance": {
        "description": "Compliance/Legal: regulatory triggers, reporting obligations, litigation risk",
        "primary_focus": "Regulatory triggers, reporting obligations",
        "secondary_focus": "Litigation risk, jurisdictional exposure",
        "recommendation_style": "Regulation-specific, deadline-driven",
        "priority_pillars": {"E": 0.8, "S": 0.8, "G": 1.0},
        "priority_frameworks": ["BRSR", "CSRD", "ESRS", "SEBI"],
        "content_types": ["regulatory", "financial"],
        "content_depth": "detailed",
        "alert_threshold": 55,
        "boost": 1.0,
    },
    "supply_chain": {
        "description": "Supply Chain/Ops: supplier risk, geographic disruption, cost pass-through",
        "primary_focus": "Supplier risk, geographic disruption",
        "secondary_focus": "Cost pass-through, alternative sourcing",
        "recommendation_style": "Operational, tier-mapped",
        "priority_pillars": {"E": 1.0, "S": 0.8, "G": 0.6},
        "priority_frameworks": ["GRI", "BRSR", "ESRS"],
        "content_types": ["operational", "technical", "narrative"],
        "content_depth": "detailed",
        "alert_threshold": 50,
        "boost": 0.9,
    },
    # Legacy aliases (backward compat)
    "executive_view": {
        "description": "CXO-level (alias for ceo)",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": ["TCFD", "IFRS_S1", "IFRS_S2"],
        "content_types": ["financial", "reputational", "regulatory"],
        "content_depth": "brief",
        "alert_threshold": 85,
        "boost": 1.2,
    },
    "sustainability_manager": {
        "description": "Sustainability manager (alias for cso)",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": ["BRSR", "GRI", "ESRS", "CSRD"],
        "content_types": ["regulatory", "operational", "technical", "data_release"],
        "content_depth": "detailed",
        "alert_threshold": 60,
        "boost": 1.0,
    },
    "data_entry_analyst": {
        "description": "ESG Analyst: framework alignment, scoring, disclosure analysis, benchmark tracking",
        "primary_focus": "ESG scoring, framework alignment, disclosure gaps, benchmark shifts",
        "secondary_focus": "Peer comparison, rating methodology, data quality, trend analysis",
        "recommendation_style": "Framework-grounded with specific section codes and disclosure deadlines",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": ["BRSR", "GRI", "ESRS", "CSRD", "CDP", "TCFD", "SASB"],
        "content_types": ["regulatory", "data_release", "technical", "financial", "operational"],
        "content_depth": "detailed",
        "alert_threshold": 55,
        "boost": 1.1,
    },
    "member": {
        "description": "General member: educational, narrative, high-level",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": [],
        "content_types": ["narrative", "reputational", "regulatory"],
        "content_depth": "brief",
        "alert_threshold": 70,
        "boost": 0.8,
    },
    "admin": {
        "description": "Admin: sees everything",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": [],
        "content_types": [],
        "content_depth": "standard",
        "boost": 1.0,
    },
    "platform_admin": {
        "description": "Platform admin: sees everything",
        "priority_pillars": {"E": 1.0, "S": 1.0, "G": 1.0},
        "priority_frameworks": [],
        "content_types": [],
        "content_depth": "standard",
        "boost": 1.0,
    },
}


def get_role_profile(role: str) -> dict:
    """Get the news curation profile for a role. Defaults to member."""
    return ROLE_PROFILES.get(role, ROLE_PROFILES["member"])


def compute_role_relevance(
    role: str,
    content_type: str | None,
    frameworks: list[str] | None,
    esg_pillar: str | None,
    role_profile: dict | None = None,
) -> float:
    """Score how well an article matches a role's profile (0-100).

    Components:
    - Content type match: 40 points (primary signal)
    - Framework overlap: 30 points
    - Pillar alignment: 20 points
    - Base score: 10 points (every article has some relevance)

    Args:
        role_profile: Pre-computed role profile dict. If None, will be
            looked up from role string (BUG-11: pass pre-computed profile
            to avoid redundant lookups in loops).
    """
    profile = role_profile if role_profile is not None else get_role_profile(role)
    score = 10.0

    # 1. Content type match (0 or 40)
    preferred_types = profile.get("content_types", [])
    if not preferred_types:
        score += 40.0
    elif content_type and content_type in preferred_types:
        score += 40.0
    elif content_type:
        score += 10.0

    # 2. Framework overlap (0-30)
    preferred_fws = profile.get("priority_frameworks", [])
    if not preferred_fws:
        score += 30.0
    elif frameworks:
        article_fw_roots = {fw.split(":")[0] for fw in frameworks}
        overlap = len(article_fw_roots & set(preferred_fws))
        if overlap > 0:
            score += min(overlap / len(preferred_fws), 1.0) * 30.0

    # 3. Pillar alignment (0-20)
    pillar_weights = profile.get("priority_pillars", {})
    if esg_pillar and esg_pillar in pillar_weights:
        score += pillar_weights[esg_pillar] * 20.0
    elif not esg_pillar:
        score += 10.0

    return min(round(score, 1), 100.0)


def compute_user_preference_boost(
    preferred_frameworks: list[str] | None,
    preferred_pillars: list[str] | None,
    preferred_topics: list[str] | None,
    dismissed_topics: list[str] | None,
    article_frameworks: list[str] | None,
    article_pillar: str | None,
    article_topics: list[str] | None,
) -> float:
    """Score user preference alignment (-20 to +30).

    Positive for matches, negative for dismissed topics.
    """
    boost = 0.0

    if preferred_frameworks and article_frameworks:
        article_fw_roots = {fw.split(":")[0] for fw in article_frameworks}
        if set(preferred_frameworks) & article_fw_roots:
            boost += 15.0

    if preferred_pillars and article_pillar:
        if article_pillar in preferred_pillars:
            boost += 10.0

    if preferred_topics and article_topics:
        if set(preferred_topics) & set(article_topics):
            boost += 5.0

    if dismissed_topics and article_topics:
        if set(dismissed_topics) & set(article_topics):
            boost -= 20.0

    return boost


def recency_score(created_at_str: str | None) -> float:
    """Calculate recency score (0-100). Newer = higher."""
    if not created_at_str:
        return 50.0

    from datetime import datetime, timezone

    try:
        if isinstance(created_at_str, datetime):
            dt = created_at_str
        else:
            from dateutil.parser import parse
            dt = parse(str(created_at_str))

        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours_old = (now - dt).total_seconds() / 3600.0
        return max(0.0, min(100.0, 100.0 - hours_old * 2.0))
    except Exception:
        return 50.0
