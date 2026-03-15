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


def get_tenant_graph_uri(tenant_id: str) -> str:
    """Get the named graph URI for a tenant per CLAUDE.md convention."""
    return f"urn:snowkap:tenant:{tenant_id}"


async def execute_sparql(tenant_id: str, query: str) -> dict:
    """Execute a SPARQL query against the tenant's named graph in Jena Fuseki.

    Per CLAUDE.md Rule #5: NEVER expose Jena SPARQL directly — always proxy.
    """
    graph_uri = get_tenant_graph_uri(tenant_id)
    logger.info("sparql_execute", tenant_id=tenant_id, graph=graph_uri, query_len=len(query))

    # Validate query doesn't escape tenant graph (basic security check)
    query_upper = query.upper()
    if "DROP" in query_upper or "DELETE" in query_upper or "CLEAR" in query_upper:
        logger.warning("sparql_dangerous_query_blocked", tenant_id=tenant_id)
        return {"error": "Destructive queries are not allowed through the proxy"}

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

    # Step 1+2: Extract and classify
    extraction = await extract_and_classify(article.title, article.content or article.summary or "")

    # Resolve entities against Jena
    resolved_entities = await resolve_entities_against_graph(extraction.entities, tenant_id)

    # Update article with extraction results
    article.entities = [
        {"text": e.text, "type": e.entity_type, "uri": e.resolved_uri}
        for e in resolved_entities
    ]
    article.sentiment = extraction.sentiment
    article.esg_pillar = extraction.esg_pillar

    # Step 3: Get all companies for this tenant
    companies_result = await db.execute(
        select(Company).where(Company.tenant_id == tenant_id)
    )
    companies = companies_result.scalars().all()

    # Step 4: Find causal chains from each entity to each company
    all_impacts: list[dict] = []
    locations = [e.text for e in resolved_entities if e.entity_type == "location"]

    for company in companies:
        # Geographic proximity check
        geo_matches = await find_geographic_matches(locations, tenant_id, db)
        geo_boost = 0.0
        for match in geo_matches:
            if match.company_id == company.id:
                geo_boost = 0.2 if match.match_type == "exact_city" else 0.1

        # Find causal chains from resolved entities
        best_chains: list[CausalPath] = []
        for entity in resolved_entities:
            if entity.resolved_uri:
                chains = await find_causal_chains(
                    entity.text, company.id, tenant_id,
                )
                best_chains.extend(chains)

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
            framework_alignment=best.frameworks or [],
            confidence=min(e.confidence for e in resolved_entities) if resolved_entities else 0.5,
        )
        db.add(chain)

        # Persist article score
        score = ArticleScore(
            tenant_id=tenant_id,
            article_id=article_id,
            company_id=company.id,
            relevance_score=final_score * 100,
            impact_score=final_score * 100,
            causal_hops=best.hops,
            scoring_metadata={
                "geo_boost": geo_boost,
                "extraction_sentiment": extraction.sentiment,
                "esg_topics": extraction.esg_topics,
                "financial_signal": extraction.financial_signal,
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

    logger.info(
        "article_impact_analyzed",
        article_id=article_id,
        tenant_id=tenant_id,
        impacts=len(all_impacts),
    )
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
