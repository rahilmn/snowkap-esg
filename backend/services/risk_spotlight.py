"""Lightweight Risk Spotlight — quick top-3 risk scan for FEED-tier articles.

Uses gpt-4o-mini with ~300 tokens to identify the 3 most relevant risk
categories from the 10-category taxonomy. Much cheaper than the full
risk_taxonomy.py which scores all 10 with probability × exposure.

Result format: {"mode": "spotlight", "top_risks": [...]}
This is overwritten by the full risk matrix for HOME-tier articles.
"""

import json

import structlog

from backend.core import llm

logger = structlog.get_logger()

RISK_CATEGORIES = [
    "Physical Risk", "Supply Chain Risk", "Reputational Risk",
    "Regulatory Risk", "Litigation Risk", "Transition Risk",
    "Human Capital Risk", "Technological Risk", "Manpower / Employee Risk",
    "Market & Uncertainty Risk",
]

CLASSIFICATION_ORDER = {"HIGH": 0, "MODERATE": 1, "LOW": 2}


async def run_risk_spotlight(
    article_title: str,
    article_content: str | None,
    company_name: str,
) -> dict | None:
    """Quick top-3 risk scan for FEED-tier articles.

    Returns {"mode": "spotlight", "top_risks": [...]} or None on failure.
    """
    if not llm.is_configured():
        return None

    text = article_content[:1000] if article_content else article_title
    categories_str = ", ".join(RISK_CATEGORIES)

    try:
        raw = await llm.chat(
            system="You are an ESG risk classifier. Return ONLY valid JSON.",
            messages=[{"role": "user", "content": f"""Given this article affecting {company_name}, identify the top 3 most relevant ESG risk categories.

ARTICLE: "{article_title}"
CONTENT: {text}

RISK CATEGORIES: {categories_str}

Return JSON array of exactly 3:
[
  {{"category_name": "<exact name from list>", "classification": "HIGH|MODERATE|LOW", "rationale": "<1 sentence>"}}
]"""}],
            max_tokens=300,
            model="gpt-4.1-nano",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        risks = json.loads(raw)
        if not isinstance(risks, list):
            return None

        # Validate and normalize
        valid_risks = []
        for r in risks[:3]:
            if not isinstance(r, dict):
                continue
            name = r.get("category_name", "")
            cls = r.get("classification", "LOW").upper()
            if cls not in CLASSIFICATION_ORDER:
                cls = "LOW"
            valid_risks.append({
                "category_name": name,
                "classification": cls,
                "rationale": r.get("rationale", ""),
            })

        # Sort by classification severity
        valid_risks.sort(key=lambda x: CLASSIFICATION_ORDER.get(x["classification"], 2))

        logger.info(
            "risk_spotlight_complete",
            article=article_title[:50],
            top_risk=valid_risks[0]["category_name"] if valid_risks else "none",
        )

        return {
            "mode": "spotlight",
            "top_risks": valid_risks,
        }
    except Exception as e:
        logger.warning("risk_spotlight_failed", error=str(e))
        return None
