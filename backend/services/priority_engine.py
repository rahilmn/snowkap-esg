"""Composite priority scoring engine for ESG news articles.

Phase 1E: Combines sentiment severity, urgency, structural impact, financial signals,
irreversibility, framework breadth, and regulatory deadline proximity into a single
0-100 priority score.

Future: Replace rule-based formula with XGBoost trained on user swipe data.
"""

import structlog

logger = structlog.get_logger()

# Urgency → weight mapping
URGENCY_WEIGHTS: dict[str, float] = {
    "critical": 25.0,
    "high": 18.0,
    "medium": 10.0,
    "low": 3.0,
}

# Reversibility → score mapping
REVERSIBILITY_SCORES: dict[str, float] = {
    "irreversible": 10.0,
    "difficult": 7.0,
    "moderate": 4.0,
    "easy": 1.0,
}

# Priority level thresholds (checked in order — first match wins)
PRIORITY_THRESHOLDS: list[tuple[float, str]] = [
    (85.0, "CRITICAL"),
    (70.0, "HIGH"),
    (40.0, "MEDIUM"),
    (0.0, "LOW"),
]


def calculate_priority_score(
    sentiment_score: float | None,
    urgency: str | None,
    impact_score: float,
    has_financial_signal: bool,
    reversibility: str | None,
    framework_count: int,
    role_multiplier: float = 1.0,
    days_to_deadline: int | None = None,
) -> tuple[float, str]:
    """Calculate composite priority score and level.

    Args:
        sentiment_score: -1.0 to +1.0 (None treated as 0.0)
        urgency: critical/high/medium/low (None treated as "low")
        impact_score: 0-100 from causal chain engine
        has_financial_signal: Whether financial amount was detected
        reversibility: irreversible/difficult/moderate/easy (None treated as "easy")
        framework_count: Number of ESG frameworks this article touches
        role_multiplier: Per-role boost (executive=1.2, member=0.8)
        days_to_deadline: Days until the nearest regulatory deadline (None = no deadline)

    Returns:
        (priority_score: float 0-100, priority_level: str)
    """
    # 1a. Sentiment severity: how negative? (0-25)
    sent = sentiment_score if sentiment_score is not None else 0.0
    sentiment_severity = max(0.0, -sent) * 25.0

    # 1b. Positive opportunity: material positive events get non-zero score (0-10)
    # Lower weight than negative (10 vs 25) to preserve downside bias,
    # but ensures green bonds, capital raises, and competitive wins surface
    positive_opportunity = max(0.0, sent) * 10.0

    # 2. Urgency weight (0-25)
    urgency_weight = URGENCY_WEIGHTS.get((urgency or "").lower(), 3.0)

    # 3. Structural impact from causal chain (0-20)
    impact_component = min(impact_score, 100.0) / 100.0 * 20.0

    # 4. Financial signal presence (0 or 15)
    financial_component = 15.0 if has_financial_signal else 0.0

    # 5. Irreversibility (0-10)
    irreversibility_score = REVERSIBILITY_SCORES.get((reversibility or "").lower(), 1.0)

    # 6. Framework breadth: multi-framework = systemic issue (0-5)
    framework_component = min(framework_count, 5) * 1.0

    # 7. Regulatory deadline proximity (0-20)
    if days_to_deadline is not None and days_to_deadline >= 0:
        if days_to_deadline <= 90:
            regulatory_deadline = 20.0
        elif days_to_deadline <= 180:
            regulatory_deadline = 12.0
        elif days_to_deadline <= 365:
            regulatory_deadline = 5.0
        else:
            regulatory_deadline = 0.0
    else:
        regulatory_deadline = 0.0

    # Composite
    raw_score = (
        sentiment_severity
        + positive_opportunity
        + urgency_weight
        + impact_component
        + financial_component
        + irreversibility_score
        + framework_component
        + regulatory_deadline
    ) * role_multiplier

    priority_score = min(round(raw_score, 1), 100.0)
    priority_score = max(priority_score, 0.0)

    # Determine level
    priority_level = "LOW"
    for threshold, level in PRIORITY_THRESHOLDS:
        if priority_score >= threshold:
            priority_level = level
            break

    logger.debug(
        "priority_calculated",
        score=priority_score,
        level=priority_level,
        components={
            "sentiment": round(sentiment_severity, 1),
            "positive_opportunity": round(positive_opportunity, 1),
            "urgency": urgency_weight,
            "impact": round(impact_component, 1),
            "financial": financial_component,
            "irreversibility": irreversibility_score,
            "frameworks": framework_component,
            "regulatory_deadline": regulatory_deadline,
        },
    )

    return priority_score, priority_level
