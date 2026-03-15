"""Prediction service — MiroFish bridge + result storage.

Per CLAUDE.md:
- MiroFish runs as separate Docker service on port 5001
- AGPL-3.0 license — runs as separate microservice (process isolation)
- Triggered only on high-impact news (score >70, financial exposure >₹10L)
"""

import structlog
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.company import Company
from backend.models.news import Article, ArticleScore, CausalChain
from backend.models.prediction import PredictionReport, SimulationRun
from backend.models.tenant import TenantConfig

logger = structlog.get_logger()

# Trigger conditions per MASTER_BUILD_PLAN Part 2
TRIGGER_CONDITIONS = {
    "impact_score_threshold": 70,
    "causal_chain_hops": 2,
    "financial_exposure_min": 1_000_000,
    "framework_criticality": "high",
}


def should_trigger_prediction(
    impact_score: float,
    causal_hops: int,
    financial_exposure: float | None,
    user_requested: bool = False,
) -> bool:
    """Check if a news event meets MiroFish trigger conditions.

    Per CLAUDE.md Rule #3: NEVER run MiroFish on every article — only high-impact.
    """
    if user_requested:
        return True
    if impact_score < TRIGGER_CONDITIONS["impact_score_threshold"]:
        return False
    if causal_hops < TRIGGER_CONDITIONS["causal_chain_hops"]:
        return False
    if financial_exposure and financial_exposure < TRIGGER_CONDITIONS["financial_exposure_min"]:
        return False
    return True


async def trigger_simulation(
    article_data: dict,
    company_data: dict,
    causal_chain: dict | None = None,
    tenant_config: dict | None = None,
) -> dict:
    """Send simulation request to MiroFish service.

    Per CLAUDE.md: MiroFish runs on port 5001 as separate process.
    """
    payload = {
        "tenant_id": company_data.get("tenant_id", ""),
        "company_id": company_data.get("id", ""),
        "article": article_data,
        "company": company_data,
        "causal_chain": causal_chain,
        "tenant_config": tenant_config,
    }

    try:
        async with httpx.AsyncClient(timeout=mirofish_timeout()) as client:
            response = await client.post(
                f"{settings.MIROFISH_URL}/predict/simulate",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("mirofish_simulation_triggered", status=result.get("status"))
            return result
    except httpx.HTTPError as e:
        logger.error("mirofish_trigger_failed", error=str(e))
        return {"status": "error", "error": str(e)}


async def run_prediction_pipeline(
    tenant_id: str,
    article_id: str,
    company_id: str,
    causal_chain_id: str | None,
    db: AsyncSession,
    user_requested: bool = False,
) -> dict:
    """Full prediction pipeline: validate → prepare data → call MiroFish → store results.

    Called from Celery task or directly from API endpoint.
    """
    # Load article
    article_result = await db.execute(
        select(Article).where(Article.id == article_id, Article.tenant_id == tenant_id)
    )
    article = article_result.scalar_one_or_none()
    if not article:
        return {"status": "error", "error": "Article not found"}

    # Load company
    company_result = await db.execute(
        select(Company).where(Company.id == company_id, Company.tenant_id == tenant_id)
    )
    company = company_result.scalar_one_or_none()
    if not company:
        return {"status": "error", "error": "Company not found"}

    # Load causal chain if provided
    chain_data = None
    if causal_chain_id:
        chain_result = await db.execute(
            select(CausalChain).where(CausalChain.id == causal_chain_id, CausalChain.tenant_id == tenant_id)
        )
        chain = chain_result.scalar_one_or_none()
        if chain:
            chain_data = {
                "id": chain.id,
                "chain_path": chain.chain_path,
                "hops": chain.hops,
                "impact_score": chain.impact_score,
                "relationship_type": chain.relationship_type,
                "explanation": chain.explanation,
                "framework_alignment": chain.framework_alignment,
            }

    # Check trigger conditions (unless user requested)
    if not user_requested:
        # Get article score for this company
        score_result = await db.execute(
            select(ArticleScore).where(
                ArticleScore.article_id == article_id,
                ArticleScore.company_id == company_id,
                ArticleScore.tenant_id == tenant_id,
            )
        )
        score = score_result.scalar_one_or_none()
        if score and not should_trigger_prediction(
            impact_score=score.impact_score,
            causal_hops=score.causal_hops,
            financial_exposure=score.financial_exposure,
        ):
            return {"status": "skipped", "reason": "Below trigger thresholds"}

    # Load tenant config for MiroFish settings
    config_result = await db.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
    )
    tenant_config = config_result.scalar_one_or_none()

    # Prepare data for MiroFish
    article_data = {
        "id": article.id,
        "title": article.title,
        "summary": article.summary or "",
        "content": (article.content or "")[:2000],
        "entities": article.entities or [],
        "esg_pillar": article.esg_pillar,
        "sentiment": article.sentiment,
    }

    company_data = {
        "id": company.id,
        "name": company.name,
        "industry": company.industry,
        "domain": company.domain,
        "tenant_id": tenant_id,
    }

    tenant_config_data = None
    if tenant_config:
        tenant_config_data = {
            "mirofish_config": tenant_config.mirofish_config,
        }

    # Call MiroFish
    mirofish_result = await trigger_simulation(
        article_data=article_data,
        company_data=company_data,
        causal_chain=chain_data,
        tenant_config=tenant_config_data,
    )

    if mirofish_result.get("status") == "error":
        return mirofish_result

    # Store prediction report
    report_data = mirofish_result.get("report", {})
    consensus = mirofish_result.get("consensus", {})
    sim_data = mirofish_result.get("simulation", {})

    prediction_report = PredictionReport(
        tenant_id=tenant_id,
        company_id=company_id,
        article_id=article_id,
        causal_chain_id=causal_chain_id,
        title=report_data.get("title", f"Prediction: {article.title[:100]}"),
        summary=report_data.get("summary", consensus.get("analysis", "")),
        prediction_text=report_data.get("prediction_text", ""),
        confidence_score=consensus.get("confidence", 0.5),
        financial_impact=report_data.get("financial_impact_high"),
        time_horizon=consensus.get("time_horizon", "medium"),
        scenario_variables=report_data.get("scenario_variables", []),
        agent_consensus={
            "analysis": consensus.get("analysis"),
            "recommendation": consensus.get("recommendation"),
            "risk_level": consensus.get("risk_level"),
            "opportunities": consensus.get("opportunities", []),
        },
        status="completed",
    )
    db.add(prediction_report)
    await db.flush()

    # Store simulation run
    sim_run = SimulationRun(
        tenant_id=tenant_id,
        prediction_report_id=prediction_report.id,
        agent_count=sim_data.get("total_agents", 20),
        rounds=sim_data.get("rounds_completed", 10),
        seed_data={"article": article_data, "company": company_data},
        config=tenant_config_data,
        results=mirofish_result,
        convergence_score=sim_data.get("convergence_score", 0),
        duration_seconds=sim_data.get("duration_seconds", 0),
        status="completed",
    )
    db.add(sim_run)
    await db.flush()

    logger.info(
        "prediction_stored",
        report_id=prediction_report.id,
        tenant_id=tenant_id,
        confidence=prediction_report.confidence_score,
    )

    return {
        "status": "completed",
        "prediction_report_id": prediction_report.id,
        "simulation_id": mirofish_result.get("simulation_id"),
        "confidence": prediction_report.confidence_score,
        "risk_level": consensus.get("risk_level", "medium"),
    }


def mirofish_timeout() -> float:
    """Get MiroFish HTTP timeout based on environment."""
    return 300.0  # 5 minutes for simulations
