"""Agent tools — SPARQL, DB, predict, causal chain, ontology rules.

Per MASTER_BUILD_PLAN Phase 5:
Each agent gets: sparql_tool, domain_db_tool, causal_chain_tool, prediction_tool, ontology_rule_tool

These are callable from the LangGraph state machine by any specialist agent.
"""

from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


async def sparql_tool(tenant_id: str, query: str, db: AsyncSession | None = None) -> dict:
    """Execute a SPARQL query scoped to the tenant's ontology graph.

    Returns structured results from the Apache Jena knowledge graph.
    """
    from backend.services.ontology_service import execute_sparql
    return await execute_sparql(tenant_id, query)


async def domain_db_tool(tenant_id: str, table: str, filters: dict, db: AsyncSession) -> dict:
    """Query the domain database with tenant scoping.

    Supports: companies, articles, article_scores, causal_chains, prediction_reports.
    All queries are automatically scoped to tenant_id.
    """
    ALLOWED_TABLES = {
        "companies": "backend.models.company:Company",
        "articles": "backend.models.news:Article",
        "article_scores": "backend.models.news:ArticleScore",
        "causal_chains": "backend.models.news:CausalChain",
        "prediction_reports": "backend.models.prediction:PredictionReport",
        "analyses": "backend.models.analysis:Analysis",
    }

    if table not in ALLOWED_TABLES:
        return {"error": f"Table '{table}' not allowed. Available: {list(ALLOWED_TABLES.keys())}"}

    module_path, class_name = ALLOWED_TABLES[table].split(":")
    import importlib
    module = importlib.import_module(module_path)
    model_class = getattr(module, class_name)

    query = select(model_class).where(model_class.tenant_id == tenant_id)

    # Apply filters
    for field, value in filters.items():
        if hasattr(model_class, field):
            col = getattr(model_class, field)
            if isinstance(value, list):
                query = query.where(col.in_(value))
            else:
                query = query.where(col == value)

    query = query.limit(50)
    result = await db.execute(query)
    rows = result.scalars().all()

    # Serialize to dicts
    records = []
    for row in rows:
        record = {}
        for col in row.__table__.columns:
            val = getattr(row, col.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, (dict, list)):
                pass  # JSON-safe already
            else:
                val = str(val) if val is not None else None
            record[col.name] = val
        records.append(record)

    logger.info("domain_db_query", table=table, tenant_id=tenant_id, results=len(records))
    return {"table": table, "count": len(records), "records": records}


async def causal_chain_tool(
    tenant_id: str,
    entity_name: str,
    company_id: str | None = None,
    max_hops: int = 3,
) -> dict:
    """Find causal chains from a news entity to tenant companies.

    Uses the causal engine's BFS traversal over the Jena knowledge graph.
    """
    from backend.ontology.causal_engine import find_all_impacts_for_entity

    try:
        impacts = await find_all_impacts_for_entity(
            tenant_id=tenant_id,
            entity_name=entity_name,
            max_hops=max_hops,
        )

        chains = []
        for impact in impacts:
            chain_data = {
                "company_uri": impact.get("company_uri", ""),
                "company_name": impact.get("company_name", ""),
                "paths": [],
            }
            for path in impact.get("paths", []):
                chain_data["paths"].append({
                    "nodes": path.nodes,
                    "edges": path.edges,
                    "hops": path.hops,
                    "impact_score": path.impact_score,
                    "relationship_type": path.relationship_type,
                    "explanation": path.explanation,
                })
            chains.append(chain_data)

        logger.info("causal_chain_query", entity=entity_name, chains=len(chains))
        return {"entity": entity_name, "chains": chains, "total": len(chains)}
    except Exception as e:
        logger.error("causal_chain_tool_error", error=str(e))
        return {"entity": entity_name, "chains": [], "error": str(e)}


async def prediction_tool(
    tenant_id: str,
    article_id: str | None = None,
    company_id: str | None = None,
    db: AsyncSession | None = None,
) -> dict:
    """Retrieve MiroFish prediction results for a company or article.

    Returns existing prediction reports — does NOT trigger new simulations.
    Use the /predictions/trigger endpoint for that.
    """
    if not db:
        return {"error": "Database session required"}

    from backend.models.prediction import PredictionReport

    query = select(PredictionReport).where(PredictionReport.tenant_id == tenant_id)
    if article_id:
        query = query.where(PredictionReport.article_id == article_id)
    if company_id:
        query = query.where(PredictionReport.company_id == company_id)

    query = query.order_by(PredictionReport.created_at.desc()).limit(10)
    result = await db.execute(query)
    reports = result.scalars().all()

    return {
        "predictions": [
            {
                "id": r.id,
                "title": r.title,
                "summary": r.summary,
                "confidence_score": r.confidence_score,
                "financial_impact": r.financial_impact,
                "time_horizon": r.time_horizon,
                "status": r.status,
                "agent_consensus": r.agent_consensus,
            }
            for r in reports
        ],
        "total": len(reports),
    }


async def ontology_rule_tool(
    tenant_id: str,
    action: str = "list",
    rule_data: dict | None = None,
) -> dict:
    """Manage ontology rules: list, describe, or suggest rules.

    Actions: 'list' (show current rules), 'describe' (explain a rule type),
    'suggest' (recommend rules based on context).
    Does NOT create or modify rules — that requires explicit API calls.
    """
    if action == "list":
        from backend.ontology.jena_client import JenaClient
        from backend.core.config import settings

        client = JenaClient(settings.JENA_FUSEKI_URL, settings.JENA_DATASET)
        graph_uri = f"urn:snowkap:tenant:{tenant_id}"

        results = await client.query(
            f"""
            SELECT ?rule ?type ?label WHERE {{
                GRAPH <{graph_uri}> {{
                    ?rule a <urn:snowkap:ontology#BusinessRule> ;
                          <urn:snowkap:ontology#ruleType> ?type .
                    OPTIONAL {{ ?rule rdfs:label ?label }}
                }}
            }} LIMIT 50
            """
        )
        rules = results.get("results", {}).get("bindings", [])
        return {
            "action": "list",
            "rules": [
                {
                    "uri": r.get("rule", {}).get("value", ""),
                    "type": r.get("type", {}).get("value", ""),
                    "label": r.get("label", {}).get("value", ""),
                }
                for r in rules
            ],
        }

    elif action == "describe":
        from backend.ontology.rule_compiler import RULE_TYPES
        return {
            "action": "describe",
            "rule_types": {
                name: {
                    "description": rt.get("description", ""),
                    "required_fields": rt.get("required_fields", []),
                }
                for name, rt in RULE_TYPES.items()
            } if hasattr(RULE_TYPES, "items") else {"info": "6 rule types: threshold, classification, relationship, material_issue, framework_mapping, geographic_risk"},
        }

    elif action == "suggest":
        return {
            "action": "suggest",
            "message": "To create rules, use the /api/ontology/rules endpoint with appropriate permissions.",
        }

    return {"error": f"Unknown action: {action}"}


# Tool registry for LangGraph
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "sparql": {
        "fn": sparql_tool,
        "description": "Query the ESG knowledge graph using SPARQL. Returns entities, relationships, and ontology data.",
        "requires_db": False,
    },
    "database": {
        "fn": domain_db_tool,
        "description": "Query the domain database (companies, articles, predictions, analyses) with tenant scoping.",
        "requires_db": True,
    },
    "causal_chain": {
        "fn": causal_chain_tool,
        "description": "Find causal impact chains from a news entity to tenant companies via knowledge graph traversal.",
        "requires_db": False,
    },
    "prediction": {
        "fn": prediction_tool,
        "description": "Retrieve MiroFish prediction reports for companies or articles.",
        "requires_db": True,
    },
    "ontology_rules": {
        "fn": ontology_rule_tool,
        "description": "List and describe ontology business rules for the tenant.",
        "requires_db": False,
    },
}
