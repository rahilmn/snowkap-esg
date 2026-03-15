"""MiroFish Prediction Engine — FastAPI Application (port 5001).

Per CLAUDE.md: Separate microservice, AGPL-3.0 process isolation.
Per MASTER_BUILD_PLAN: POST /predict/simulate triggered by Celery task.
"""

import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from prediction.config import mirofish_settings
from prediction.simulation_manager import run_full_prediction

logger = structlog.get_logger()

app = FastAPI(
    title="MiroFish ESG Prediction Engine",
    description="Multi-agent ESG prediction simulation",
    version="1.0.0",
)


class SimulateRequest(BaseModel):
    tenant_id: str
    company_id: str
    company: dict  # {name, industry, domain, tenant_id}
    article: dict  # {id, title, summary, entities, esg_pillar}
    causal_chain: dict | None = None  # {id, chain_path, hops, impact_score, relationship_type}
    tenant_config: dict | None = None  # mirofish_config from tenant settings
    config: dict | None = None  # Override: {agent_count, rounds}


class SimulateResponse(BaseModel):
    simulation_id: str
    status: str
    report: dict | None = None
    simulation: dict | None = None
    consensus: dict | None = None
    scenario_archetype: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        service="mirofish-prediction",
        version="1.0.0",
    )


@app.post("/predict/simulate", response_model=SimulateResponse)
async def simulate(req: SimulateRequest) -> SimulateResponse:
    """Run a full MiroFish prediction simulation.

    Per MASTER_BUILD_PLAN: triggered by Celery task on high-impact news.
    """
    logger.info(
        "simulation_request",
        tenant_id=req.tenant_id,
        company_id=req.company_id,
        article_title=req.article.get("title", "")[:60],
    )

    try:
        result = await run_full_prediction(
            tenant_id=req.tenant_id,
            company_id=req.company_id,
            company_data=req.company,
            article_data=req.article,
            causal_chain_data=req.causal_chain,
            tenant_config=req.tenant_config,
        )

        return SimulateResponse(
            simulation_id=result["simulation_id"],
            status="completed",
            report=result.get("report"),
            simulation=result.get("simulation"),
            consensus=result.get("consensus"),
            scenario_archetype=result.get("scenario_archetype"),
        )

    except Exception as e:
        logger.error("simulation_failed", error=str(e))
        return SimulateResponse(
            simulation_id="",
            status="error",
            error=str(e),
        )


@app.get("/predict/archetypes")
async def list_archetypes() -> dict:
    """List available scenario archetypes for simulation."""
    from prediction.ontology_generator import SCENARIO_ARCHETYPES
    return {"archetypes": SCENARIO_ARCHETYPES}


@app.get("/predict/agents")
async def list_agent_templates() -> dict:
    """List available agent profile templates."""
    from prediction.oasis_profile_generator import AGENT_TEMPLATES
    return {
        "agents": {
            key: {"name": a.name, "role": a.role, "decision_style": a.decision_style}
            for key, a in AGENT_TEMPLATES.items()
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "prediction.app:app",
        host=mirofish_settings.HOST,
        port=mirofish_settings.PORT,
        reload=mirofish_settings.DEBUG,
    )
