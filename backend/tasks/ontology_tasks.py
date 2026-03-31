"""Celery tasks for background ontology processing.

Per MASTER_BUILD_PLAN Phase 3:
- Auto-provision tenant ontology graph
- Background article impact analysis
- Bulk entity extraction and resolution

Stage 8.1: Use asgiref.sync.async_to_sync instead of creating new event loops.
Stage 8.2: Add soft_time_limit + hard time_limit to all tasks.
QA: Added retry policies and hard_time_limit.
"""

import structlog
from asgiref.sync import async_to_sync

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    name="ontology.provision_tenant",
    soft_time_limit=600,
    time_limit=720,
    max_retries=1,
    default_retry_delay=60,
)
def provision_tenant_ontology_task(
    tenant_id: str,
    tenant_name: str,
    industry: str | None,
    sasb_category: str | None,
    domain: str,
) -> dict:
    """Background task: provision tenant's Jena knowledge graph."""
    async def _provision():
        from backend.ontology.tenant_provisioner import provision_tenant_graph
        return await provision_tenant_graph(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            industry=industry,
            sasb_category=sasb_category,
            domain=domain,
        )

    try:
        result = async_to_sync(_provision)()
        logger.info("tenant_ontology_provisioned", tenant_id=tenant_id, success=result)
        return {"tenant_id": tenant_id, "success": result}
    except Exception as e:
        logger.error("tenant_ontology_provision_failed", tenant_id=tenant_id, error=str(e))
        return {"tenant_id": tenant_id, "success": False, "error": str(e)}


@celery_app.task(
    name="ontology.analyze_article",
    soft_time_limit=600,
    time_limit=720,
    max_retries=2,
    default_retry_delay=30,
)
def analyze_article_impact_task(article_id: str, tenant_id: str) -> dict:
    """Background task: full article impact analysis pipeline.

    1. Extract entities via Claude NER
    2. Resolve against Jena graph
    3. Find causal chains to tenant's companies
    4. Score and store results
    """
    async def _analyze():
        from backend.core.database import create_worker_session_factory
        from backend.services.ontology_service import analyze_article_impact

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            impacts = await analyze_article_impact(article_id, tenant_id, db)
            await db.commit()
            return impacts

    try:
        impacts = async_to_sync(_analyze)()
        logger.info(
            "article_impact_analyzed_bg",
            article_id=article_id,
            tenant_id=tenant_id,
            impacts=len(impacts),
        )
        # After impact analysis, check if any company scores meet prediction thresholds
        from backend.tasks.prediction_tasks import auto_trigger_check_task
        auto_trigger_check_task.delay(tenant_id, article_id)

        return {"article_id": article_id, "impacts_count": len(impacts)}
    except Exception as e:
        logger.error("article_impact_analysis_failed", article_id=article_id, error=str(e))
        return {"article_id": article_id, "error": str(e)}


@celery_app.task(
    name="ontology.provision_full",
    soft_time_limit=600,
    time_limit=720,
    max_retries=1,
    default_retry_delay=60,
)
def provision_full_ontology_task(tenant_id: str) -> dict:
    """Background task: full ontology provisioning (companies + facilities + supply chain)."""
    async def _provision_full():
        from backend.core.database import create_worker_session_factory
        from backend.ontology.tenant_provisioner import provision_full_tenant_ontology

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            stats = await provision_full_tenant_ontology(tenant_id, db)
            await db.commit()
            return stats

    try:
        stats = async_to_sync(_provision_full)()
        logger.info("full_ontology_provisioned_bg", **stats)
        return stats
    except Exception as e:
        logger.error("full_ontology_provision_failed", tenant_id=tenant_id, error=str(e))
        return {"tenant_id": tenant_id, "error": str(e)}


@celery_app.task(
    name="ontology.generate_supply_chain",
    soft_time_limit=600,
    time_limit=720,
    max_retries=1,
    default_retry_delay=60,
)
def generate_supply_chain_task(
    company_id: str,
    company_name: str,
    industry: str,
    tenant_id: str,
) -> dict:
    """Background task: auto-generate supply chain for a company via Claude."""
    async def _generate():
        from backend.ontology.supply_chain_graph import generate_industry_supply_chain
        nodes = await generate_industry_supply_chain(company_name, industry, tenant_id)
        return [{"name": n.name, "type": n.node_type, "tier": n.tier} for n in nodes]

    try:
        nodes = async_to_sync(_generate)()
        logger.info(
            "supply_chain_generated_bg",
            company_id=company_id,
            nodes=len(nodes),
        )
        return {"company_id": company_id, "nodes": nodes}
    except Exception as e:
        logger.error("supply_chain_generation_failed", company_id=company_id, error=str(e))
        return {"company_id": company_id, "error": str(e)}
