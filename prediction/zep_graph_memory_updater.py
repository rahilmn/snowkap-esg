"""Zep graph memory updater — updates causal graph after each simulation.

Per MASTER_BUILD_PLAN Phase 4:
- Simulation outcomes → Jena triples
- Store prediction-derived knowledge back into the ontology
"""

import structlog
import httpx

from prediction.config import mirofish_settings
from prediction.simulation_runner import SimulationResult

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"


async def update_jena_with_prediction(
    tenant_id: str,
    company_id: str,
    article_id: str,
    simulation_result: SimulationResult,
    report: dict,
) -> bool:
    """Store prediction results as triples in the tenant's Jena named graph.

    This feeds simulation outcomes back into the knowledge graph, enriching
    future causal chain traversals and predictions.
    """
    graph_uri = f"urn:snowkap:tenant:{tenant_id}"
    prediction_uri = f"{SNOWKAP_NS}prediction_{simulation_result.simulation_id}"
    company_uri = f"{SNOWKAP_NS}company_{company_id}"
    article_uri = f"{SNOWKAP_NS}article_{article_id}"

    triples = [
        # Prediction node
        f'<{prediction_uri}> a <{SNOWKAP_NS}PredictionReport> .',
        f'<{prediction_uri}> rdfs:label "{_escape(report.get("title", "Prediction"))}" .',
        f'<{prediction_uri}> <{SNOWKAP_NS}confidenceScore> "{simulation_result.consensus_confidence}"^^xsd:float .',
        f'<{prediction_uri}> <{SNOWKAP_NS}riskLevel> "{simulation_result.risk_level}" .',
        f'<{prediction_uri}> <{SNOWKAP_NS}convergenceScore> "{simulation_result.convergence_score}"^^xsd:float .',
        # Links
        f'<{prediction_uri}> <{SNOWKAP_NS}predictsImpactOn> <{company_uri}> .',
        f'<{prediction_uri}> <{SNOWKAP_NS}triggeredByArticle> <{article_uri}> .',
    ]

    # Add ESG impact triples
    for impact in report.get("esg_impacts", []):
        pillar = impact.get("pillar", "E")
        topic = impact.get("topic", "unknown").replace(" ", "_")
        severity = impact.get("severity", "medium")
        impact_uri = f"{SNOWKAP_NS}impact_{simulation_result.simulation_id}_{topic}"
        triples.append(f'<{impact_uri}> a <{SNOWKAP_NS}MaterialIssue> .')
        triples.append(f'<{impact_uri}> rdfs:label "{topic.replace("_", " ")}" .')
        triples.append(f'<{impact_uri}> <{SNOWKAP_NS}esgPillar> "{pillar}" .')
        triples.append(f'<{prediction_uri}> <{SNOWKAP_NS}identifiesIssue> <{impact_uri}> .')

    # Add recommendation triples
    for i, rec in enumerate(report.get("recommendations", [])[:5]):
        rec_uri = f"{SNOWKAP_NS}rec_{simulation_result.simulation_id}_{i}"
        triples.append(f'<{rec_uri}> a <{SNOWKAP_NS}Recommendation> .')
        triples.append(f'<{rec_uri}> rdfs:label "{_escape(rec.get("action", ""))[:200]}" .')
        triples.append(f'<{rec_uri}> <{SNOWKAP_NS}priority> "{rec.get("priority", "medium")}" .')
        triples.append(f'<{prediction_uri}> <{SNOWKAP_NS}hasRecommendation> <{rec_uri}> .')

    # Build SPARQL INSERT
    triple_block = "\n  ".join(triples)
    sparql = f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    PREFIX snowkap: <{SNOWKAP_NS}>

    INSERT DATA {{
        GRAPH <{graph_uri}> {{
            {triple_block}
        }}
    }}
    """

    update_url = f"{mirofish_settings.JENA_FUSEKI_URL}/{mirofish_settings.JENA_DATASET}/update"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                update_url,
                data={"update": sparql},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            logger.info(
                "jena_prediction_stored",
                tenant_id=tenant_id,
                simulation_id=simulation_result.simulation_id,
                triples=len(triples),
            )
            return True
    except httpx.HTTPError as e:
        logger.error("jena_prediction_store_failed", error=str(e))
        return False


def _escape(text: str) -> str:
    """Escape special chars for SPARQL string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
