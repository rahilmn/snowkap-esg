"""Report agent — generates structured prediction reports for SNOWKAP UI.

Per MASTER_BUILD_PLAN Phase 4:
- Report JSON → React dashboard cards
- Structured prediction with confidence scoring
- Actionable recommendations
"""

import json

import structlog
from anthropic import AsyncAnthropic

from prediction.config import mirofish_settings
from prediction.simulation_config_generator import SimulationConfig
from prediction.simulation_runner import SimulationResult

logger = structlog.get_logger()


async def generate_prediction_report(
    simulation_result: SimulationResult,
    config: SimulationConfig,
    archetype: str,
    archetype_context: dict,
) -> dict:
    """Generate a structured prediction report from simulation results.

    Output format matches the PredictionReport model in backend/models/prediction.py.
    """
    if not mirofish_settings.ANTHROPIC_API_KEY:
        return _fallback_report(simulation_result, config, archetype)

    client = AsyncAnthropic(api_key=mirofish_settings.ANTHROPIC_API_KEY)

    prompt = f"""Generate a structured ESG prediction report based on this simulation.

## Simulation Context
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

## Key Questions Addressed
{chr(10).join(archetype_context.get('key_questions', []))}

Generate a report as JSON:
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

Financial values in INR. Return JSON only."""

    try:
        response = await client.messages.create(
            model=mirofish_settings.LLM_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            report = json.loads(text[start:end])
        else:
            report = json.loads(text)

        logger.info("prediction_report_generated", title=report.get("title", "")[:50])
        return report

    except Exception as e:
        logger.error("report_generation_failed", error=str(e))
        return _fallback_report(simulation_result, config, archetype)


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
