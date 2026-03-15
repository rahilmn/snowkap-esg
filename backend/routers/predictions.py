"""Predictions router — MiroFish trigger + results + scenario explorer.

Per CLAUDE.md: Triggered only on high-impact news (score >70, financial exposure >₹10L).
Per CLAUDE.md Rule #3: NEVER run MiroFish on every article.
Per MASTER_BUILD_PLAN Phase 4.4:
- "What If" cards on news articles (high-impact only)
- Scenario explorer: adjust variables, re-simulate
- Prediction confidence scoring
- Historical prediction accuracy tracking
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.prediction import PredictionReport, SimulationRun

logger = structlog.get_logger()
router = APIRouter()


# --- Request / Response schemas ---

class PredictionResponse(BaseModel):
    id: str
    company_id: str
    article_id: str | None
    title: str
    summary: str | None
    prediction_text: str | None
    confidence_score: float
    financial_impact: float | None
    time_horizon: str | None
    scenario_variables: dict | None
    agent_consensus: dict | None
    status: str


class PredictionDetailResponse(PredictionResponse):
    causal_chain_id: str | None
    simulation_runs: list[dict] = []


class TriggerPredictionRequest(BaseModel):
    article_id: str
    company_id: str
    causal_chain_id: str | None = None


class TriggerResponse(BaseModel):
    status: str
    message: str
    prediction_report_id: str | None = None


class PredictionStatsResponse(BaseModel):
    total_predictions: int
    avg_confidence: float
    high_risk_count: int
    completed_count: int
    pending_count: int


# --- Endpoints ---

@router.get("/", response_model=list[PredictionResponse])
async def list_predictions(
    company_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[PredictionResponse]:
    """List prediction reports — tenant-scoped."""
    query = select(PredictionReport).where(PredictionReport.tenant_id == ctx.tenant_id)
    if company_id:
        query = query.where(PredictionReport.company_id == company_id)
    query = query.order_by(PredictionReport.created_at.desc()).limit(limit).offset(offset)

    result = await ctx.db.execute(query)
    reports = result.scalars().all()
    return [
        PredictionResponse(
            id=r.id, company_id=r.company_id, article_id=r.article_id,
            title=r.title, summary=r.summary, prediction_text=r.prediction_text,
            confidence_score=r.confidence_score, financial_impact=r.financial_impact,
            time_horizon=r.time_horizon, scenario_variables=r.scenario_variables,
            agent_consensus=r.agent_consensus, status=r.status,
        )
        for r in reports
    ]


@router.get("/stats", response_model=PredictionStatsResponse)
async def prediction_stats(
    ctx: TenantContext = Depends(get_tenant_context),
) -> PredictionStatsResponse:
    """Get prediction statistics for this tenant."""
    total_result = await ctx.db.execute(
        select(func.count(PredictionReport.id)).where(
            PredictionReport.tenant_id == ctx.tenant_id,
        )
    )
    total = total_result.scalar() or 0

    avg_result = await ctx.db.execute(
        select(func.avg(PredictionReport.confidence_score)).where(
            PredictionReport.tenant_id == ctx.tenant_id,
        )
    )
    avg_confidence = avg_result.scalar() or 0.0

    completed_result = await ctx.db.execute(
        select(func.count(PredictionReport.id)).where(
            PredictionReport.tenant_id == ctx.tenant_id,
            PredictionReport.status == "completed",
        )
    )
    completed = completed_result.scalar() or 0

    # Count high risk (confidence > 0.7 in agent_consensus with risk_level = high/critical)
    # Simplified: count those with confidence > 0.7
    high_risk_result = await ctx.db.execute(
        select(func.count(PredictionReport.id)).where(
            PredictionReport.tenant_id == ctx.tenant_id,
            PredictionReport.confidence_score > 0.7,
        )
    )
    high_risk = high_risk_result.scalar() or 0

    return PredictionStatsResponse(
        total_predictions=total,
        avg_confidence=round(float(avg_confidence), 3),
        high_risk_count=high_risk,
        completed_count=completed,
        pending_count=total - completed,
    )


@router.get("/{prediction_id}", response_model=PredictionDetailResponse)
async def get_prediction(
    prediction_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> PredictionDetailResponse:
    """Get a single prediction report with simulation details."""
    result = await ctx.db.execute(
        select(PredictionReport).where(
            PredictionReport.id == prediction_id,
            PredictionReport.tenant_id == ctx.tenant_id,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prediction not found")

    # Get simulation runs
    sim_result = await ctx.db.execute(
        select(SimulationRun).where(SimulationRun.prediction_report_id == prediction_id)
    )
    sim_runs = sim_result.scalars().all()

    return PredictionDetailResponse(
        id=report.id, company_id=report.company_id, article_id=report.article_id,
        title=report.title, summary=report.summary, prediction_text=report.prediction_text,
        confidence_score=report.confidence_score, financial_impact=report.financial_impact,
        time_horizon=report.time_horizon, scenario_variables=report.scenario_variables,
        agent_consensus=report.agent_consensus, status=report.status,
        causal_chain_id=report.causal_chain_id,
        simulation_runs=[
            {
                "id": s.id,
                "agent_count": s.agent_count,
                "rounds": s.rounds,
                "convergence_score": s.convergence_score,
                "duration_seconds": s.duration_seconds,
                "status": s.status,
            }
            for s in sim_runs
        ],
    )


@router.post("/trigger", response_model=TriggerResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_prediction(
    req: TriggerPredictionRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> TriggerResponse:
    """Trigger a MiroFish prediction simulation.

    Per CLAUDE.md Rule #3: NEVER run MiroFish on every article.
    This is the manual "predict impact" button or permission-gated auto-trigger.
    """
    if "trigger_predictions" not in ctx.user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No permission to trigger predictions",
        )

    # Dispatch Celery task
    from backend.tasks.prediction_tasks import trigger_simulation_task
    task = trigger_simulation_task.delay(
        tenant_id=ctx.tenant_id,
        article_id=req.article_id,
        company_id=req.company_id,
        causal_chain_id=req.causal_chain_id,
        user_requested=True,
    )

    logger.info(
        "prediction_triggered",
        article_id=req.article_id,
        company_id=req.company_id,
        tenant_id=ctx.tenant_id,
        task_id=task.id,
    )

    return TriggerResponse(
        status="queued",
        message="Prediction simulation queued for processing",
    )
