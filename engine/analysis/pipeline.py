"""Analysis pipeline — 12-stage ontology-driven orchestrator.

Runs one article through:

1.  NLP extraction        — OpenAI
2.  ESG theme tagging     — OpenAI + ontology
3.  Event classification  — ontology rules
4.  Relevance scoring     — ontology materiality
--- gate: relevance < 4 → REJECTED (stop) ---
5.  Causal chain BFS      — ontology graph (entities + theme seed)
6.  Framework matching    — ontology
7.  Stakeholder mapping   — ontology (runs for ALL non-rejected tiers)
8.  SDG mapping           — ontology (runs for ALL non-rejected tiers)
9.  Risk assessment       — HOME: full LLM+ontology, SECONDARY: ontology-only lite

Deep insight generation, perspective transformation, and REREACT
recommendations live in Phase 5+ orchestration outside this module.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.analysis.framework_matcher import FrameworkMatch, match_frameworks
from engine.analysis.relevance_scorer import (
    TIER_HOME,
    TIER_REJECTED,
    RelevanceScore,
    score_relevance,
)
from engine.analysis.risk_assessor import RiskAssessment, assess_risk, assess_risk_lite
from engine.config import Company
from engine.nlp.event_classifier import EventClassification, classify_event
from engine.nlp.extractor import NLPExtraction, run_nlp_pipeline
from engine.nlp.theme_tagger import ESGThemeTags, tag_esg_themes
from engine.ontology.causal_engine import (
    CausalPath,
    find_causal_chains,
    find_theme_causal_chains,
)
from engine.ontology.intelligence import (
    query_sdgs_for_topic,
    query_stakeholders_for_topic,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    article_id: str
    title: str
    url: str
    source: str
    published_at: str
    company_slug: str

    # Stage outputs
    nlp: NLPExtraction
    themes: ESGThemeTags
    event: EventClassification
    relevance: RelevanceScore
    causal_chains: list[CausalPath] = field(default_factory=list)
    frameworks: list[FrameworkMatch] = field(default_factory=list)
    risk: RiskAssessment | None = None
    stakeholders: list[str] = field(default_factory=list)
    sdgs: list[str] = field(default_factory=list)

    # Meta
    tier: str = ""
    rejected: bool = False
    rejection_reason: str = ""
    ontology_query_count: int = 0
    elapsed_seconds: float = 0.0
    stages_executed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _safe(obj: Any) -> Any:
            if obj is None:
                return None
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            if isinstance(obj, list):
                return [_safe(x) for x in obj]
            return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj

        return {
            "article_id": self.article_id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "company_slug": self.company_slug,
            "tier": self.tier,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "ontology_query_count": self.ontology_query_count,
            "elapsed_seconds": self.elapsed_seconds,
            "stages_executed": self.stages_executed,
            "nlp": _safe(self.nlp),
            "themes": _safe(self.themes),
            "event": _safe(self.event),
            "relevance": _safe(self.relevance),
            "causal_chains": [
                {
                    "nodes": p.nodes,
                    "edges": p.edges,
                    "hops": p.hops,
                    "relationship_type": p.relationship_type,
                    "impact_score": p.impact_score,
                    "explanation": p.explanation,
                }
                for p in self.causal_chains
            ],
            "frameworks": [fm.to_dict() for fm in self.frameworks],
            "risk": _safe(self.risk),
            "stakeholders": self.stakeholders,
            "sdgs": self.sdgs,
        }


def process_article(
    article: dict[str, Any],
    company: Company,
) -> PipelineResult:
    """Run the full analysis pipeline for a single article + company.

    ``article`` is the ingested JSON shape (title, content, source, url, etc.).
    """
    started = time.perf_counter()
    stages: list[str] = []
    ontology_queries = 0

    title = article.get("title", "")
    content = article.get("content") or article.get("summary") or ""
    source = article.get("source", "")

    # Stage 1: NLP extraction
    stages.append("nlp_extraction")
    nlp = run_nlp_pipeline(title, content, source)

    # Stage 2: ESG theme tagging
    stages.append("theme_tagging")
    themes = tag_esg_themes(title, content)

    # Stage 3: Event classification (rule-based, ontology-sourced rules)
    stages.append("event_classification")
    event = classify_event(title, content, theme=themes.primary_theme)
    ontology_queries += 1  # theme taxonomy + event rules queries are implicit but count once

    # Stage 4: Relevance scoring (ontology materiality)
    stages.append("relevance_scoring")
    relevance = score_relevance(nlp, themes, company.industry)
    ontology_queries += relevance.ontology_queries

    result = PipelineResult(
        article_id=article.get("id", ""),
        title=title,
        url=article.get("url", ""),
        source=source,
        published_at=article.get("published_at", ""),
        company_slug=company.slug,
        nlp=nlp,
        themes=themes,
        event=event,
        relevance=relevance,
        tier=relevance.tier,
        stages_executed=stages,
    )

    # Gate: reject early
    if relevance.tier == TIER_REJECTED:
        result.rejected = True
        result.rejection_reason = relevance.rejection_reason
        result.ontology_query_count = ontology_queries
        result.elapsed_seconds = round(time.perf_counter() - started, 3)
        logger.info(
            "pipeline: %s REJECTED (%s)",
            article.get("id", "")[:8],
            relevance.rejection_reason,
        )
        return result

    # Stage 5: Causal chain BFS (run for both HOME and SECONDARY tiers).
    # Two seed strategies are combined:
    #  (a) Entity-based BFS — walks the instance graph from each NER entity
    #      to the target company through 0-4 hops of typed relationships.
    #  (b) Theme-based semantic chains — walks topic → industry → company
    #      and topic → triggersFramework → company. This catches macro/
    #      sentiment articles where no specific entity exists in the graph.
    stages.append("causal_chains")
    seeds = list(nlp.entities)[:3]
    if not seeds:
        seeds = [company.name]
    all_paths: list[CausalPath] = []
    for seed in seeds:
        paths = find_causal_chains(seed, company.slug)
        ontology_queries += 2  # entity resolution + BFS
        all_paths.extend(paths)

    # Theme-driven semantic chains (new — Phase 13 fix)
    if themes.primary_theme:
        theme_paths = find_theme_causal_chains(themes.primary_theme, company.slug)
        ontology_queries += 2  # topic resolution + 2 SPARQL traversals
        all_paths.extend(theme_paths)

    # Keep unique top 5 paths by impact score
    seen_sigs: set[tuple] = set()
    unique_paths: list[CausalPath] = []
    for path in sorted(all_paths, key=lambda p: p.impact_score, reverse=True):
        sig = tuple(path.node_uris)
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        unique_paths.append(path)
        if len(unique_paths) >= 5:
            break
    result.causal_chains = unique_paths

    # Stage 6: Framework matching
    stages.append("framework_matching")
    frameworks, fq = match_frameworks(
        themes,
        company_industry=company.industry,
        company_country=company.headquarter_country,
        company_region=company.headquarter_region,
        market_cap=company.market_cap,
    )
    result.frameworks = frameworks
    ontology_queries += fq

    # Stage 7: Stakeholder mapping (ontology SPARQL — cheap, runs for ALL tiers)
    stages.append("stakeholder_mapping")
    stakeholders = query_stakeholders_for_topic(themes.primary_theme)
    ontology_queries += 1
    result.stakeholders = stakeholders

    # Stage 8: SDG mapping (ontology SPARQL — cheap, runs for ALL tiers)
    stages.append("sdg_mapping")
    sdgs = query_sdgs_for_topic(themes.primary_theme)
    ontology_queries += 1
    result.sdgs = sdgs

    # Stage 9: Risk assessment.
    # HOME tier → full LLM + ontology assessment (expensive but most accurate).
    # SECONDARY tier → ontology-only deterministic inference (no LLM, ~5ms).
    if relevance.tier == TIER_HOME:
        stages.append("risk_assessment")
        risk = assess_risk(
            article_title=title,
            article_content=content,
            company_name=company.name,
            industry=company.industry,
            extraction=nlp,
            tags=themes,
        )
        ontology_queries += risk.ontology_queries
        result.risk = risk
    else:
        stages.append("risk_assessment_lite")
        risk = assess_risk_lite(
            company_industry=company.industry,
            extraction=nlp,
            themes=themes,
            relevance=relevance,
        )
        ontology_queries += risk.ontology_queries
        result.risk = risk

    result.ontology_query_count = ontology_queries
    result.elapsed_seconds = round(time.perf_counter() - started, 3)
    logger.info(
        "pipeline: %s tier=%s score=%.1f stages=%s queries=%s elapsed=%.2fs",
        article.get("id", "")[:8],
        result.tier,
        relevance.adjusted_total,
        len(stages),
        ontology_queries,
        result.elapsed_seconds,
    )
    return result
