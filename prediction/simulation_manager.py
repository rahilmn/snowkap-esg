"""Simulation manager — orchestrates the full prediction pipeline.

Per MASTER_BUILD_PLAN Phase 4.3:
- Celery task: trigger_prediction(news_event, company, causal_chain)
- Seed data: news article + causal chain from Jena + company profile
- MiroFish graph_builder receives Jena subgraph as seed
- Simulation runs 10-40 rounds (configurable per impact severity)
- report_agent generates structured prediction report
- Results stored in prediction_reports table + Jena triples
"""

import structlog

from prediction.config import mirofish_settings
from prediction.graph_builder import build_seed_document, extract_company_subgraph
from prediction.oasis_profile_generator import generate_simulation_agents
from prediction.ontology_generator import classify_scenario_archetype, get_archetype_context
from prediction.report_agent import generate_prediction_report
from prediction.simulation_config_generator import SimulationConfig, generate_config
from prediction.simulation_runner import SimulationResult, run_simulation

logger = structlog.get_logger()


async def run_full_prediction(
    tenant_id: str,
    company_id: str,
    company_data: dict,
    article_data: dict,
    causal_chain_data: dict | None = None,
    tenant_config: dict | None = None,
) -> dict:
    """Run the full MiroFish prediction pipeline.

    Steps:
    1. Extract company subgraph from Jena
    2. Generate simulation config
    3. Classify scenario archetype
    4. Generate agent profiles
    5. Build seed document
    6. Run simulation
    7. Generate prediction report
    8. Return results for storage
    """
    simulation_id = f"sim_{tenant_id}_{company_id}_{article_data.get('id', 'unknown')}"

    logger.info(
        "prediction_pipeline_start",
        simulation_id=simulation_id,
        tenant_id=tenant_id,
        company_id=company_id,
    )

    # Step 1: Extract company knowledge from Jena
    subgraph = await extract_company_subgraph(company_id, tenant_id)

    # Step 2: Generate simulation config
    config = generate_config(
        company_data=company_data,
        article_data=article_data,
        causal_chain_data=causal_chain_data,
        tenant_config=tenant_config,
    )

    # Step 3: Classify scenario
    archetype = classify_scenario_archetype(
        article_title=article_data.get("title", ""),
        esg_pillar=article_data.get("esg_pillar"),
        relationship_type=causal_chain_data.get("relationship_type") if causal_chain_data else None,
        entities=article_data.get("entities", []),
    )
    archetype_context = get_archetype_context(archetype)
    config.material_issues = subgraph.get("material_issues", [])

    # Step 4: Generate agents
    agents = generate_simulation_agents(
        company_name=company_data.get("name", ""),
        industry=company_data.get("industry", ""),
        scenario_context=archetype,
        agent_count=config.agent_count,
    )

    # Step 5: Build seed document
    seed_document = build_seed_document(
        company_subgraph=subgraph,
        article_data=article_data,
        causal_chain_data=causal_chain_data,
    )

    # Step 6: Run simulation
    sim_result = await run_simulation(
        config=config,
        agents=agents,
        seed_document=seed_document,
        simulation_id=simulation_id,
    )

    # Step 7: Generate report
    report = await generate_prediction_report(
        simulation_result=sim_result,
        config=config,
        archetype=archetype,
        archetype_context=archetype_context,
    )

    logger.info(
        "prediction_pipeline_complete",
        simulation_id=simulation_id,
        rounds=sim_result.rounds_completed,
        convergence=sim_result.convergence_score,
        confidence=sim_result.consensus_confidence,
    )

    # Return full results for storage by the caller
    return {
        "simulation_id": simulation_id,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "article_id": article_data.get("id"),
        "causal_chain_id": causal_chain_data.get("id") if causal_chain_data else None,
        "report": report,
        "simulation": {
            "rounds_completed": sim_result.rounds_completed,
            "total_agents": sim_result.total_agents,
            "convergence_score": sim_result.convergence_score,
            "duration_seconds": sim_result.duration_seconds,
        },
        "consensus": {
            "analysis": sim_result.consensus_analysis,
            "recommendation": sim_result.consensus_recommendation,
            "financial_impact": sim_result.consensus_financial_impact,
            "confidence": sim_result.consensus_confidence,
            "time_horizon": sim_result.consensus_time_horizon,
            "risk_level": sim_result.risk_level,
            "opportunities": sim_result.opportunities,
        },
        "scenario_archetype": archetype,
    }
