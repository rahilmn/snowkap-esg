"""Simulation runner — runs agent-based ESG simulations using Claude.

Per MASTER_BUILD_PLAN Phase 4:
- OASIS-inspired framework for agent simulation
- Parallel agent deliberation, results aggregation
- 10-40 rounds per simulation

Stage 4.3: Inter-agent debate — top 3 divergent responses fed back
Stage 4.4: Structured action logging per agent per round
Stage 4.5: LLM retry with exponential backoff (2s, 5s)
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

import structlog
from openai import AsyncOpenAI

from prediction.config import mirofish_settings
from prediction.oasis_profile_generator import AgentProfile
from prediction.simulation_config_generator import SimulationConfig

logger = structlog.get_logger()

# Stage 4.5: Retry config
LLM_RETRY_DELAYS = [2.0, 5.0]
LLM_RETRYABLE_ERRORS = ("overloaded", "rate_limit", "timeout", "529", "529", "500", "502", "503")


@dataclass
class AgentResponse:
    """A single agent's response to a simulation round."""
    agent_id: str
    agent_role: str
    analysis: str = ""
    recommendation: str = ""
    financial_estimate: str = ""
    confidence: float = 0.5
    time_horizon: str = "medium"
    round_number: int = 0


@dataclass
class ActionLogEntry:
    """Stage 4.4: Structured log entry for agent actions."""
    round_number: int
    agent_id: str
    agent_role: str
    action: str  # "analyze", "debate", "revise", "error"
    confidence: float = 0.0
    duration_ms: float = 0.0
    retries: int = 0
    error: str | None = None


@dataclass
class RoundResult:
    """Result of a single simulation round."""
    round_number: int
    responses: list[AgentResponse] = field(default_factory=list)
    consensus_score: float = 0.0
    dominant_sentiment: str = "neutral"
    debate_triggered: bool = False


@dataclass
class SimulationResult:
    """Complete result of a simulation run."""
    simulation_id: str = ""
    rounds_completed: int = 0
    total_agents: int = 0
    duration_seconds: float = 0.0
    convergence_score: float = 0.0
    round_results: list[RoundResult] = field(default_factory=list)
    action_log: list[ActionLogEntry] = field(default_factory=list)
    consensus_analysis: str = ""
    consensus_recommendation: str = ""
    consensus_financial_impact: str = ""
    consensus_confidence: float = 0.0
    consensus_time_horizon: str = "medium"
    scenario_archetype: str = ""
    risk_level: str = "medium"
    opportunities: list[str] = field(default_factory=list)


async def run_simulation(
    config: SimulationConfig,
    agents: list[AgentProfile],
    seed_document: str,
    simulation_id: str,
) -> SimulationResult:
    """Run a full multi-round agent simulation.

    Algorithm:
    1. Initialize all agents with seed document + scenario prompt
    2. For each round:
       a. Each agent analyzes the scenario
       b. Collect all responses
       c. Stage 4.3: If divergence detected, trigger inter-agent debate
       d. Check for convergence
       e. If converged or max rounds reached, stop
    3. Synthesize consensus from all agent responses
    """
    start_time = time.time()
    scenario_prompt = config.to_scenario_prompt()

    result = SimulationResult(
        simulation_id=simulation_id,
        total_agents=len(agents),
    )

    if not mirofish_settings.OPENAI_API_KEY:
        logger.warning("openai_key_missing_mirofish")
        result.consensus_analysis = "Simulation skipped — no API key configured"
        result.convergence_score = 0.0
        return result

    client = AsyncOpenAI(api_key=mirofish_settings.OPENAI_API_KEY)
    all_responses: list[AgentResponse] = []
    prev_round_summary = ""

    for round_num in range(1, config.rounds + 1):
        round_result = RoundResult(round_number=round_num)

        # Run each agent for this round
        for agent in agents:
            response, log_entry = await _run_agent_round_with_logging(
                client=client,
                agent=agent,
                seed_document=seed_document,
                scenario_prompt=scenario_prompt,
                round_number=round_num,
                prev_round_summary=prev_round_summary,
            )
            response.round_number = round_num
            round_result.responses.append(response)
            all_responses.append(response)
            result.action_log.append(log_entry)

        # Stage 4.3: Inter-agent debate on divergent views
        if round_num >= 2 and len(round_result.responses) >= 3:
            divergent = _find_divergent_responses(round_result.responses)
            if divergent:
                round_result.debate_triggered = True
                debate_summary = await _run_debate_round(
                    client, divergent, seed_document, scenario_prompt, round_num,
                )
                if debate_summary:
                    prev_round_summary = debate_summary
                    # Log debate action
                    result.action_log.append(ActionLogEntry(
                        round_number=round_num,
                        agent_id="debate_moderator",
                        agent_role="moderator",
                        action="debate",
                    ))

        # Calculate round convergence
        round_result.consensus_score = _calculate_convergence(round_result.responses)
        round_result.dominant_sentiment = _dominant_sentiment(round_result.responses)
        result.round_results.append(round_result)

        # Build summary for next round (if no debate summary was generated)
        if not round_result.debate_triggered:
            prev_round_summary = _summarize_round(round_result)

        logger.info(
            "simulation_round_complete",
            simulation_id=simulation_id,
            round=round_num,
            convergence=round_result.consensus_score,
            debate=round_result.debate_triggered,
        )

        # Early stop if converged (>0.8 consensus)
        if round_result.consensus_score > 0.8 and round_num >= 3:
            logger.info("simulation_converged_early", round=round_num)
            break

    # Synthesize final consensus
    consensus = await _synthesize_consensus(client, all_responses, config)
    result.consensus_analysis = consensus.get("analysis", "")
    result.consensus_recommendation = consensus.get("recommendation", "")
    result.consensus_financial_impact = consensus.get("financial_impact", "")
    result.consensus_confidence = consensus.get("confidence", 0.5)
    result.consensus_time_horizon = consensus.get("time_horizon", "medium")
    result.risk_level = consensus.get("risk_level", "medium")
    result.opportunities = consensus.get("opportunities", [])

    result.rounds_completed = len(result.round_results)
    result.duration_seconds = time.time() - start_time
    result.convergence_score = result.round_results[-1].consensus_score if result.round_results else 0.0

    logger.info(
        "simulation_complete",
        simulation_id=simulation_id,
        rounds=result.rounds_completed,
        convergence=result.convergence_score,
        duration=f"{result.duration_seconds:.1f}s",
        total_actions=len(result.action_log),
        debates=sum(1 for r in result.round_results if r.debate_triggered),
    )
    return result


async def _llm_call_with_retry(
    client: AsyncOpenAI,
    *,
    model: str,
    max_tokens: int,
    system: str | None = None,
    messages: list[dict],
) -> str:
    """Stage 4.5: LLM call with exponential backoff retry.

    Retries on transient errors (overloaded, rate limit, 5xx).
    Returns the text content from the first content block.
    """
    last_error: Exception | None = None

    for attempt in range(1 + len(LLM_RETRY_DELAYS)):
        try:
            full_messages = []
            if system:
                full_messages.append({"role": "system", "content": system})
            full_messages.extend(messages)

            response = await client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=full_messages,
                temperature=0.3,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            is_retryable = any(kw in error_str for kw in LLM_RETRYABLE_ERRORS)

            if not is_retryable or attempt >= len(LLM_RETRY_DELAYS):
                raise

            delay = LLM_RETRY_DELAYS[attempt]
            logger.warning(
                "llm_retry",
                attempt=attempt + 1,
                delay=delay,
                error=str(e)[:100],
            )
            await asyncio.sleep(delay)

    raise last_error  # type: ignore[misc]


async def _run_agent_round_with_logging(
    client: AsyncOpenAI,
    agent: AgentProfile,
    seed_document: str,
    scenario_prompt: str,
    round_number: int,
    prev_round_summary: str,
) -> tuple[AgentResponse, ActionLogEntry]:
    """Stage 4.4: Run a single agent round with structured action logging."""
    start_ms = time.time() * 1000
    retries = 0
    error_msg = None

    system_prompt = agent.to_system_prompt()

    user_content = f"""## Simulation Round {round_number}

### Background Knowledge
{seed_document}

### Current Scenario
{scenario_prompt}
"""
    if prev_round_summary and round_number > 1:
        user_content += f"""
### Previous Round Summary (other agents' views)
{prev_round_summary}

Consider the other agents' perspectives but form your own independent analysis.
"""

    user_content += """
Provide your analysis as JSON:
{"analysis": "...", "recommendation": "...", "financial_estimate": "...", "confidence": 0.0-1.0, "time_horizon": "short|medium|long"}"""

    try:
        text = await _llm_call_with_retry(
            client,
            model=mirofish_settings.LLM_MODEL,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        # Parse JSON response
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
            else:
                data = {"analysis": text, "confidence": 0.5}

        response = AgentResponse(
            agent_id=agent.agent_id,
            agent_role=agent.role,
            analysis=data.get("analysis", ""),
            recommendation=data.get("recommendation", ""),
            financial_estimate=data.get("financial_estimate", ""),
            confidence=float(data.get("confidence", 0.5)),
            time_horizon=data.get("time_horizon", "medium"),
        )
        action = "analyze"

    except Exception as e:
        error_msg = str(e)[:200]
        logger.warning("agent_round_failed", agent=agent.agent_id, error=error_msg)
        response = AgentResponse(
            agent_id=agent.agent_id,
            agent_role=agent.role,
            analysis=f"Agent failed to respond: {error_msg}",
            confidence=0.0,
        )
        action = "error"

    duration_ms = time.time() * 1000 - start_ms
    log_entry = ActionLogEntry(
        round_number=round_number,
        agent_id=agent.agent_id,
        agent_role=agent.role,
        action=action,
        confidence=response.confidence,
        duration_ms=round(duration_ms, 1),
        retries=retries,
        error=error_msg,
    )

    return response, log_entry


def _find_divergent_responses(responses: list[AgentResponse]) -> list[AgentResponse]:
    """Stage 4.3: Find the top 3 most divergent agent responses.

    Divergence = responses whose confidence differs most from the mean.
    Returns empty list if divergence is low (all agents roughly agree).
    """
    if len(responses) < 3:
        return []

    valid = [r for r in responses if r.confidence > 0]
    if len(valid) < 3:
        return []

    avg_conf = sum(r.confidence for r in valid) / len(valid)
    variance = sum((r.confidence - avg_conf) ** 2 for r in valid) / len(valid)

    # Only trigger debate if meaningful divergence exists
    if variance < 0.04:  # std dev < 0.2
        return []

    # Sort by distance from mean, pick top 3
    sorted_by_divergence = sorted(valid, key=lambda r: abs(r.confidence - avg_conf), reverse=True)
    return sorted_by_divergence[:3]


async def _run_debate_round(
    client: AsyncOpenAI,
    divergent_responses: list[AgentResponse],
    seed_document: str,
    scenario_prompt: str,
    round_number: int,
) -> str | None:
    """Stage 4.3: Run a debate round where divergent agents present their cases.

    The moderator synthesizes the debate into a summary that informs the next round.
    """
    debate_context = []
    for r in divergent_responses:
        debate_context.append(
            f"**{r.agent_role}** (confidence={r.confidence:.2f}):\n"
            f"Analysis: {r.analysis[:300]}\n"
            f"Recommendation: {r.recommendation[:200]}"
        )

    prompt = f"""You are moderating an ESG simulation debate. Three agents with divergent views
are presenting their cases about this scenario:

{scenario_prompt[:500]}

## Divergent Agent Views

{chr(10).join(debate_context)}

## Your Task
Synthesize these divergent views into a balanced summary that:
1. Identifies the key point of disagreement
2. Highlights the strongest argument from each side
3. Notes where further analysis is needed

Keep your summary under 300 words. Write in third person ("The agents disagree on...")."""

    try:
        text = await _llm_call_with_retry(
            client,
            model=mirofish_settings.LLM_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return text
    except Exception as e:
        logger.warning("debate_round_failed", error=str(e))
        return None


def _calculate_convergence(responses: list[AgentResponse]) -> float:
    """Calculate convergence score (0-1) for a set of agent responses.

    Higher score = more agreement among agents.
    Based on confidence variance and sentiment alignment.
    """
    if not responses:
        return 0.0

    confidences = [r.confidence for r in responses if r.confidence > 0]
    if not confidences:
        return 0.0

    # Low variance in confidence = high convergence
    avg_confidence = sum(confidences) / len(confidences)
    variance = sum((c - avg_confidence) ** 2 for c in confidences) / len(confidences)
    confidence_convergence = max(0, 1 - variance * 4)

    # Time horizon agreement
    horizons = [r.time_horizon for r in responses]
    most_common = max(set(horizons), key=horizons.count) if horizons else "medium"
    horizon_agreement = horizons.count(most_common) / len(horizons) if horizons else 0

    return round((confidence_convergence * 0.6 + horizon_agreement * 0.4), 3)


def _dominant_sentiment(responses: list[AgentResponse]) -> str:
    """Determine dominant sentiment from agent responses."""
    if not responses:
        return "neutral"
    avg_confidence = sum(r.confidence for r in responses) / len(responses)
    if avg_confidence > 0.7:
        return "high_concern"
    if avg_confidence > 0.5:
        return "moderate_concern"
    return "low_concern"


def _summarize_round(round_result: RoundResult) -> str:
    """Create a summary of a round for the next round's context."""
    summaries = []
    for r in round_result.responses[:5]:  # Limit to avoid token bloat
        summaries.append(f"- {r.agent_role}: {r.analysis[:150]}...")
    return "\n".join(summaries)


async def _synthesize_consensus(
    client: AsyncOpenAI,
    all_responses: list[AgentResponse],
    config: SimulationConfig,
) -> dict:
    """Synthesize a consensus from all agent responses across all rounds."""
    # Collect unique analyses
    analyses = []
    for r in all_responses:
        if r.analysis and r.confidence > 0.1:
            analyses.append(f"[{r.agent_role}, confidence={r.confidence}]: {r.analysis[:200]}")

    # Limit to last round + top confidence
    analyses = analyses[-30:]  # Last 30 responses

    prompt = f"""You are synthesizing the results of an ESG simulation with {config.agent_count} agents
over multiple rounds, analyzing the impact of a news event on {config.company_name}.

Agent analyses:
{chr(10).join(analyses)}

Synthesize into a JSON consensus:
{{
    "analysis": "2-3 sentence synthesis of the overall impact assessment",
    "recommendation": "Top 3 actionable recommendations, numbered",
    "financial_impact": "Estimated financial impact range in INR",
    "confidence": 0.0-1.0,
    "time_horizon": "short|medium|long",
    "risk_level": "low|medium|high|critical",
    "opportunities": ["opportunity 1", "opportunity 2"]
}}

Return JSON only, no markdown."""

    try:
        text = await _llm_call_with_retry(
            client,
            model=mirofish_settings.LLM_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return json.loads(text)
    except Exception as e:
        logger.error("consensus_synthesis_failed", error=str(e))
        return {
            "analysis": "Consensus synthesis failed",
            "recommendation": "Manual review required",
            "confidence": 0.0,
        }
