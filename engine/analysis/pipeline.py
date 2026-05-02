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
import re
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

    # Stage outputs (themes/event/relevance are None for cross-entity-rejected
    # articles where the pipeline short-circuits after NLP — see Phase 22.1)
    nlp: NLPExtraction
    themes: ESGThemeTags | None = None
    event: EventClassification | None = None
    relevance: RelevanceScore | None = None
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

    # Phase 22.4 — Persist a truncated copy of the raw article body so the
    # output verifier can ground "(from article)" tags + reused-number
    # audits against actual source text (not just NLP-derived narrative
    # fragments). Capped at 6000 chars; enough for ~3-4 KB articles which
    # cover ~95% of corpus.
    article_content: str = ""

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
            # Phase 22.4 — round-trip the raw article body so any path that
            # serializes/deserializes PipelineResult before insight generation
            # still has the grounding signal for source-tag verification.
            "article_content": self.article_content,
        }


# Phase 22.1 — Cross-entity attribution gate.
#
# Surfaced from a live audit (Adani Energy Solutions ESG-rating article
# viewed under the Adani Power dashboard). The article was about Adani
# Energy Solutions (NSE: ADANIENSOL, transmission), but the analysis
# pipeline applied it to Adani Power (NSE: ADANIPOWER, generation),
# producing a confidently-wrong CEO board paragraph "Adani Power has
# received an inaugural ESG rating of 86.8/100" that would embarrass
# the team in front of analysts.
#
# Root cause: the news_fetcher's ESG keyword queries match articles
# mentioning ANY Adani-group ESG event. The pipeline then runs NLP, sees
# "Adani Energy Solutions" as the primary entity, but downstream stages
# blindly use `company.slug = adani-power` for cascade calibration,
# perspective generation, and recommendations.
#
# Gate logic:
#  1. Build the target's name variants: full name, name without "Limited"/
#     "Ltd", slug tokens, capitalized slug tokens.
#  2. Check if any variant appears in `nlp.entities` OR in the title +
#     first 2 KB of the article body. If yes → not cross-entity, proceed.
#  3. Otherwise check if the most-mentioned entity is a sibling group
#     company (shares the first slug token, e.g. "adani-*" siblings).
#     If yes → reject with reason "cross_entity: <sibling-name>".
#  4. Else → not cross-entity (the relevance scorer's off-topic detection
#     handles the rest).

_CORP_SUFFIX_RE = re.compile(r"\s+(?:limited|ltd\.?|inc\.?|plc\.?|llc\.?)$", re.IGNORECASE)


def _detect_cross_entity(
    nlp: NLPExtraction,
    title: str,
    content: str,
    company: Company,
) -> tuple[bool, str]:
    """Detect when the article is about a sibling group entity, not the target.

    Returns ``(is_cross_entity, reason)``. When True, callers should reject
    the article BEFORE running expensive downstream stages — the analysis
    would be confidently wrong about which entity the impact applies to.
    """
    name_lower = (company.name or "").lower().strip()
    if not name_lower:
        return False, ""
    name_no_suffix = _CORP_SUFFIX_RE.sub("", name_lower).strip()
    slug_tokens = [t for t in (company.slug or "").lower().split("-") if t]

    # Build candidate variants of the target's name. Include both the full
    # name (with/without corporate suffix) and the slug tokens (so e.g.
    # "icici-bank" matches "ICICI" or "Bank" alone in shorthand mentions).
    variants: set[str] = {name_lower, name_no_suffix}
    if len(slug_tokens) >= 2:
        # Whole multi-word slug joined ("adani power")
        variants.add(" ".join(slug_tokens))
    # Don't add single-token slug parts (e.g. "adani") because they would
    # match sibling entities ("Adani Energy Solutions") and defeat the
    # point of the gate.

    # 1. Check NLP entities (case-insensitive substring match)
    entities_lc = [(e or "").lower() for e in (nlp.entities or [])]
    for v in variants:
        if not v:
            continue
        for e in entities_lc:
            if v in e or e in v:
                return False, ""

    # 2. Check title + first 2 KB of body
    haystack = (title or "" + " " + (content or "")[:2000]).lower()
    for v in variants:
        if v and v in haystack:
            return False, ""

    # The target company is NOT mentioned in entities OR title OR body.
    # Check if the most-mentioned entity is a sibling group company —
    # shares the slug's first token (e.g. both start with "adani").
    if not slug_tokens:
        return False, ""
    group_prefix = slug_tokens[0]

    # Don't fire on generic prefixes that aren't real conglomerate stems
    # (e.g. "yes" from "yes-bank" would falsely match "Yes Capital LLC").
    # Indian-conglomerate prefixes worth gating on:
    _GROUP_PREFIXES = {
        "adani", "tata", "reliance", "mahindra", "birla", "aditya", "bajaj",
        "godrej", "wipro", "infosys", "icici", "hdfc", "axis", "kotak",
        "larsen", "tcs", "vedanta", "jsw", "hero", "asian", "ultratech",
        "marico", "britannia", "biocon", "torrent", "havells", "ambuja",
    }
    if group_prefix not in _GROUP_PREFIXES:
        return False, ""

    sibling: str = ""
    for entity in (nlp.entities or [])[:5]:
        e_lc = (entity or "").lower()
        if e_lc and e_lc.startswith(group_prefix) and name_lower not in e_lc:
            # Confirm it's a different entity by checking it's not just a
            # variant of the target (e.g. target "Tata Power Company Limited"
            # vs entity "Tata Power" — should NOT trigger).
            if name_no_suffix in e_lc or e_lc in name_no_suffix:
                continue
            sibling = entity
            break

    if sibling:
        return True, (
            f"cross_entity: article is about '{sibling}' (sibling group "
            f"company) but the dashboard target is '{company.name}'. "
            f"Reject to prevent confident-wrong attribution."
        )
    return False, ""


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

    # Phase 22.1 — Cross-entity attribution gate. When the article is
    # actually about a sibling group company (e.g. Adani Energy Solutions
    # filed under Adani Power's feed), reject before running stages 2-12.
    # Saves LLM dollars and prevents confident-wrong CFO/CEO output.
    is_cross, cross_reason = _detect_cross_entity(nlp, title, content, company)
    if is_cross:
        result = PipelineResult(
            article_id=article.get("id", ""),
            title=title,
            url=article.get("url", ""),
            source=source,
            published_at=article.get("published_at", ""),
            company_slug=company.slug,
            nlp=nlp,
            themes=None,
            event=None,
            relevance=None,
            tier=TIER_REJECTED,
            rejected=True,
            rejection_reason=cross_reason,
            stages_executed=stages + ["cross_entity_gate"],
            ontology_query_count=ontology_queries,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            article_content=(content or "")[:6000],
        )
        logger.info(
            "pipeline: %s REJECTED (cross_entity for %s)",
            article.get("id", "")[:8],
            company.slug,
        )
        return result

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
        article_content=(content or "")[:6000],
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
        # Phase 23 reviewer fix — explicit framework jurisdiction wins
        # over the country/region heuristic so a UK bank doesn't get
        # tagged as EU via the "europ" substring match.
        framework_region=company.framework_region,
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
