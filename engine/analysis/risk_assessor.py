"""Risk assessment — 10 ESG categories + 7 TEMPLES categories (ontology-driven).

Combines two risk frameworks:
- ESG risk taxonomy: physical, supply chain, reputational, regulatory,
  litigation, transition, human capital, technological, manpower,
  market & uncertainty
- TEMPLES enterprise risk: technological, economic, media, political,
  legal, environmental, social

Both query the ontology for industry risk weights and lead/lag indicators.
A light LLM call produces per-category probability × exposure scores
(1-5 each). Industry weight multiplier comes from the graph.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.config import get_openai_api_key, load_settings
from engine.nlp.extractor import NLPExtraction
from engine.nlp.theme_tagger import ESGThemeTags
from engine.ontology.graph import get_graph
from engine.ontology.intelligence import (
    query_esg_risk_categories,
    query_risk_indicators,
    query_risk_level_thresholds,
    query_risk_weight,
    query_temples_categories,
    query_theme_risk_map,
    query_theme_temples_map,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskScore:
    category: str
    probability: int  # 1-5
    exposure: int  # 1-5
    raw_score: int  # probability × exposure
    industry_weight: float
    adjusted_score: float  # raw_score × industry_weight
    level: str  # CRITICAL | HIGH | MODERATE | LOW
    lead_indicators: list[str] = field(default_factory=list)
    lag_indicators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskAssessment:
    esg_risks: list[RiskScore]
    temples_risks: list[RiskScore]
    top_risks: list[RiskScore]  # top 5 merged view
    aggregate_score: float  # 0.0-1.0 normalized
    ontology_queries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "esg_risks": [r.to_dict() for r in self.esg_risks],
            "temples_risks": [r.to_dict() for r in self.temples_risks],
            "top_risks": [r.to_dict() for r in self.top_risks],
            "aggregate_score": self.aggregate_score,
            "ontology_queries": self.ontology_queries,
        }


# ---------------------------------------------------------------------------
# LLM scoring
# ---------------------------------------------------------------------------


def _classify_level(adjusted_score: float) -> str:
    """Classify risk level using ontology-sourced thresholds."""
    thresholds = query_risk_level_thresholds()
    for t in thresholds:  # sorted DESC by min_score
        if adjusted_score >= t.min_score:
            return t.level
    return "LOW"


def _build_llm_prompt(
    article_title: str, article_content: str, company_name: str, industry: str,
) -> str:
    esg_cats = query_esg_risk_categories()
    temples_cats = query_temples_categories()
    esg_list = "\n".join(f"- {c}" for c in esg_cats)
    temples_list = "\n".join(f"- {c}" for c in temples_cats)
    return f"""Assess the risks posed by this news event to {company_name} (industry: {industry}).

Score each risk category on a 1-5 scale for probability AND exposure:
- 1 = minimal, 5 = severe/certain.

ESG RISK CATEGORIES:
{esg_list}

TEMPLES ENTERPRISE RISK CATEGORIES:
{temples_list}

ARTICLE TITLE: {article_title}
ARTICLE CONTENT: {article_content[:2500]}

Respond with a JSON object:
{{
  "esg": {{ "<category>": {{"probability": <1-5>, "exposure": <1-5>}}, ... }},
  "temples": {{ "<category>": {{"probability": <1-5>, "exposure": <1-5>}}, ... }}
}}

Only include categories with probability >= 2 AND exposure >= 2 to avoid noise.
Return ONLY the JSON, no prose."""


def _llm_score(
    article_title: str, article_content: str, company_name: str, industry: str,
) -> dict[str, Any]:
    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    client = OpenAI(api_key=get_openai_api_key())
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an ESG risk analyst. Respond with JSON only.",
                },
                {
                    "role": "user",
                    "content": _build_llm_prompt(
                        article_title, article_content, company_name, industry
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("risk_assessor LLM failed (%s)", type(exc).__name__)
        return {}


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _materialize_risks(
    raw: dict[str, dict[str, int]],
    categories: list[str],
    industry: str,
) -> tuple[list[RiskScore], int]:
    """Convert LLM raw scores + ontology risk weights → list of RiskScore."""
    risks: list[RiskScore] = []
    queries = 0
    for category in categories:
        data = raw.get(category) or raw.get(category.lower()) or {}
        prob = int(data.get("probability", 0) or 0)
        exp = int(data.get("exposure", 0) or 0)
        if prob < 2 or exp < 2:
            continue
        raw_score = prob * exp
        weight = query_risk_weight(industry, category)
        queries += 1
        adjusted = raw_score * weight
        indicators = query_risk_indicators(category)
        queries += 1
        risks.append(
            RiskScore(
                category=category,
                probability=prob,
                exposure=exp,
                raw_score=raw_score,
                industry_weight=weight,
                adjusted_score=round(adjusted, 2),
                level=_classify_level(adjusted),
                lead_indicators=indicators.lead_indicators[:3],
                lag_indicators=indicators.lag_indicators[:3],
            )
        )
    return risks, queries


def assess_risk(
    article_title: str,
    article_content: str,
    company_name: str,
    industry: str,
    extraction: NLPExtraction | None = None,
    tags: ESGThemeTags | None = None,
) -> RiskAssessment:
    """Run the 10 ESG + 7 TEMPLES risk assessment for an article."""
    raw = _llm_score(article_title, article_content, company_name, industry)
    esg_raw = raw.get("esg", {}) or {}
    temples_raw = raw.get("temples", {}) or {}

    esg_cats = query_esg_risk_categories()
    temples_cats = query_temples_categories()
    esg_risks, q1 = _materialize_risks(esg_raw, esg_cats, industry)
    temples_risks, q2 = _materialize_risks(temples_raw, temples_cats, industry)

    merged = sorted(esg_risks + temples_risks, key=lambda r: r.adjusted_score, reverse=True)
    top5 = merged[:5]
    total_raw = sum(r.adjusted_score for r in merged)
    # Normalize: max possible = 10 categories × 25 (5×5) × 2.0 weight = 500; keep it simple
    aggregate = min(total_raw / 250.0, 1.0) if merged else 0.0

    return RiskAssessment(
        esg_risks=esg_risks,
        temples_risks=temples_risks,
        top_risks=top5,
        aggregate_score=round(aggregate, 3),
        ontology_queries=q1 + q2,
    )


# ---------------------------------------------------------------------------
# Lite (LLM-free) risk assessment for SECONDARY tier articles
# ---------------------------------------------------------------------------


# Theme → Risk and Theme → TEMPLES mappings are now sourced from ontology via
# query_theme_risk_map() and query_theme_temples_map() in intelligence.py.
# See knowledge_expansion.ttl: triggersRiskCategory / triggersTEMPLES triples.


# Severity bands for the lite scorer. Probability × exposure are derived
# from the article's NLP signals (sentiment, urgency, content type) without
# any LLM call.
def _lite_seed_pe(extraction: NLPExtraction, is_primary: bool) -> tuple[int, int]:
    """Seed (probability, exposure) for a category from NLP signals.

    The primary risk category for the article's theme gets the highest
    P × E. Secondary categories scale down by ~25 %.
    """
    sentiment = extraction.sentiment if extraction else 0
    urgency = (extraction.urgency if extraction else "low") or "low"
    content_type = (extraction.content_type if extraction else "narrative") or "narrative"

    # Base scores
    if urgency == "critical":
        base_p, base_e = 5, 5
    elif urgency == "high":
        base_p, base_e = 4, 4
    elif urgency == "medium":
        base_p, base_e = 3, 3
    else:
        base_p, base_e = 2, 3

    # Negative sentiment escalates exposure
    if sentiment <= -1:
        base_e = min(5, base_e + 1)
    if sentiment >= 1:
        base_e = max(1, base_e - 1)

    # Reputational / litigation content boosts exposure
    if content_type in ("reputational", "regulatory"):
        base_e = min(5, base_e + 1)

    # Secondary categories slightly damped
    if not is_primary:
        base_p = max(1, base_p - 1)

    return base_p, base_e


def assess_risk_lite(
    company_industry: str,
    extraction: NLPExtraction,
    themes: ESGThemeTags,
    relevance: Any,
) -> RiskAssessment:
    """Deterministic, ontology-driven risk assessment.

    No LLM cost. Uses:
      1. The article's primary theme → ontology SPARQL :func:`query_theme_risk_map`
         to seed which categories activate.
      2. NLP signals (sentiment, urgency, content_type) to seed P × E.
      3. Ontology SPARQL :func:`query_risk_weight` to amplify each
         category by the industry's risk multiplier.
      4. Ontology SPARQL :func:`query_risk_indicators` to attach lead /
         lag indicators per category.

    Designed to populate the risk panel for SECONDARY tier articles where
    the LLM-based ``assess_risk`` would be wasted budget.
    """
    primary_theme = (themes.primary_theme or "").lower().strip() if themes else ""
    secondary_labels = [
        (s.get("theme") or "").lower().strip()
        for s in (themes.secondary_themes if themes else [])
        if isinstance(s, dict)
    ]

    # Build category list from primary + secondary themes via ontology
    primary_esg = list(query_theme_risk_map(primary_theme)) if primary_theme else []
    primary_temples = list(query_theme_temples_map(primary_theme)) if primary_theme else []
    seen_esg: set[str] = set(primary_esg)
    seen_temples: set[str] = set(primary_temples)
    for sec in secondary_labels:
        for cat in query_theme_risk_map(sec):
            if cat not in seen_esg:
                primary_esg.append(cat)
                seen_esg.add(cat)
        for cat in query_theme_temples_map(sec):
            if cat not in seen_temples:
                primary_temples.append(cat)
                seen_temples.add(cat)

    if not primary_esg and not primary_temples:
        # Theme is unknown; fall back to a generic reputational + regulatory pair
        primary_esg = ["Reputational Risk", "Regulatory Risk"]
        primary_temples = ["Media", "Legal"]

    queries = 0
    esg_risks: list[RiskScore] = []
    for idx, cat in enumerate(primary_esg):
        prob, exp = _lite_seed_pe(extraction, is_primary=(idx == 0))
        weight = query_risk_weight(company_industry, cat)
        queries += 1
        adjusted = prob * exp * weight
        indicators = query_risk_indicators(cat)
        queries += 1
        esg_risks.append(
            RiskScore(
                category=cat,
                probability=prob,
                exposure=exp,
                raw_score=prob * exp,
                industry_weight=weight,
                adjusted_score=round(adjusted, 2),
                level=_classify_level(adjusted),
                lead_indicators=indicators.lead_indicators[:3],
                lag_indicators=indicators.lag_indicators[:3],
            )
        )

    temples_risks: list[RiskScore] = []
    for idx, cat in enumerate(primary_temples):
        prob, exp = _lite_seed_pe(extraction, is_primary=(idx == 0))
        weight = query_risk_weight(company_industry, cat)
        queries += 1
        adjusted = prob * exp * weight
        indicators = query_risk_indicators(cat)
        queries += 1
        temples_risks.append(
            RiskScore(
                category=cat,
                probability=prob,
                exposure=exp,
                raw_score=prob * exp,
                industry_weight=weight,
                adjusted_score=round(adjusted, 2),
                level=_classify_level(adjusted),
                lead_indicators=indicators.lead_indicators[:3],
                lag_indicators=indicators.lag_indicators[:3],
            )
        )

    merged = sorted(esg_risks + temples_risks, key=lambda r: r.adjusted_score, reverse=True)
    top5 = merged[:5]
    total_raw = sum(r.adjusted_score for r in merged)
    aggregate = min(total_raw / 250.0, 1.0) if merged else 0.0

    return RiskAssessment(
        esg_risks=esg_risks,
        temples_risks=temples_risks,
        top_risks=top5,
        aggregate_score=round(aggregate, 3),
        ontology_queries=queries,
    )
