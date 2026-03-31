"""Auto-generate ESG recommendations from causal chain + climate risk intelligence.

Phase 4: When a causal chain links climate risk to a company facility,
generates actionable recommendations with framework references.
"""

import json

import structlog

from backend.core import llm

logger = structlog.get_logger()


async def generate_recommendations(
    article_title: str,
    company_name: str,
    facility_name: str | None,
    climate_risk: str | None,
    relationship_type: str,
    frameworks: list[str],
    urgency: str | None,
) -> list[dict]:
    """Generate 3 actionable ESG recommendations from causal chain context.

    Returns list of {title, description, priority, framework, timeline}.
    Uses gpt-4o-mini for cost efficiency.
    """
    if not llm.is_configured():
        return []

    facility_context = f" which impacts facility '{facility_name}'" if facility_name else ""
    climate_context = f" in a {climate_risk.replace('_', ' ')} zone" if climate_risk else ""
    fw_list = ", ".join(frameworks[:5]) if frameworks else "general ESG frameworks"

    prompt = f"""Generate exactly 3 actionable ESG recommendations. Return ONLY a JSON array.

Context:
- News article: "{article_title}"
- Company: {company_name}{facility_context}{climate_context}
- Relationship: {relationship_type}
- Frameworks affected: {fw_list}
- Urgency: {urgency or 'medium'}

Return this exact JSON structure (no markdown):
[
  {{
    "title": "Action-oriented title (max 10 words)",
    "description": "Specific steps to take (max 40 words)",
    "priority": "critical|high|medium",
    "framework": "Which framework this addresses (e.g., BRSR:P6, GRI:303)",
    "timeline": "days|weeks|months"
  }}
]

Rules:
- First recommendation should be the most urgent action
- Reference specific framework indicators (BRSR:P6, GRI:303, etc.)
- Include measurable outcomes where possible
- Be specific to {company_name}, not generic ESG advice"""

    try:
        raw = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            model="gpt-4o-mini",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        recommendations = json.loads(raw)
        if not isinstance(recommendations, list):
            return []

        logger.info(
            "recommendations_generated",
            company=company_name,
            count=len(recommendations),
        )
        return recommendations[:3]
    except Exception as e:
        logger.error("recommendation_generation_failed", error=str(e))
        return []
