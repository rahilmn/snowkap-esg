"""Report agent — generates structured prediction reports for SNOWKAP UI.

Per MASTER_BUILD_PLAN Phase 4:
- Report JSON → React dashboard cards
- Structured prediction with confidence scoring
- Actionable recommendations

Stage 4.6: ReACT-lite two-stage generation:
  Stage 1 (Plan): LLM produces a report outline with section headings + key points
  Stage 2 (Generate): LLM fills each section using the plan as a scaffold
This yields more coherent, structured reports than single-shot generation.
"""

import json

import structlog
from openai import AsyncOpenAI

from prediction.config import mirofish_settings
from prediction.simulation_config_generator import SimulationConfig
from prediction.simulation_runner import SimulationResult, _llm_call_with_retry

logger = structlog.get_logger()


async def generate_prediction_report(
    simulation_result: SimulationResult,
    config: SimulationConfig,
    archetype: str,
    archetype_context: dict,
) -> dict:
    """Generate a structured prediction report via two-stage ReACT-lite.

    Stage 1: Plan — produce report outline
    Stage 2: Generate — fill sections from outline
    """
    if not mirofish_settings.OPENAI_API_KEY:
        return _fallback_report(simulation_result, config, archetype)

    client = AsyncOpenAI(api_key=mirofish_settings.OPENAI_API_KEY)

    # Build shared context block
    context = _build_report_context(simulation_result, config, archetype, archetype_context)

    # Stage 1: Planning pass
    plan = await _plan_report(client, context)
    if not plan:
        return _fallback_report(simulation_result, config, archetype)

    # Stage 2: Generation pass using the plan
    report = await _generate_from_plan(client, context, plan)
    if not report:
        return _fallback_report(simulation_result, config, archetype)

    logger.info("prediction_report_generated", title=report.get("title", "")[:50], stages=2)
    return report


def _build_report_context(
    simulation_result: SimulationResult,
    config: SimulationConfig,
    archetype: str,
    archetype_context: dict,
) -> str:
    """Build the shared context string used by both planning and generation stages."""
    return f"""## Simulation Context
Company: {config.company_name} ({config.company_industry})
News Event: {config.article_title}
Scenario Type: {archetype}
ESG Pillar: {config.esg_pillar or 'Mixed'}

## Simulation Results
Agents: {simulation_result.total_agents}
Rounds: {simulation_result.rounds_completed}
Convergence: {simulation_result.convergence_score:.2f}

## Consensus
Analysis: {simulation_result.consensus_analysis}
Recommendation: {simulation_result.consensus_recommendation}
Financial Impact: {simulation_result.consensus_financial_impact}
Confidence: {simulation_result.consensus_confidence:.2f}
Risk Level: {simulation_result.risk_level}
Opportunities: {', '.join(simulation_result.opportunities) if simulation_result.opportunities else 'None identified'}

## Key Questions
{chr(10).join(archetype_context.get('key_questions', ['No specific questions']))}"""


async def _plan_report(client: AsyncOpenAI, context: str) -> str | None:
    """Stage 1: Planning pass — produce a structured report outline.

    Returns the plan text, or None on failure.
    """
    prompt = f"""You are an ESG prediction report planner. Given simulation results,
create a detailed report outline.

{context}

Create a report plan as a numbered outline with these sections:
1. TITLE — one clear, actionable prediction title (max 100 chars)
2. EXECUTIVE SUMMARY — 2-3 key points to cover
3. IMPACT ANALYSIS — what aspects of impact to detail (financial, operational, reputational)
4. TIMELINE — key milestones and time horizons to address
5. FINANCIAL PROJECTIONS — what ranges and scenarios to include
6. RECOMMENDATIONS — top 3-5 action items with priorities
7. ESG FRAMEWORK IMPACTS — which pillars/frameworks are affected
8. SCENARIO VARIABLES — what could change the prediction

For each section, list 2-3 bullet points of what to include.
Be specific to this company and scenario. Write the outline, not prose."""

    try:
        return await _llm_call_with_retry(
            client,
            model=mirofish_settings.LLM_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("report_plan_failed", error=str(e))
        return None


async def _generate_from_plan(
    client: AsyncOpenAI,
    context: str,
    plan: str,
) -> dict | None:
    """Stage 2: Generation pass — fill sections from the outline.

    Uses the plan as a scaffold to produce a coherent, structured report.
    """
    prompt = f"""You are generating a final ESG prediction report. Use BOTH the simulation
context and the report plan below to produce a complete, structured report.

{context}

## Report Plan (follow this structure)
{plan}

Generate the report as JSON:
{{
    "title": "Clear, actionable prediction title (max 100 chars)",
    "summary": "2-3 sentence executive summary",
    "prediction_text": "Detailed prediction (3-5 paragraphs covering impact, timeline, financial, and mitigation)",
    "confidence_score": 0.0-1.0,
    "financial_impact_low": 0,
    "financial_impact_high": 0,
    "time_horizon": "short_term|medium_term|long_term",
    "risk_level": "low|medium|high|critical",
    "recommendations": [
        {{"action": "...", "priority": "high|medium|low", "estimated_cost": "...", "timeline": "..."}},
    ],
    "esg_impacts": [
        {{"pillar": "E|S|G", "topic": "...", "severity": "high|medium|low", "framework": "..."}}
    ],
    "scenario_variables": [
        {{"variable": "...", "current_value": "...", "threshold": "...", "impact_if_breached": "..."}}
    ]
}}

Financial values in INR. Return JSON only, no markdown."""

    try:
        text = await _llm_call_with_retry(
            client,
            model=mirofish_settings.LLM_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return json.loads(text)
    except Exception as e:
        logger.error("report_generation_failed", error=str(e))
        return None


def _fallback_report(
    sim_result: SimulationResult,
    config: SimulationConfig,
    archetype: str,
) -> dict:
    """Generate a basic report when LLM is unavailable."""
    return {
        "title": f"ESG Impact Prediction: {config.article_title[:80]}",
        "summary": sim_result.consensus_analysis or "Prediction simulation completed. Review agent consensus for details.",
        "prediction_text": (
            f"A {archetype.replace('_', ' ')} scenario was simulated with "
            f"{sim_result.total_agents} agents over {sim_result.rounds_completed} rounds.\n\n"
            f"Consensus analysis: {sim_result.consensus_analysis}\n\n"
            f"Recommendation: {sim_result.consensus_recommendation}\n\n"
            f"Financial impact estimate: {sim_result.consensus_financial_impact}"
        ),
        "confidence_score": sim_result.consensus_confidence,
        "financial_impact_low": None,
        "financial_impact_high": None,
        "time_horizon": sim_result.consensus_time_horizon,
        "risk_level": sim_result.risk_level,
        "recommendations": [],
        "esg_impacts": [],
        "scenario_variables": [],
    }


def format_report_for_ui(report: dict) -> dict:
    """Format a prediction report for React dashboard display.

    Adds display-friendly fields and card metadata.
    """
    risk_colors = {
        "low": "#22c55e",
        "medium": "#f59e0b",
        "high": "#ef4444",
        "critical": "#dc2626",
    }

    confidence_label = "High" if report.get("confidence_score", 0) > 0.7 else \
                       "Medium" if report.get("confidence_score", 0) > 0.4 else "Low"

    return {
        **report,
        "display": {
            "risk_color": risk_colors.get(report.get("risk_level", "medium"), "#f59e0b"),
            "confidence_label": confidence_label,
            "confidence_percentage": f"{report.get('confidence_score', 0) * 100:.0f}%",
            "time_horizon_label": report.get("time_horizon", "medium_term").replace("_", " ").title(),
        },
    }
