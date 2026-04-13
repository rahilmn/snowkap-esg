"""5-dimension relevance scoring (ontology-driven).

Reuses the existing NLP + theme extraction and queries the ontology for
the materiality weight per (topic × industry). No hardcoded
materiality dicts anywhere — all knowledge lives in the graph.

Output tier:
- HOME (total >= 7 AND esg_correlation > 0) — deep insight generation
- SECONDARY (4-6) — feed-only
- REJECTED (< 4 OR esg_correlation == 0) — filtered out
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.nlp.extractor import NLPExtraction
from engine.nlp.theme_tagger import ESGThemeTags
from engine.ontology.intelligence import query_materiality_weight

logger = logging.getLogger(__name__)

TIER_HOME = "HOME"
TIER_SECONDARY = "SECONDARY"
TIER_REJECTED = "REJECTED"

HOME_THRESHOLD = 6
SECONDARY_THRESHOLD = 3


@dataclass
class RelevanceScore:
    total: int  # 0-10
    tier: str  # HOME | SECONDARY | REJECTED

    # 5 dimensions (0-2 each)
    esg_correlation: int
    financial_impact: int
    compliance_risk: int
    supply_chain_impact: int
    people_impact: int

    materiality_weight: float  # 0.0-1.0, from ontology
    adjusted_total: float  # total × materiality weight
    rejection_reason: str = ""
    ontology_queries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 5-D scoring rubric
# ---------------------------------------------------------------------------


def _score_esg_correlation(extraction: NLPExtraction, tags: ESGThemeTags) -> int:
    """0-2 based on how strongly the article maps to an ESG topic."""
    if tags.method == "llm" and tags.confidence >= 0.7:
        return 2
    if tags.primary_theme and tags.confidence >= 0.4:
        return 1
    if tags.primary_theme:
        return 1
    return 0


def _score_financial_impact(extraction: NLPExtraction) -> int:
    """0-2 based on presence of financial signals."""
    signal = extraction.financial_signal or {}
    amount = signal.get("amount")
    if amount and isinstance(amount, (int, float)) and amount > 0:
        return 2
    if extraction.content_type == "financial":
        return 2
    text = (
        extraction.narrative_core_claim + " " + extraction.narrative_implied_causation
    ).lower()
    financial_keywords = (
        "revenue", "cost", "margin", "ebitda", "profit", "loss", "investment",
        "capex", "valuation", "wealth", "capital", "fund", "bond", "equity",
        "billion", "million", "crore", "lakh",
    )
    if any(k in text for k in financial_keywords):
        return 1
    # Any company news with an operational content type still has indirect financial signal
    if extraction.content_type in ("operational", "reputational"):
        return 1
    return 0


def _score_compliance_risk(extraction: NLPExtraction) -> int:
    """0-2 based on regulatory references + content type."""
    if extraction.regulatory_references:
        return 2
    if extraction.content_type == "regulatory":
        return 2
    if extraction.urgency in ("critical", "high"):
        return 1
    text = (
        extraction.narrative_core_claim + " " + extraction.narrative_implied_causation
    ).lower()
    compliance_keywords = (
        "regulat", "sebi", "rbi", "brsr", "csrd", "disclosure", "compliance",
        "mandate", "filing", "penalty", "fine", "audit", "sanction",
    )
    if any(k in text for k in compliance_keywords):
        return 1
    # Phase 14: Reputational → regulatory escalation (NGO naming → latent compliance risk)
    if extraction.content_type == "reputational":
        escalation_kw = (
            "ngo", "greenpeace", "oxfam", "amnesty", "dirty list", "polluter",
            "boycott", "campaign", "activist", "watchdog", "naming", "shaming",
        )
        if any(k in text for k in escalation_kw):
            return 1
        if extraction.sentiment <= -1:
            return 1
    return 0


def _score_supply_chain_impact(extraction: NLPExtraction, tags: ESGThemeTags) -> int:
    """0-2 based on supply chain signals."""
    text = (extraction.narrative_core_claim + " " + extraction.narrative_implied_causation).lower()
    sc_topics = {"supply chain labor", "supply_chain"}
    has_topic = any(
        t.lower() in sc_topics
        for t in ([tags.primary_theme] + [s.get("theme", "") for s in tags.secondary_themes])
    )
    if has_topic:
        return 2
    if any(k in text for k in ("supplier", "upstream", "downstream", "scope 3", "tier 1", "tier 2")):
        return 1
    return 0


def _score_people_impact(extraction: NLPExtraction, tags: ESGThemeTags) -> int:
    """0-2 based on human capital / community / safety signals."""
    text = (
        extraction.narrative_core_claim
        + " "
        + extraction.narrative_stakeholder_framing
    ).lower()
    if tags.primary_pillar == "S":
        return 2
    if any(k in text for k in ("employee", "worker", "community", "safety", "injury", "attrition")):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def score_relevance(
    extraction: NLPExtraction,
    tags: ESGThemeTags,
    company_industry: str,
) -> RelevanceScore:
    """Score an article's relevance to a company on a 5-dimension rubric.

    The total score is adjusted by the ontology-queried materiality weight for
    the (primary_theme × company_industry) pair. If materiality is < 0.4,
    the final score is dampened by 40 %; if ≥ 0.8, no dampening.
    """
    esg = _score_esg_correlation(extraction, tags)
    fin = _score_financial_impact(extraction)
    comp = _score_compliance_risk(extraction)
    sc = _score_supply_chain_impact(extraction, tags)
    ppl = _score_people_impact(extraction, tags)

    total = esg + fin + comp + sc + ppl

    # Query ontology for materiality weight. This is the ontology-driven
    # replacement for the legacy `MATERIALITY_MAP` Python dict.
    weight = query_materiality_weight(tags.primary_theme, company_industry)
    ontology_queries = 1

    if weight >= 0.8:
        adjusted = float(total)
    elif weight >= 0.4:
        adjusted = total * 0.85
    else:
        adjusted = total * 0.6

    # Tier assignment
    rejection_reason = ""
    if esg == 0:
        tier = TIER_REJECTED
        rejection_reason = "No ESG correlation"
    elif adjusted < SECONDARY_THRESHOLD:
        tier = TIER_REJECTED
        rejection_reason = f"Adjusted score {adjusted:.1f} below threshold {SECONDARY_THRESHOLD}"
    elif adjusted >= HOME_THRESHOLD:
        tier = TIER_HOME
    else:
        tier = TIER_SECONDARY

    return RelevanceScore(
        total=total,
        tier=tier,
        esg_correlation=esg,
        financial_impact=fin,
        compliance_risk=comp,
        supply_chain_impact=sc,
        people_impact=ppl,
        materiality_weight=weight,
        adjusted_total=round(adjusted, 2),
        rejection_reason=rejection_reason,
        ontology_queries=ontology_queries,
    )
