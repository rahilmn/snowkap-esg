"""10-Category Risk Taxonomy with Probability & Exposure Scoring (Module 6).

Scores each article across 10 ESG risk categories on two axes:
- Probability of Occurrence (1-5): Rare → Almost Certain
- Exposure to Risk (1-5): Negligible → Critical

Risk Priority Score = Probability × Exposure (max 25 per category).
Aggregate score = sum of all 10 category scores / 250 (normalised 0-1).

Classifications:
  CRITICAL  20-25
  HIGH      12-19
  MODERATE   6-11
  LOW        1-5
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core import llm

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Risk category definitions
# ---------------------------------------------------------------------------

RISK_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "physical",
        "name": "Physical Risk",
        "definition": "Acute and chronic climate hazards including extreme weather, flooding, wildfire, sea-level rise, and heat stress.",
        "example_indicators": [
            "facility damage from cyclone/flood",
            "chronic water stress in operating regions",
            "heat-related productivity losses",
        ],
    },
    {
        "id": "supply_chain",
        "name": "Supply Chain Risk",
        "definition": "Tier 1/2/3 supplier disruption, geographic concentration, single-source dependency, and logistics fragility.",
        "example_indicators": [
            "key supplier plant shutdown",
            "port congestion or trade route disruption",
            "raw material sourcing concentration >60%",
        ],
    },
    {
        "id": "reputational",
        "name": "Reputational Risk",
        "definition": "Brand damage, stakeholder trust erosion, greenwashing allegations, and social media backlash.",
        "example_indicators": [
            "viral negative coverage",
            "greenwashing lawsuit or accusation",
            "consumer boycott or petition",
        ],
    },
    {
        "id": "regulatory",
        "name": "Regulatory Risk",
        "definition": "New or tightening ESG regulations, disclosure mandates, carbon pricing, and compliance deadlines.",
        "example_indicators": [
            "CSRD/BRSR reporting deadline",
            "carbon border adjustment mechanism",
            "mandatory human rights due diligence",
        ],
    },
    {
        "id": "litigation",
        "name": "Litigation Risk",
        "definition": "ESG-related lawsuits, enforcement actions, class-action suits, and regulatory penalties.",
        "example_indicators": [
            "climate liability lawsuit",
            "environmental damage penalty",
            "securities fraud tied to ESG misstatement",
        ],
    },
    {
        "id": "transition",
        "name": "Transition Risk",
        "definition": "Stranded assets, demand shifts away from carbon-intensive products, technology obsolescence in the move to net-zero.",
        "example_indicators": [
            "fossil fuel asset write-down",
            "EV adoption eroding ICE demand",
            "renewable energy displacing thermal capacity",
        ],
    },
    {
        "id": "human_capital",
        "name": "Human Capital Risk",
        "definition": "Talent attraction/retention challenges, labor relations disputes, workplace safety incidents, and skills gaps.",
        "example_indicators": [
            "high attrition in critical roles",
            "workplace fatality or safety violation",
            "union strike or collective bargaining failure",
        ],
    },
    {
        "id": "technological",
        "name": "Technological Risk",
        "definition": "Technology disruption, cybersecurity breaches, AI governance failures, and digital transformation risk.",
        "example_indicators": [
            "major data breach or ransomware attack",
            "AI bias in hiring or lending algorithms",
            "failure to adopt industry-standard technology",
        ],
    },
    {
        "id": "manpower_employee",
        "name": "Manpower / Employee Risk",
        "definition": "Workforce availability constraints, productivity decline, employee wellbeing issues, and demographic shifts.",
        "example_indicators": [
            "labour shortage in key markets",
            "rising absenteeism or burnout metrics",
            "workforce aging without succession planning",
        ],
    },
    {
        "id": "market_uncertainty",
        "name": "Market & Uncertainty Risk",
        "definition": "Market volatility, capital flow shifts (ESG fund in/outflows), geopolitical disruption, and macro uncertainty.",
        "example_indicators": [
            "ESG fund outflows >10% in quarter",
            "geopolitical sanctions impacting operations",
            "commodity price shock affecting margins",
        ],
    },
]

# ---------------------------------------------------------------------------
# Scoring labels
# ---------------------------------------------------------------------------

PROBABILITY_LABELS: dict[int, str] = {
    1: "Rare",
    2: "Unlikely",
    3: "Possible",
    4: "Likely",
    5: "Almost Certain",
}

EXPOSURE_LABELS: dict[int, str] = {
    1: "Negligible",
    2: "Minor",
    3: "Moderate",
    4: "Severe",
    5: "Critical",
}

# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------

_CLASSIFICATION_THRESHOLDS: list[tuple[int, str]] = [
    (20, "CRITICAL"),
    (12, "HIGH"),
    (6, "MODERATE"),
    (1, "LOW"),
]


def classify_risk(score: int) -> str:
    """Classify a single risk priority score (1-25) into a level.

    Returns:
        One of CRITICAL, HIGH, MODERATE, LOW.
    """
    for threshold, level in _CLASSIFICATION_THRESHOLDS:
        if score >= threshold:
            return level
    return "LOW"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CategoryScore:
    """Score for a single risk category."""

    category_id: str
    category_name: str
    probability: int  # 1-5
    exposure: int  # 1-5
    rationale: str = ""

    @property
    def risk_score(self) -> int:
        return self.probability * self.exposure

    @property
    def classification(self) -> str:
        return classify_risk(self.risk_score)

    @property
    def probability_label(self) -> str:
        return PROBABILITY_LABELS.get(self.probability, "Unknown")

    @property
    def exposure_label(self) -> str:
        return EXPOSURE_LABELS.get(self.exposure, "Unknown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "probability": self.probability,
            "probability_label": self.probability_label,
            "exposure": self.exposure,
            "exposure_label": self.exposure_label,
            "risk_score": self.risk_score,
            "classification": self.classification,
            "rationale": self.rationale,
        }


@dataclass
class RiskAssessment:
    """Complete 10-category risk assessment for an article."""

    categories: list[CategoryScore] = field(default_factory=list)

    @property
    def total_score(self) -> int:
        """Sum of all category risk scores (max 250)."""
        return sum(c.risk_score for c in self.categories)

    @property
    def aggregate_score(self) -> float:
        """Normalised aggregate score: total / 250 (0.0 – 1.0)."""
        return round(self.total_score / 250, 4) if self.categories else 0.0

    @property
    def top_risks(self) -> list[CategoryScore]:
        """Top 3 categories by risk score (descending)."""
        return sorted(self.categories, key=lambda c: c.risk_score, reverse=True)[:3]

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": [c.to_dict() for c in self.categories],
            "total_score": self.total_score,
            "aggregate_score": self.aggregate_score,
            "top_risks": [c.to_dict() for c in self.top_risks],
        }


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ESG risk analyst. You assess news articles against a 10-category risk taxonomy.

For each category you must provide:
- probability: integer 1-5 (1=Rare, 2=Unlikely, 3=Possible, 4=Likely, 5=Almost Certain)
- exposure: integer 1-5 (1=Negligible, 2=Minor, 3=Moderate, 4=Severe, 5=Critical)
- rationale: one-sentence justification

Be precise and evidence-based. If the article has no relevance to a category, assign probability=1 and exposure=1.
Return ONLY valid JSON, no markdown fences."""

_CATEGORY_BLOCK = "\n".join(
    f'{i+1}. {cat["id"]} — {cat["name"]}: {cat["definition"]}'
    for i, cat in enumerate(RISK_CATEGORIES)
)


def _build_user_prompt(
    article_title: str,
    article_content: str,
    company_name: str,
    nlp_extraction: dict[str, Any],
    esg_themes: dict[str, Any],
    frameworks: list[str],
) -> str:
    """Build the user prompt for the risk assessment LLM call."""
    content_truncated = article_content[:3000] if article_content else article_title
    fw_list = ", ".join(frameworks[:8]) if frameworks else "general ESG"
    themes_str = json.dumps(esg_themes, default=str)[:500] if esg_themes else "{}"
    nlp_str = json.dumps(nlp_extraction, default=str)[:500] if nlp_extraction else "{}"

    return f"""Assess the following article against ALL 10 risk categories.

ARTICLE TITLE: {article_title}
ARTICLE CONTENT: {content_truncated}

CONTEXT:
- Company: {company_name}
- Applicable frameworks: {fw_list}
- ESG themes detected: {themes_str}
- NLP extraction: {nlp_str}

RISK CATEGORIES:
{_CATEGORY_BLOCK}

Return a JSON object with a single key "categories" containing an array of 10 objects, each with:
  "category_id": string (matching the IDs above),
  "probability": integer 1-5,
  "exposure": integer 1-5,
  "rationale": string (one sentence)

Example element:
{{"category_id": "physical", "probability": 3, "exposure": 4, "rationale": "Facility is in a high-flood-risk coastal zone."}}
"""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _clamp(value: Any, low: int = 1, high: int = 5) -> int:
    """Clamp a value to [low, high], defaulting to 1 on bad input."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, v))


_CATEGORY_NAME_MAP: dict[str, str] = {cat["id"]: cat["name"] for cat in RISK_CATEGORIES}
_VALID_IDS: set[str] = {cat["id"] for cat in RISK_CATEGORIES}


def _parse_llm_response(raw: str) -> RiskAssessment:
    """Parse the LLM JSON response into a RiskAssessment.

    Handles minor formatting issues (markdown fences, extra keys).
    Falls back to a default LOW assessment if parsing fails entirely.
    """
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("risk_taxonomy_json_parse_failed", raw_length=len(raw))
        return _default_assessment()

    raw_categories = data.get("categories") if isinstance(data, dict) else data
    if not isinstance(raw_categories, list):
        logger.warning("risk_taxonomy_unexpected_structure")
        return _default_assessment()

    seen_ids: set[str] = set()
    scores: list[CategoryScore] = []

    for item in raw_categories:
        if not isinstance(item, dict):
            continue
        cat_id = str(item.get("category_id", "")).strip()
        if cat_id not in _VALID_IDS or cat_id in seen_ids:
            continue
        seen_ids.add(cat_id)
        scores.append(
            CategoryScore(
                category_id=cat_id,
                category_name=_CATEGORY_NAME_MAP[cat_id],
                probability=_clamp(item.get("probability")),
                exposure=_clamp(item.get("exposure")),
                rationale=str(item.get("rationale", ""))[:300],
            )
        )

    # Fill any missing categories with defaults
    for cat in RISK_CATEGORIES:
        if cat["id"] not in seen_ids:
            scores.append(
                CategoryScore(
                    category_id=cat["id"],
                    category_name=cat["name"],
                    probability=1,
                    exposure=1,
                    rationale="Not assessed — defaulted to low risk.",
                )
            )

    return RiskAssessment(categories=scores)


def _default_assessment() -> RiskAssessment:
    """Return a default all-LOW assessment when LLM parsing fails."""
    return RiskAssessment(
        categories=[
            CategoryScore(
                category_id=cat["id"],
                category_name=cat["name"],
                probability=1,
                exposure=1,
                rationale="Assessment unavailable — defaulted to low risk.",
            )
            for cat in RISK_CATEGORIES
        ]
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def assess_risk_matrix(
    article_title: str,
    article_content: str,
    company_name: str,
    nlp_extraction: dict[str, Any],
    esg_themes: dict[str, Any],
    frameworks: list[str],
) -> RiskAssessment:
    """Assess an article against the 10-category risk taxonomy.

    Makes a single LLM call (gpt-4o) that scores all 10 categories
    on probability (1-5) and exposure (1-5).

    Args:
        article_title: Headline of the article.
        article_content: Full or truncated article body text.
        company_name: Name of the company being assessed.
        nlp_extraction: Dict of entities/facts extracted by NLP pipeline.
        esg_themes: Dict of detected ESG themes and sub-themes.
        frameworks: List of applicable ESG framework codes (e.g. ["BRSR", "GRI"]).

    Returns:
        RiskAssessment with per-category scores, aggregate, and top 3 risks.
    """
    if not llm.is_configured():
        logger.warning("risk_taxonomy_llm_not_configured")
        return _default_assessment()

    user_prompt = _build_user_prompt(
        article_title=article_title,
        article_content=article_content,
        company_name=company_name,
        nlp_extraction=nlp_extraction,
        esg_themes=esg_themes,
        frameworks=frameworks,
    )

    try:
        raw_response = await llm.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system=_SYSTEM_PROMPT,
            max_tokens=2000,
            model="gpt-4o",
            temperature=0.2,
        )
    except Exception:
        logger.exception("risk_taxonomy_llm_call_failed")
        return _default_assessment()

    assessment = _parse_llm_response(raw_response)

    logger.info(
        "risk_taxonomy_assessed",
        company=company_name,
        aggregate_score=assessment.aggregate_score,
        total_score=assessment.total_score,
        top_risks=[
            {"id": r.category_id, "score": r.risk_score, "class": r.classification}
            for r in assessment.top_risks
        ],
    )

    return assessment
