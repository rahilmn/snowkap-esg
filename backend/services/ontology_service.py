"""Ontology service — full Jena SPARQL, causal chain engine, ontology management.

Per CLAUDE.md:
- Base ontology: sustainability.ttl (OWL2)
- Each tenant gets a named graph: urn:snowkap:tenant:{tenant_id}
- SPARQL queries always scoped to tenant named graph
- Causal chain traversal: BFS, max 4 hops, decay scoring (1.0 → 0.7 → 0.4 → 0.2)
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.company import Company
from backend.models.news import Article, ArticleScore, CausalChain
from backend.ontology.causal_engine import (
    CausalPath,
    calculate_impact,
    find_all_impacts_for_entity,
    find_causal_chains,
)
from backend.ontology.entity_extractor import (
    ExtractionResult,
    extract_and_classify,
    resolve_entities_against_graph,
)
from backend.ontology.geographic_intelligence import find_geographic_matches
from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Impact decay per hop — per MASTER_BUILD_PLAN Phase 3.2
HOP_DECAY = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.1}

# ---------------------------------------------------------------------------
# Rule-based framework mapper — fills framework_hits when OpenAI extraction
# returns nothing (common for general news articles).
# ---------------------------------------------------------------------------

# Topic keyword → framework codes
_TOPIC_FRAMEWORK_MAP: dict[str, list[str]] = {
    "emissions": ["GRI:305", "BRSR:P6", "TCFD:Metrics", "ESRS:E1", "CDP:Climate"],
    "carbon": ["GRI:305", "BRSR:P6", "TCFD:Metrics", "ESRS:E1", "CDP:Climate"],
    "climate": ["GRI:305", "BRSR:P6", "TCFD:Metrics", "ESRS:E1", "CDP:Climate"],
    "greenhouse": ["GRI:305", "BRSR:P6", "TCFD:Metrics", "ESRS:E1", "CDP:Climate"],
    "water": ["GRI:303", "BRSR:P6", "ESRS:E3", "CDP:Water"],
    "effluent": ["GRI:303", "BRSR:P6", "ESRS:E3", "CDP:Water"],
    "biodiversity": ["GRI:304", "BRSR:P6", "ESRS:E4"],
    "ecosystem": ["GRI:304", "BRSR:P6", "ESRS:E4"],
    "waste": ["GRI:306", "BRSR:P6", "ESRS:E5"],
    "circular economy": ["GRI:306", "BRSR:P6", "ESRS:E5"],
    "energy": ["GRI:302", "BRSR:P6", "TCFD:Metrics"],
    "renewable": ["GRI:302", "BRSR:P6", "TCFD:Metrics"],
    "safety": ["GRI:403", "BRSR:P3", "ESRS:S1"],
    "health": ["GRI:403", "BRSR:P3", "ESRS:S1"],
    "occupational": ["GRI:403", "BRSR:P3", "ESRS:S1"],
    "diversity": ["GRI:405", "BRSR:P5", "ESRS:S1"],
    "inclusion": ["GRI:405", "BRSR:P5", "ESRS:S1"],
    "gender": ["GRI:405", "BRSR:P5", "ESRS:S1"],
    "corruption": ["GRI:205", "BRSR:P1", "ESRS:G1"],
    "bribery": ["GRI:205", "BRSR:P1", "ESRS:G1"],
    "ethics": ["GRI:205", "BRSR:P1", "ESRS:G1"],
    "community": ["GRI:413", "BRSR:P8", "ESRS:S3"],
    "social": ["GRI:413", "BRSR:P8", "ESRS:S3"],
    "supply chain": ["GRI:414", "BRSR:P5", "ESRS:S2"],
    "supplier": ["GRI:414", "BRSR:P5", "ESRS:S2"],
    "privacy": ["GRI:418", "BRSR:P9", "ESRS:S4"],
    "data": ["GRI:418", "BRSR:P9", "ESRS:S4"],
    "employee": ["GRI:401", "BRSR:P3", "ESRS:S1"],
    "labor": ["GRI:401", "BRSR:P3", "ESRS:S1"],
    "labour": ["GRI:401", "BRSR:P3", "ESRS:S1"],
    "workforce": ["GRI:401", "BRSR:P3", "ESRS:S1"],
    "sustainability": ["BRSR:P6", "TCFD:Strategy", "ESRS:E1"],
    "esg": ["BRSR:P6", "TCFD:Strategy", "ESRS:E1"],
    "governance": ["TCFD:Governance", "BRSR:P1", "ESRS:G1"],
    "board": ["TCFD:Governance", "BRSR:P1", "ESRS:G1"],
}

# Pillar-level fallback frameworks
_PILLAR_FRAMEWORK_MAP: dict[str, list[str]] = {
    "E": ["GRI:305", "BRSR:P6", "TCFD:Metrics", "ESRS:E1", "CDP:Climate"],
    "S": ["GRI:403", "GRI:401", "BRSR:P3", "ESRS:S1"],
    "G": ["GRI:205", "TCFD:Governance", "BRSR:P1", "ESRS:G1"],
}


def infer_frameworks_from_content(
    title: str | None = None,
    summary: str | None = None,
    esg_topics: list[str] | None = None,
    esg_pillar: str | None = None,
) -> list[str]:
    """Map ESG topics, pillar, and text content to relevant framework codes.

    Used as a fallback when OpenAI entity extraction returns no framework
    mentions (typical for general news articles).
    """
    matched: set[str] = set()

    # 1. Match from explicit esg_topics (highest signal)
    for topic in (esg_topics or []):
        topic_lower = topic.lower().replace("_", " ")
        for keyword, frameworks in _TOPIC_FRAMEWORK_MAP.items():
            if keyword in topic_lower:
                matched.update(frameworks)

    # 2. Scan title + summary for keyword hits
    text_blob = " ".join(filter(None, [title, summary])).lower()
    for keyword, frameworks in _TOPIC_FRAMEWORK_MAP.items():
        if keyword in text_blob:
            matched.update(frameworks)

    # 3. Pillar-level fallback if nothing matched yet
    if not matched and esg_pillar:
        matched.update(_PILLAR_FRAMEWORK_MAP.get(esg_pillar, []))

    return sorted(matched)


def get_tenant_graph_uri(tenant_id: str) -> str:
    """Get the named graph URI for a tenant per CLAUDE.md convention."""
    return f"urn:snowkap:tenant:{tenant_id}"


def validate_sparql_query(query: str) -> bool:
    """Stage 8.3: Validate a SPARQL query against the whitelist.

    Returns True if the query is read-only and safe, False if it contains
    destructive operations. Extracted for testability.
    """
    query_stripped = query.strip()
    query_upper = query_stripped.upper()

    # Skip PREFIX declarations to find the actual query type
    query_body = query_stripped
    while query_body.upper().startswith("PREFIX"):
        newline_idx = query_body.find("\n")
        if newline_idx == -1:
            break
        query_body = query_body[newline_idx + 1:].strip()

    query_body_upper = query_body.upper()
    if not (query_body_upper.startswith("SELECT") or query_body_upper.startswith("CONSTRUCT")
            or query_body_upper.startswith("ASK") or query_body_upper.startswith("DESCRIBE")):
        return False

    # Block destructive keywords even within subqueries
    dangerous_keywords = {"DROP", "DELETE", "INSERT", "CLEAR", "LOAD", "CREATE", "MOVE", "COPY", "ADD"}
    for keyword in dangerous_keywords:
        if keyword in query_upper:
            return False

    # BUG-16: Block FILTER injection — disallow function calls and nested subqueries
    # that could be abused via unescaped user-provided FILTER content
    dangerous_filter_keywords = {"BIND", "SERVICE"}
    for keyword in dangerous_filter_keywords:
        if keyword in query_upper:
            return False

    # Block nested subqueries within FILTER clauses (SELECT inside FILTER)
    import re
    if re.search(r'FILTER\s*\(.*\bSELECT\b', query_upper):
        return False

    return True


async def execute_sparql(tenant_id: str, query: str) -> dict:
    """Execute a SPARQL query against the tenant's named graph in Jena Fuseki.

    Per CLAUDE.md Rule #5: NEVER expose Jena SPARQL directly — always proxy.
    """
    graph_uri = get_tenant_graph_uri(tenant_id)
    logger.info("sparql_execute", tenant_id=tenant_id, graph=graph_uri, query_len=len(query))

    if not validate_sparql_query(query):
        logger.warning("sparql_query_blocked", tenant_id=tenant_id)
        return {"error": "Only read-only SPARQL queries (SELECT, CONSTRUCT, ASK, DESCRIBE) are allowed"}

    return await jena_client.query(query, tenant_id=tenant_id)


async def analyze_article_impact(
    article_id: str,
    tenant_id: str,
    db: AsyncSession,
) -> list[dict]:
    """Full article impact analysis pipeline.

    Per MASTER_BUILD_PLAN Part 1 flow:
    1. Extract entities from article
    2. Resolve entities against Jena graph
    3. For each resolved entity, find causal chains to tenant's companies
    4. Score impacts with geographic intelligence overlay
    5. Store results in causal_chains and article_scores tables
    """
    # Get article
    result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.tenant_id == tenant_id,
        )
    )
    article = result.scalar_one_or_none()
    if not article:
        return []

    # ── v2.0 Module 1: NLP Pipeline (MUST run before any scoring) ──
    nlp_data = None
    try:
        from backend.services.nlp_pipeline import run_nlp_pipeline, _is_non_english, _translate_if_needed

        # Translate non-English content BEFORE NLP analysis and store translation
        article_title = article.title
        article_content = article.content or article.summary or ""
        if _is_non_english(article_title) or _is_non_english(article_content):
            translated_content, translated_title = await _translate_if_needed(
                article_title, article_content,
            )
            # Store original as metadata, replace with English for all downstream processing
            article.nlp_extraction = article.nlp_extraction or {}
            if isinstance(article.nlp_extraction, dict):
                article.nlp_extraction["original_language_title"] = article.title
                article.nlp_extraction["original_language_content"] = (article.content or "")[:500]
            article.title = translated_title
            article.content = translated_content
            article.summary = translated_title  # Update summary too
            article_title = translated_title
            article_content = translated_content

        nlp_result = await run_nlp_pipeline(
            article_title=article_title,
            article_content=article_content,
            article_source=article.source,
        )
        nlp_data = nlp_result.to_dict()
        article.nlp_extraction = nlp_data
    except Exception as e:
        logger.warning("v2_nlp_pipeline_skipped", error=str(e))

    # Step 1+2: Extract and classify (existing entity extraction)
    try:
        extraction = await extract_and_classify(article.title, article.content or article.summary or "")
    except Exception as e:
        logger.error("entity_extraction_crashed", article_id=article_id, error=str(e))
        extraction = ExtractionResult(entities=[])  # Empty fallback — pipeline continues

    # Resolve entities against Jena
    try:
        resolved_entities = await resolve_entities_against_graph(extraction.entities, tenant_id)
    except Exception as e:
        logger.error("entity_resolution_crashed", article_id=article_id, error=str(e))
        resolved_entities = []

    # ── v2.0 Module 3: ESG Theme Tagging ──
    esg_themes_data = None
    try:
        from backend.services.esg_theme_tagger import tag_esg_themes
        # Fetch company industry for sector-aware theme tagging
        _comp_industry = None
        try:
            _comp_q = await db.execute(select(Company).where(Company.tenant_id == tenant_id).limit(1))
            _comp = _comp_q.scalar_one_or_none()
            _comp_industry = _comp.industry if _comp else None
        except Exception as exc:
            logger.warning("esg_theme_tag_company_lookup_failed", tenant_id=tenant_id, error=str(exc))
        esg_tags = await tag_esg_themes(
            title=article.title,
            content=article.content or article.summary or "",
            esg_pillar=extraction.esg_pillar,
            topics=extraction.esg_topics,
            company_industry=_comp_industry,
        )
        esg_themes_data = esg_tags.to_dict()
        article.esg_themes = esg_themes_data
    except Exception as e:
        logger.warning("v2_esg_theme_tagging_skipped", error=str(e))

    # Stage 3.1: Capture frameworks from extraction
    extraction_frameworks = extraction.frameworks_mentioned or []

    # Fallback: rule-based framework inference when extraction returns none
    if not extraction_frameworks:
        extraction_frameworks = infer_frameworks_from_content(
            title=article.title,
            summary=article.summary,
            esg_topics=extraction.esg_topics,
            esg_pillar=extraction.esg_pillar,
        )
        if extraction_frameworks:
            logger.info(
                "frameworks_inferred_by_rules",
                article_id=article_id,
                count=len(extraction_frameworks),
                frameworks=extraction_frameworks,
            )

    # Update article with extraction results
    article.entities = [
        {"text": e.text, "type": e.entity_type, "uri": e.resolved_uri}
        for e in resolved_entities
    ]
    article.sentiment = extraction.sentiment
    article.esg_pillar = extraction.esg_pillar

    # Phase 1C: Populate enhanced sentiment + criticality fields
    article.sentiment_score = extraction.sentiment_score
    article.sentiment_confidence = extraction.sentiment_confidence
    article.aspect_sentiments = extraction.aspect_sentiments

    # Fix sentiment label in nlp_extraction to match the float score from entity extractor
    # NLP pipeline uses integer -2 to +2, entity extractor uses float -1.0 to +1.0
    if nlp_data and extraction.sentiment_score is not None:
        s = extraction.sentiment_score
        if s <= -0.7:
            label = "STRONGLY_NEGATIVE"
        elif s <= -0.3:
            label = "NEGATIVE"
        elif s >= 0.7:
            label = "STRONGLY_POSITIVE"
        elif s >= 0.3:
            label = "POSITIVE"
        else:
            label = "NEUTRAL"
        nlp_data["sentiment"]["label"] = label
        nlp_data["sentiment"]["score"] = round(s, 2)
        article.nlp_extraction = nlp_data
    article.content_type = extraction.content_type
    article.urgency = extraction.urgency
    article.time_horizon = extraction.time_horizon
    article.reversibility = extraction.reversibility
    article.stakeholder_impact = extraction.stakeholder_impact
    if extraction.financial_signal_detail:
        article.financial_signal = extraction.financial_signal_detail
    if extraction.climate_events:
        article.climate_events = extraction.climate_events

    # Phase 1: 5D Relevance Scoring + Content Quality Gate
    from backend.services.relevance_scorer import parse_relevance_from_llm
    relevance = parse_relevance_from_llm({"relevance": extraction.relevance_data}) if extraction.relevance_data else None
    if relevance:
        article.relevance_score = relevance.total
        article.relevance_breakdown = relevance.to_dict()

        # Enhancement 6: SASB-aligned materiality adjustment
        # Adjusts relevance score based on how material the article's ESG theme
        # is for the company's industry. Runs AFTER base scoring, BEFORE tier classification.
        try:
            from backend.services.materiality_map import apply_materiality_adjustment

            _mat_industry = _comp_industry  # Already fetched during ESG theme tagging
            _mat_theme = (
                esg_themes_data.get("primary_theme") if isinstance(esg_themes_data, dict) else None
            )
            if _mat_industry and _mat_theme:
                adjusted_score = apply_materiality_adjustment(
                    relevance.total, _mat_industry, _mat_theme,
                )
                article.relevance_score = adjusted_score
                # Preserve original score in breakdown for transparency
                if isinstance(article.relevance_breakdown, dict):
                    article.relevance_breakdown["pre_materiality_score"] = relevance.total
                    article.relevance_breakdown["materiality_industry"] = _mat_industry
                    article.relevance_breakdown["materiality_theme"] = _mat_theme
                    article.relevance_breakdown["materiality_adjusted_score"] = adjusted_score
                logger.info(
                    "materiality_adjustment",
                    article_id=article_id,
                    industry=_mat_industry,
                    theme=_mat_theme,
                    original=relevance.total,
                    adjusted=adjusted_score,
                )
        except Exception as e:
            logger.warning("materiality_adjustment_skipped", error=str(e))

        if relevance.tier == "REJECTED":
            article.priority_level = "REJECTED"
            article.priority_score = 0.0
            logger.info("article_rejected_low_relevance", score=relevance.total, title=article.title[:50])
            await db.flush()
            return []

    # Step 2b: Build geographic signal (Module 1 — jurisdictional mapping)
    from backend.ontology.jurisdictional_mapper import build_geographic_signal
    entity_locations = [e.text for e in resolved_entities if e.entity_type == "location"]
    geographic_signal = build_geographic_signal(entity_locations) if entity_locations else None

    # Step 3: Get all companies for this tenant
    companies_result = await db.execute(
        select(Company).where(Company.tenant_id == tenant_id)
    )
    companies = companies_result.scalars().all()

    # Step 4: Find causal chains from each entity to each company
    all_impacts: list[dict] = []
    locations = [e.text for e in resolved_entities if e.entity_type == "location"]

    # Phase 4: Climate event → facility risk zone mapping
    CLIMATE_EVENT_TO_RISK_ZONE: dict[str, set[str]] = {
        "water_scarcity": {"water_stress", "drought_prone"},
        "drought": {"water_stress", "drought_prone"},
        "monsoon_failure": {"water_stress", "drought_prone"},
        "heatwave": {"heat_stress"},
        "heat_stress": {"heat_stress"},
        "flood": {"coastal_flood", "flood_prone"},
        "cyclone": {"coastal_flood", "cyclone_prone"},
        "typhoon": {"coastal_flood"},
        "coastal_flood": {"coastal_flood"},
        "sea_level_rise": {"coastal_flood"},
    }
    article_risk_zones: set[str] = set()
    for event in (extraction.climate_events or []):
        article_risk_zones.update(CLIMATE_EVENT_TO_RISK_ZONE.get(event, set()))

    # Guard: Only inject facility-climate chains if article is genuinely about
    # physical/climate risk — NOT for commodity price, market volatility, or
    # financial articles that incidentally mention weather/water keywords.
    _primary_theme = (esg_themes_data.get("primary_theme", "") if isinstance(esg_themes_data, dict) else "").lower()
    _title_lower = (article.title or "").lower()
    _article_is_physical_climate = (
        _primary_theme in (
            "climate change", "physical risk", "water stress", "natural disaster",
            "extreme weather", "biodiversity", "land use", "water management",
            "pollution", "waste management", "environmental compliance",
        )
        or any(kw in _title_lower for kw in (
            "flood", "drought", "cyclone", "wildfire", "water scarcity",
            "sea level", "heatwave", "monsoon failure", "climate disaster",
            "water crisis", "deforestation", "pollution spill",
        ))
    )
    if not _article_is_physical_climate:
        article_risk_zones = set()  # Skip facility matching for non-climate articles
        logger.debug(
            "climate_facility_guard_skipped",
            title=article.title[:60],
            primary_theme=_primary_theme,
            climate_events=extraction.climate_events,
        )

    # QA Fix: Batch-load all facilities once to avoid N+1 query per company
    all_facilities_by_company: dict[str, list] = {}
    if article_risk_zones:
        from backend.models.company import Facility
        all_fac_result = await db.execute(
            select(Facility).where(
                Facility.tenant_id == tenant_id,
                Facility.climate_risk_zone.isnot(None),
            )
        )
        for f in all_fac_result.scalars().all():
            all_facilities_by_company.setdefault(f.company_id, []).append(f)

    # QA Fix: Run geo_matches once, not per company
    geo_matches = await find_geographic_matches(locations, tenant_id, db)

    for company in companies:
        # Geographic proximity check (using pre-fetched geo_matches)
        geo_boost = 0.0
        for match in geo_matches:
            if match.company_id == company.id:
                geo_boost = 0.2 if match.match_type == "exact_city" else 0.1

        best_chains: list[CausalPath] = []

        # Phase 4: Climate risk intelligence — check if article's climate events
        # match any facility's climate_risk_zone (using batch-loaded facilities)
        if article_risk_zones:
            company_facilities = all_facilities_by_company.get(company.id, [])
            at_risk_facilities = [
                f for f in company_facilities
                if f.climate_risk_zone in article_risk_zones
            ]
            for facility in at_risk_facilities:
                best_chains.append(CausalPath(
                    nodes=[article.title[:50], facility.city or facility.name, company.name],
                    hops=0,
                    relationship_type="climateRiskExposure",
                    impact_score=calculate_impact(0),
                    explanation=(
                        f"Climate risk: {', '.join(extraction.climate_events or [])} "
                        f"directly affects facility '{facility.name}' in {facility.city} "
                        f"(zone: {facility.climate_risk_zone})"
                    ),
                    frameworks=extraction_frameworks,
                ))
                geo_boost = max(geo_boost, 0.3)  # Climate risk boost higher than proximity

        # Find causal chains from resolved entities
        for entity in resolved_entities:
            if entity.resolved_uri:
                chains = await find_causal_chains(
                    entity.text, company.id, tenant_id,
                )
                best_chains.extend(chains)

        # Fallback: direct company name matching when Jena is unavailable
        # GAP-7 fix: tighter matching to prevent cross-entity article leakage
        if not best_chains:
            company_name_lower = company.name.lower()
            # Also check known competitors for competitive intelligence labeling
            raw_competitors = getattr(company, 'competitors', None) or []
            competitor_names = {
                (c["name"].lower() if isinstance(c, dict) else str(c).lower())
                for c in raw_competitors
            }
            for entity in resolved_entities:
                entity_lower = entity.text.lower()

                # GAP-7: Require minimum entity confidence before any fallback matching
                if entity.confidence < 0.7:
                    continue

                # Check if entity IS the tracked company (exact/substring match)
                is_tracked_company = (
                    entity_lower in company_name_lower
                    or company_name_lower in entity_lower
                )
                # Check if entity is a named competitor
                is_named_competitor = any(
                    entity_lower in comp or comp in entity_lower
                    for comp in competitor_names
                )
                # Loose word match — only for the tracked company, not random sector peers
                is_word_match = (
                    not is_tracked_company
                    and not is_named_competitor
                    and any(w in company_name_lower for w in entity_lower.split() if len(w) > 3)
                )

                if is_tracked_company:
                    rel_type = "directOperational" if entity.entity_type == "company" else "industrySpillover"
                    best_chains.append(CausalPath(
                        nodes=[article.title[:50], entity.text, company.name],
                        hops=0 if entity.entity_type == "company" else 1,
                        relationship_type=rel_type,
                        impact_score=calculate_impact(0 if entity.entity_type == "company" else 1),
                        explanation=f"Direct match: '{entity.text}' linked to {company.name}",
                        frameworks=extraction_frameworks,
                    ))
                    break
                elif is_named_competitor:
                    # GAP-7: Competitor article — label as competitiveIntelligence, cap impact at 0.3
                    raw_impact = calculate_impact(1) * 0.7
                    capped_impact = min(raw_impact, 0.3)
                    best_chains.append(CausalPath(
                        nodes=[article.title[:50], entity.text, company.name],
                        hops=1,
                        relationship_type="competitiveIntelligence",
                        impact_score=capped_impact,
                        explanation=f"Competitive Intelligence: '{entity.text}' is a named competitor of {company.name}",
                        frameworks=extraction_frameworks,
                    ))
                    break
                elif is_word_match and entity.confidence >= 0.8:
                    # GAP-7: Loose word match requires even higher confidence (0.8) and capped impact
                    raw_impact = calculate_impact(1) * 0.5
                    capped_impact = min(raw_impact, 0.3)  # Cap industrySpillover at 0.3
                    best_chains.append(CausalPath(
                        nodes=[article.title[:50], entity.text, company.name],
                        hops=1,
                        relationship_type="industrySpillover",
                        impact_score=capped_impact,
                        explanation=f"Sector News: '{entity.text}' loosely linked to {company.name}",
                        frameworks=extraction_frameworks,
                    ))
                    break

        if not best_chains and geo_matches:
            # Geographic proximity alone creates a 0-hop connection
            for match in geo_matches:
                if match.company_id == company.id:
                    best_chains.append(CausalPath(
                        nodes=[article.title[:50], match.facility_name, company.name],
                        hops=0,
                        relationship_type="geographicProximity",
                        impact_score=calculate_impact(0),
                        explanation=f"Geographic proximity: news location '{match.matched_location}' "
                                    f"matches facility '{match.facility_name}'",
                    ))

        if not best_chains:
            continue

        # Store the best chain
        best = max(best_chains, key=lambda c: c.impact_score + geo_boost)
        final_score = min(best.impact_score + geo_boost, 1.0)

        # Stage 3.3: Merge frameworks from extraction + causal chain
        chain_frameworks = best.frameworks or []
        all_frameworks = list(set(extraction_frameworks + chain_frameworks))

        # Persist causal chain
        chain = CausalChain(
            tenant_id=tenant_id,
            article_id=article_id,
            company_id=company.id,
            chain_path=[{"nodes": best.nodes, "edges": best.edges}],
            hops=best.hops,
            relationship_type=best.relationship_type,
            impact_score=final_score,
            explanation=best.explanation,
            esg_pillar=extraction.esg_pillar,
            framework_alignment=all_frameworks,
            confidence=min(e.confidence for e in resolved_entities) if resolved_entities else 0.5,
        )
        db.add(chain)

        # GAP-7: Derive content_label from relationship_type for feed labeling
        _rel_type = best.relationship_type
        if _rel_type in ("directOperational", "geographicProximity", "climateRiskExposure"):
            content_label = "direct_impact"
        elif _rel_type == "competitiveIntelligence":
            content_label = "competitive_intelligence"
        elif _rel_type == "industrySpillover":
            content_label = "sector_news"
        else:
            content_label = "direct_impact"  # Default for graph-resolved chains

        # Persist article score (Stage 3.3: populate frameworks field)
        score = ArticleScore(
            tenant_id=tenant_id,
            article_id=article_id,
            company_id=company.id,
            relevance_score=final_score * 100,
            impact_score=final_score * 100,
            causal_hops=best.hops,
            frameworks=all_frameworks,
            scoring_metadata={
                "content_label": content_label,
                "geo_boost": geo_boost,
                "extraction_sentiment": extraction.sentiment,
                "esg_topics": extraction.esg_topics,
                "financial_signal": extraction.financial_signal,
                "frameworks_from_extraction": extraction_frameworks,
                "frameworks_from_chain": chain_frameworks,
            },
        )
        db.add(score)

        all_impacts.append({
            "company_id": company.id,
            "company_name": company.name,
            "impact_score": final_score,
            "hops": best.hops,
            "relationship_type": best.relationship_type,
            "explanation": best.explanation,
            "esg_pillar": extraction.esg_pillar,
            "geo_match": bool(geo_boost > 0),
        })

    await db.flush()

    # Phase 1E: Calculate composite priority score
    from backend.services.priority_engine import calculate_priority_score as calc_priority
    from backend.services.regulatory_calendar import (
        detect_deadline_language,
        find_nearest_deadline,
    )

    # Enhancement 2: Regulatory deadline detection
    days_to_deadline: int | None = None
    try:
        nearest_deadline = find_nearest_deadline(extraction_frameworks)
        if nearest_deadline:
            days_to_deadline = nearest_deadline["days_until"]
            # Store the date in the DateTime column, full details in scoring_metadata
            from datetime import date as _date
            deadline_date_str = nearest_deadline.get("deadline_date")
            if deadline_date_str:
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    if isinstance(deadline_date_str, str):
                        article.regulatory_deadline = _dt.fromisoformat(deadline_date_str).replace(tzinfo=_tz.utc)
                    elif isinstance(deadline_date_str, _date):
                        article.regulatory_deadline = _dt.combine(deadline_date_str, _dt.min.time(), tzinfo=_tz.utc)
                except Exception:
                    pass
            logger.info(
                "regulatory_deadline_detected",
                article_id=article_id,
                framework=nearest_deadline["framework"],
                days_until=days_to_deadline,
            )
    except Exception as e:
        logger.warning("regulatory_calendar_skipped", article_id=article_id, error=str(e))

    best_impact = max((i["impact_score"] for i in all_impacts), default=0.0)
    priority_score, priority_level = calc_priority(
        sentiment_score=extraction.sentiment_score,
        urgency=extraction.urgency,
        impact_score=best_impact * 100,  # convert 0-1 to 0-100
        has_financial_signal=bool(extraction.financial_signal_detail),
        reversibility=extraction.reversibility,
        framework_count=len(extraction_frameworks),
        days_to_deadline=days_to_deadline,
    )
    article.priority_score = priority_score
    article.priority_level = priority_level

    # Phase B1: Generate AI executive insight for significant articles
    if priority_score >= 40 and all_impacts:
        from backend.services.insight_generator import generate_executive_insight
        best_impact = max(all_impacts, key=lambda x: x["impact_score"])
        try:
            insight = await generate_executive_insight(
                article_title=article.title,
                article_summary=article.summary or "",
                company_name=best_impact["company_name"],
                relationship_type=best_impact["relationship_type"],
                causal_hops=best_impact["hops"],
                frameworks=extraction_frameworks,
                sentiment_score=extraction.sentiment_score,
                urgency=extraction.urgency,
                content_type=extraction.content_type,
                article_content=article.content,
                esg_pillar=extraction.esg_pillar,
            )
            if insight:
                article.executive_insight = insight
        except Exception as e:
            logger.warning("insight_generation_skipped", error=str(e))

    # ── v2.0 Risk Spotlight for ALL non-rejected articles (Tier 2: FEED) ──
    # Cheap gpt-4o-mini call — top 3 risks only. Overwritten by full matrix for HOME articles.
    if all_impacts and not article.risk_matrix:
        best_impact_obj = max(all_impacts, key=lambda x: x["impact_score"])
        try:
            from backend.services.risk_spotlight import run_risk_spotlight
            spotlight = await run_risk_spotlight(
                article_title=article.title,
                article_content=article.content or article.summary,
                company_name=best_impact_obj["company_name"],
            )
            if spotlight:
                article.risk_matrix = spotlight
        except Exception as e:
            logger.warning("v2_risk_spotlight_skipped", error=str(e))

    # Phase 2+3: Deep Insight + REREACT for high-relevance articles (≥7)
    if relevance and relevance.qualified_for_home and all_impacts:
        best_impact = max(all_impacts, key=lambda x: x["impact_score"])
        # Get company competitors for context
        company_obj = next((c for c in companies if c.id == best_impact["company_id"]), None)
        competitor_names = [c.get("name", "") for c in (company_obj.competitors or [])] if company_obj and company_obj.competitors else []

        # ── v2.0 Module 4: Framework RAG ──
        framework_matches_data = None
        try:
            from backend.services.framework_rag import retrieve_applicable_frameworks
            # Build theme name list from esg_themes_data dict
            theme_names = None
            if esg_themes_data:
                theme_names = [esg_themes_data["primary_theme"]]
                for st in esg_themes_data.get("secondary_themes", []):
                    if isinstance(st, dict) and st.get("theme"):
                        theme_names.append(st["theme"])
            # Pass company region for region-based framework boosting
            _company_region = company_obj.headquarter_region if company_obj else None
            fm = await retrieve_applicable_frameworks(
                esg_themes=theme_names,
                article_content=article.content or article.summary or article.title,
                article_title=article.title,
                company_region=_company_region,
                company_market_cap=company_obj.market_cap if company_obj else None,
            )
            # Enrich with is_mandatory field from mandatory_frameworks module
            _company_market_cap = company_obj.market_cap if company_obj else None
            try:
                from backend.services.mandatory_frameworks import is_framework_mandatory
                for match in fm:
                    match.is_mandatory = is_framework_mandatory(
                        match.framework_id, _company_region, _company_market_cap,
                        country=getattr(company_obj, 'headquarter_country', None) if company_obj else None,
                    )
            except Exception:
                pass  # graceful degradation if mandatory_frameworks unavailable
            framework_matches_data = [m.to_dict() if hasattr(m, "to_dict") else m for m in fm]
            article.framework_matches = framework_matches_data
        except Exception as e:
            logger.warning("v2_framework_rag_skipped", error=str(e))

        # ── v2.0 Module 6: Risk Taxonomy (10 categories × P×E) ──
        risk_matrix_data = None
        try:
            from backend.services.risk_taxonomy import assess_risk_matrix
            _risk_company = next((c for c in companies if c.id == best_impact["company_id"]), None)
            risk_result = await assess_risk_matrix(
                article_title=article.title,
                article_content=article.content or article.summary,
                company_name=best_impact["company_name"],
                nlp_extraction=nlp_data,
                esg_themes=esg_themes_data,
                frameworks=extraction_frameworks,
                industry=_risk_company.industry if _risk_company else None,
                sasb_category=_risk_company.sasb_category if _risk_company else None,
            )
            risk_matrix_data = risk_result.to_dict()
            risk_matrix_data["mode"] = "full"  # distinguishes from spotlight
            article.risk_matrix = risk_matrix_data
        except Exception as e:
            logger.warning("v2_risk_taxonomy_skipped", error=str(e))

        # ── v2.0 Module 2: Store geographic signal ──
        if geographic_signal:
            article.geographic_signal = geographic_signal

        try:
            from backend.services.deep_insight_generator import generate_deep_insight
            deep = await generate_deep_insight(
                article_title=article.title,
                article_content=article.content,
                article_summary=article.summary,
                company_name=best_impact["company_name"],
                frameworks=extraction_frameworks,
                sentiment_score=extraction.sentiment_score,
                urgency=extraction.urgency,
                content_type=extraction.content_type,
                esg_pillar=extraction.esg_pillar,
                competitors=competitor_names,
                # v2.0 module data
                nlp_extraction=nlp_data,
                esg_themes=esg_themes_data,
                framework_matches=framework_matches_data,
                risk_matrix=risk_matrix_data,
                geographic_signal=article.geographic_signal,
                # v2.1 financial calibration context
                market_cap=company_obj.market_cap_value if company_obj else None,
                revenue=company_obj.revenue_last_fy if company_obj else None,
            )
            if deep:
                article.deep_insight = deep
                logger.info("deep_insight_v2_stored", article=article.title[:40])
        except Exception as e:
            logger.warning("deep_insight_skipped", error=str(e))

        # REREACT 3-agent recommendations — runs INLINE (no Celery)
        if article.deep_insight:
            try:
                from backend.services.rereact_engine import rereact_recommendations
                rereact = await rereact_recommendations(
                    article_title=article.title,
                    article_content=article.content,
                    deep_insight=article.deep_insight,
                    company_name=best_impact["company_name"],
                    frameworks=extraction_frameworks,
                    content_type=extraction.content_type,
                    competitors=list(competitor_names),
                    market_cap=getattr(company_obj, 'market_cap', None) if company_obj else None,
                    listing_exchange=getattr(company_obj, 'listing_exchange', None) if company_obj else None,
                    headquarter_country=getattr(company_obj, 'headquarter_country', None) if company_obj else None,
                )
                if rereact:
                    article.rereact_recommendations = rereact
                    logger.info("rereact_generated_inline", article=article.title[:40],
                        recs=len(rereact.get("validated_recommendations", [])))
            except Exception as e:
                logger.warning("rereact_inline_failed", error=str(e))

        # ── Priority override: reconcile rule-based priority with LLM materiality ──
        # The rule-based priority_engine scores urgency/sentiment/frameworks but
        # ignores financial materiality. The LLM's decision_summary provides the
        # true materiality assessment. When they conflict (e.g. CRITICAL priority +
        # MONITOR action), the materiality gate must win — otherwise executives see
        # "CRITICAL" next to "No immediate board action needed", which is misleading.
        if article.deep_insight and isinstance(article.deep_insight, dict):
            ds = article.deep_insight.get("decision_summary") or {}
            materiality = (ds.get("materiality") or "").upper()
            action = (ds.get("action") or "").upper()
            if materiality and action:
                if materiality in ("CRITICAL",) and action in ("ACT",):
                    article.priority_level = "CRITICAL"
                elif materiality in ("HIGH",) or action in ("ACT",):
                    article.priority_level = "HIGH"
                elif action == "MONITOR":
                    # MONITOR signal = at most MEDIUM priority, regardless of urgency score
                    article.priority_level = "MEDIUM"
                elif action == "IGNORE" or materiality in ("NON-MATERIAL",):
                    article.priority_level = "LOW"
                logger.info(
                    "priority_level_reconciled",
                    original=priority_level,
                    reconciled=article.priority_level,
                    materiality=materiality,
                    action=action,
                )

    # Populate financial_exposure on the highest-impact ArticleScore
    if extraction.financial_signal_detail and extraction.financial_signal_detail.get("amount"):
        for impact in all_impacts:
            if impact["impact_score"] == best_impact:
                # Update the ArticleScore we just created
                from sqlalchemy import update
                await db.execute(
                    update(ArticleScore).where(
                        ArticleScore.article_id == article_id,
                        ArticleScore.company_id == impact["company_id"],
                        ArticleScore.tenant_id == tenant_id,
                    ).values(financial_exposure=extraction.financial_signal_detail["amount"])
                )
                break

    await db.flush()

    # ── GAP 8: Event Deduplication ──
    # After scoring, check if this article is part of a duplicate event cluster.
    # Consolidates risk scores (highest wins) and links related coverage.
    if all_impacts:
        try:
            from backend.services.event_deduplication import apply_deduplication
            for impact in all_impacts:
                dedup_count = await apply_deduplication(
                    tenant_id=tenant_id,
                    company_id=impact["company_id"],
                    db=db,
                )
                if dedup_count:
                    logger.info(
                        "event_deduplication_applied",
                        article_id=article_id,
                        company_id=impact["company_id"],
                        articles_updated=dedup_count,
                    )
            await db.flush()
        except Exception as e:
            logger.warning("event_deduplication_skipped", article_id=article_id, error=str(e))

    logger.info(
        "article_impact_analyzed",
        article_id=article_id,
        tenant_id=tenant_id,
        impacts=len(all_impacts),
        priority_score=priority_score,
        priority_level=priority_level,
    )

    # Cache the article's analysis fields for 24h so repeated reads skip the DB
    try:
        from backend.core.redis import CACHE_TTL_ANALYSIS, cache_set
        analysis_snapshot = {
            "deep_insight": article.deep_insight,
            "rereact_recommendations": article.rereact_recommendations,
            "risk_matrix": article.risk_matrix,
            "framework_matches": article.framework_matches,
            "priority_score": article.priority_score,
            "priority_level": article.priority_level,
        }
        await cache_set(tenant_id, "article_analysis", article_id, analysis_snapshot, ttl=CACHE_TTL_ANALYSIS)
    except Exception:
        pass  # Cache failure must never break the pipeline

    # Notify connected frontend clients via Socket.IO
    try:
        from backend.core.socketio import emit_to_tenant
        await emit_to_tenant(
            tenant_id,
            "article_analysis_complete",
            {
                "article_id": article_id,
                "priority_score": article.priority_score,
                "priority_level": article.priority_level,
            },
        )
    except Exception:
        pass

    # Clear in-progress status key so polling returns "idle" if cache missed
    try:
        from backend.core.redis import cache_delete
        await cache_delete(tenant_id, "article_analysis_status", article_id)
    except Exception:
        pass

    return all_impacts


async def get_causal_chain_explorer(
    entity_text: str,
    tenant_id: str,
) -> list[dict]:
    """Causal chain explorer: "Show me all paths from [news event] to [my company]"

    Per MASTER_BUILD_PLAN Phase 3.6: Ontology API
    """
    return await find_all_impacts_for_entity(entity_text, tenant_id)


async def get_ontology_stats(tenant_id: str) -> dict:
    """Get stats about the tenant's ontology graph."""
    triple_count = await jena_client.count_triples(tenant_id)
    graph_exists = await jena_client.graph_exists(tenant_id)

    # Count specific entity types
    stats = {
        "graph_exists": graph_exists,
        "total_triples": triple_count,
    }

    if graph_exists:
        graph_uri = jena_client._tenant_graph(tenant_id)
        for entity_type in ["Company", "Facility", "Supplier", "Commodity", "MaterialIssue", "GeographicRegion"]:
            sparql = f"""
            PREFIX snowkap: <{SNOWKAP_NS}>
            SELECT (COUNT(DISTINCT ?e) AS ?count) WHERE {{
                GRAPH <{graph_uri}> {{
                    ?e a snowkap:{entity_type} .
                }}
            }}
            """
            try:
                result = await jena_client.query(sparql)
                bindings = result.get("results", {}).get("bindings", [])
                if bindings:
                    stats[entity_type.lower() + "_count"] = int(bindings[0]["count"]["value"])
            except Exception:
                stats[entity_type.lower() + "_count"] = 0

    return stats


def calculate_impact_score(hops: int, base_score: float = 1.0) -> float:
    """Calculate impact score with decay per hop."""
    return calculate_impact(hops, base_score)
