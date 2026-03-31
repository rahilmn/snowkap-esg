"""LangGraph state machine for ESG AI agent.

Per MASTER_BUILD_PLAN Phase 5 + Stage 7:
  load_context → classify_intent → query_tools → synthesise → quality_gate → (loop or END)

Stage 7.1: Tool chaining — after synthesise, loop back to query_tools if more data needed (max 2 rounds)
Stage 7.2: Quality gates — validate response length, tool data usage, framework refs
Stage 7.5: Escalation protocol — track low-confidence turns, offer specialist switch/human escalation
"""

import json
import re
from typing import Any, TypedDict

import structlog

from backend.services.agent_service import (
    AGENT_ROSTER,
    classify_intent_with_llm,
    load_personality,
    route_to_specialist,
    run_agent_conversation,
)

logger = structlog.get_logger()

# Stage 7.2: Quality gate constants
MIN_RESPONSE_LENGTH = 100
MAX_RESPONSE_LENGTH = 3000
MAX_TOOL_ROUNDS = 2  # Stage 7.1: max tool chaining rounds

# Stage 7.5: Escalation thresholds
ESCALATION_LOW_CONFIDENCE_THRESHOLD = 0.5
ESCALATION_CONSECUTIVE_LIMIT = 3

# Framework codes that compliance/legal agents should reference
_FRAMEWORK_CODES = {"BRSR", "GRI", "TCFD", "ESRS", "CDP", "IFRS", "CSRD", "SASB", "SEBI", "CBAM"}
_FRAMEWORK_AGENTS = {"compliance", "legal", "content"}


class AgentState(TypedDict):
    """LangGraph state for the ESG agent."""
    question: str
    tenant_id: str
    user_id: str
    active_agent: str
    agent_name: str
    personality: str
    context: dict[str, Any]
    tool_results: list[dict]
    response: str
    classification: dict[str, Any]
    error: str | None
    # Stage 7.1: Tool chaining
    tool_round: int
    needs_more_data: bool
    additional_tool_request: str
    # Stage 7.2: Quality gate
    quality_passed: bool
    quality_retry_count: int
    quality_feedback: str
    # Stage 7.5: Escalation
    low_confidence_count: int
    escalation_offered: bool
    # Role personalization
    designation: str


# --- Node Functions ---

async def load_context(state: AgentState) -> dict:
    """Load tenant context, user permissions, company data."""
    logger.info("agent_load_context", tenant_id=state["tenant_id"])

    from backend.agent.memory import memory_manager

    # Get conversation memory
    memory = await memory_manager.get_memory(
        state["tenant_id"], state["user_id"], last_n=5,
    )
    context_summary = await memory_manager.get_context_summary(
        state["tenant_id"], state["user_id"],
    )

    # Stage 7.4: Get cross-agent context if switching agents
    cross_agent_context = []
    if state.get("active_agent"):
        cross_agent_context = await memory_manager.get_agent_memory(
            state["tenant_id"], state["user_id"],
            agent_id=state["active_agent"], last_n=3,
        )

    # Stage 7.5: Load escalation state from recent memory
    low_confidence_count = 0
    for msg in memory[-ESCALATION_CONSECUTIVE_LIMIT:]:
        meta = msg.get("metadata", {})
        if meta.get("role") == "assistant" and meta.get("confidence", 1.0) < ESCALATION_LOW_CONFIDENCE_THRESHOLD:
            low_confidence_count += 1
        else:
            low_confidence_count = 0

    return {
        "context": {
            "loaded": True,
            "memory": memory,
            "context_summary": context_summary,
            "cross_agent_context": cross_agent_context,
        },
        "low_confidence_count": low_confidence_count,
    }


async def classify_intent(state: AgentState) -> dict:
    """Classify user intent and route to specialist agent."""
    context_str = ""
    if state.get("context", {}).get("context_summary"):
        context_str = state["context"]["context_summary"]

    classification = await classify_intent_with_llm(
        state["question"], context_str,
    )

    selected = classification["agent"]
    agent_config = AGENT_ROSTER[selected]
    personality = load_personality(agent_config["personality_key"])

    logger.info(
        "agent_classified",
        agent=selected,
        agent_name=agent_config["name"],
        method=classification.get("method"),
    )

    return {
        "active_agent": selected,
        "agent_name": agent_config["name"],
        "personality": personality,
        "classification": classification,
    }


async def query_tools(state: AgentState) -> dict:
    """Execute tools with the selected specialist agent.

    Stage 7.1: Supports multiple rounds — on round 2+, uses additional_tool_request
    to target specific tools/data.
    """
    from backend.services.agent_service import _gather_tool_context

    agent_config = AGENT_ROSTER.get(state["active_agent"], {})
    tools = agent_config.get("tools", [])
    tool_round = state.get("tool_round", 0) + 1

    if tool_round > 1 and state.get("additional_tool_request"):
        # Stage 7.1: Targeted tool request from previous synthesis
        tool_context = await _gather_tool_context(
            tenant_id=state["tenant_id"],
            question=state["additional_tool_request"],
            tools=tools,
            db=None,
        )
        # Merge with existing results
        existing = {r["tool"]: r["data"] for r in state.get("tool_results", [])}
        for k, v in tool_context.items():
            existing[f"{k}_round{tool_round}"] = v
        tool_results = [{"tool": k, "data": v} for k, v in existing.items()]
    else:
        tool_context = await _gather_tool_context(
            tenant_id=state["tenant_id"],
            question=state["question"],
            tools=tools,
            db=None,
        )
        tool_results = [{"tool": k, "data": v} for k, v in tool_context.items()]

    logger.info(
        "agent_tools_executed",
        agent=state["active_agent"],
        round=tool_round,
        tools=list(tool_context.keys()),
    )

    return {
        "tool_results": tool_results,
        "tool_round": tool_round,
    }


async def synthesise(state: AgentState) -> dict:
    """Generate response using specialist agent personality.

    Stage 7.1: Response may include [NEED_MORE_DATA: <request>] to trigger another tool round.
    """
    from backend.services.agent_service import _generate_response

    tool_context = {r["tool"]: r["data"] for r in state.get("tool_results", [])}

    # Build enhanced personality with tool chaining instructions
    personality = state.get("personality", "You are an ESG analyst.")
    tool_round = state.get("tool_round", 1)

    if tool_round < MAX_TOOL_ROUNDS:
        personality += (
            "\n\n## Tool Chaining"
            "\nIf you need additional data to give a complete answer, include exactly one line:"
            "\n[NEED_MORE_DATA: describe what additional data you need]"
            "\nThis will trigger another tool query round. You get 1 additional round max."
        )

    # Stage 7.2: If retrying after quality gate failure, add feedback
    quality_feedback = state.get("quality_feedback", "")
    if quality_feedback:
        personality += (
            f"\n\n## Quality Revision Required"
            f"\nYour previous response didn't meet quality standards: {quality_feedback}"
            f"\nPlease revise your response to address this feedback."
        )

    # Stage 7.4: Include cross-agent context
    cross_agent = state.get("context", {}).get("cross_agent_context", [])
    memory_context = state.get("context", {}).get("context_summary")
    if cross_agent:
        cross_summary = "\n".join(
            f"[{m.get('metadata', {}).get('agent', '?')}] {m.get('content', '')[:200]}"
            for m in cross_agent
        )
        if memory_context:
            memory_context += f"\n\n## Cross-Agent Context\n{cross_summary}"
        else:
            memory_context = f"## Cross-Agent Context\n{cross_summary}"

    # Resolve role profile for personalization
    synth_role_profile = None
    synth_designation = state.get("designation") or None
    if synth_designation:
        from backend.core.permissions import map_designation_to_role
        from backend.services.role_curation import get_role_profile
        mapped_role = map_designation_to_role(synth_designation)
        synth_role_profile = get_role_profile(mapped_role)

    response = await _generate_response(
        personality=personality,
        question=state["question"],
        tool_context=tool_context,
        memory_context=memory_context,
        recent_messages=state.get("context", {}).get("memory", []),
        tenant_id=state["tenant_id"],
        role_profile=synth_role_profile,
        designation=synth_designation,
    )

    # Stage 7.1: Check if agent requests more data
    needs_more = False
    additional_request = ""
    more_data_match = re.search(r'\[NEED_MORE_DATA:\s*(.+?)\]', response)
    if more_data_match and tool_round < MAX_TOOL_ROUNDS:
        needs_more = True
        additional_request = more_data_match.group(1).strip()
        # Strip the directive from the response for cleanliness
        response = re.sub(r'\[NEED_MORE_DATA:\s*.+?\]', '', response).strip()
        logger.info("agent_needs_more_data", request=additional_request, round=tool_round)

    # Store in memory
    from backend.agent.memory import memory_manager
    await memory_manager.add_message(
        state["tenant_id"], state["user_id"],
        "user", state["question"],
        metadata={"agent": state["active_agent"]},
    )
    await memory_manager.add_message(
        state["tenant_id"], state["user_id"],
        "assistant", response,
        metadata={
            "agent": state["active_agent"],
            "confidence": state.get("classification", {}).get("confidence", 0.8),
        },
    )

    logger.info("agent_response_generated", agent=state["active_agent"], round=tool_round)

    return {
        "response": response,
        "needs_more_data": needs_more,
        "additional_tool_request": additional_request,
    }


async def quality_gate(state: AgentState) -> dict:
    """Stage 7.2: Validate the synthesised response against quality standards.

    Checks:
    - Response length: min 100, max 3000 chars
    - Tool data usage: response should reference data from tool results
    - Framework references: compliance/legal agents must cite framework codes
    - On failure: sets quality_feedback for retry
    """
    response = state.get("response", "")
    agent_id = state.get("active_agent", "")
    tool_results = state.get("tool_results", [])
    retry_count = state.get("quality_retry_count", 0)

    # Don't retry more than once
    if retry_count >= 1:
        logger.info("quality_gate_skip_max_retries", agent=agent_id)
        return {"quality_passed": True}

    issues: list[str] = []

    # Check 1: Response length
    if len(response) < MIN_RESPONSE_LENGTH:
        issues.append(f"Response too short ({len(response)} chars, minimum {MIN_RESPONSE_LENGTH}). Provide more detail.")
    if len(response) > MAX_RESPONSE_LENGTH:
        issues.append(f"Response too long ({len(response)} chars, maximum {MAX_RESPONSE_LENGTH}). Be more concise.")

    # Check 2: Tool data usage — response should reference specifics from tool data
    if tool_results and len(response) >= MIN_RESPONSE_LENGTH:
        has_specifics = False
        for result in tool_results:
            data = result.get("data", {})
            data_str = json.dumps(data, default=str).lower()
            # Check if any specific values from tool data appear in the response
            # Look for numbers, entity names, or specific terms
            for token in _extract_data_tokens(data_str):
                if token in response.lower():
                    has_specifics = True
                    break
            if has_specifics:
                break
        if not has_specifics and tool_results:
            issues.append("Response doesn't reference specific data from the tools. Include concrete data points.")

    # Check 3: Framework references for compliance/legal/content agents
    if agent_id in _FRAMEWORK_AGENTS:
        response_upper = response.upper()
        has_framework = any(fw in response_upper for fw in _FRAMEWORK_CODES)
        if not has_framework:
            issues.append(f"As a {agent_id} agent, your response should reference specific framework codes (e.g., BRSR, GRI, TCFD).")

    if issues:
        feedback = " ".join(issues)
        logger.info("quality_gate_failed", agent=agent_id, issues=issues)
        return {
            "quality_passed": False,
            "quality_feedback": feedback,
            "quality_retry_count": retry_count + 1,
        }

    logger.info("quality_gate_passed", agent=agent_id)
    return {"quality_passed": True}


async def escalation_check(state: AgentState) -> dict:
    """Stage 7.5: Check if escalation is needed based on low-confidence history.

    After 3 consecutive low-confidence turns:
    - Offer to switch specialist agent
    - Offer to escalate to human
    - Offer raw data for user interpretation
    """
    confidence = state.get("classification", {}).get("confidence", 0.8)
    low_count = state.get("low_confidence_count", 0)

    if confidence < ESCALATION_LOW_CONFIDENCE_THRESHOLD:
        low_count += 1
    else:
        low_count = 0

    if low_count >= ESCALATION_CONSECUTIVE_LIMIT and not state.get("escalation_offered"):
        # Build escalation message
        current_agent = state.get("agent_name", "the current agent")
        tool_data_summary = ""
        for r in state.get("tool_results", [])[:3]:
            data = r.get("data", {})
            if isinstance(data, dict) and "error" not in data:
                tool_data_summary += f"\n- {r['tool']}: {json.dumps(data, default=str)[:300]}"

        escalation_msg = (
            f"\n\n---\n"
            f"**I'm having difficulty providing a confident answer.** Here are your options:\n\n"
            f"1. **Switch specialist** — Try a different agent better suited to this question\n"
            f"2. **Escalate to human** — Flag this for a human ESG analyst to review\n"
            f"3. **View raw data** — See the underlying data I found:{tool_data_summary[:500] if tool_data_summary else ' (no tool data available)'}\n"
            f"\nReply with your preference, or rephrase your question for {current_agent}."
        )

        response = state.get("response", "") + escalation_msg
        logger.info("escalation_triggered", agent=state.get("active_agent"), low_count=low_count)

        return {
            "response": response,
            "escalation_offered": True,
            "low_confidence_count": low_count,
        }

    return {"low_confidence_count": low_count}


# --- Routing Functions ---

def should_loop_tools(state: AgentState) -> str:
    """Stage 7.1: Route after quality gate — loop back to tools or finish."""
    if not state.get("quality_passed", True):
        return "synthesise"  # Retry synthesis with quality feedback
    if state.get("needs_more_data") and state.get("tool_round", 0) < MAX_TOOL_ROUNDS:
        return "query_tools"  # Tool chaining: gather more data
    return "escalation_check"


def _extract_data_tokens(data_str: str) -> list[str]:
    """Extract meaningful tokens from tool data for quality gate checking."""
    tokens = []
    # Extract numbers (scores, counts, IDs)
    numbers = re.findall(r'\d+\.?\d*', data_str)
    tokens.extend(n for n in numbers if len(n) >= 2)
    # Extract capitalized terms (entity names, codes)
    words = re.findall(r'[A-Z][a-z]+(?:\s[A-Z][a-z]+)*', data_str)
    tokens.extend(w.lower() for w in words if len(w) >= 4)
    return tokens[:20]  # Cap to avoid excessive checking


# --- Graph Construction ---

def build_graph():
    """Build the LangGraph state machine with tool chaining and quality gates.

    Stage 7.1: Conditional edge after quality_gate loops back to query_tools
    Stage 7.2: quality_gate node validates response
    Stage 7.5: escalation_check node appends escalation options if needed
    """
    try:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AgentState)

        # Add nodes
        graph.add_node("load_context", load_context)
        graph.add_node("classify_intent", classify_intent)
        graph.add_node("query_tools", query_tools)
        graph.add_node("synthesise", synthesise)
        graph.add_node("quality_gate", quality_gate)
        graph.add_node("escalation_check", escalation_check)

        # Define edges
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "classify_intent")
        graph.add_edge("classify_intent", "query_tools")
        graph.add_edge("query_tools", "synthesise")
        graph.add_edge("synthesise", "quality_gate")

        # Stage 7.1 + 7.2: Conditional routing after quality gate
        graph.add_conditional_edges(
            "quality_gate",
            should_loop_tools,
            {
                "synthesise": "synthesise",      # Quality retry
                "query_tools": "query_tools",    # Tool chaining
                "escalation_check": "escalation_check",  # Continue to finish
            },
        )
        graph.add_edge("escalation_check", END)

        compiled = graph.compile()
        logger.info("langgraph_compiled", features=["tool_chaining", "quality_gates", "escalation"])
        return compiled

    except ImportError:
        logger.info("langgraph_not_available_using_sequential")
        return None


# Lazy-init compiled graph
_compiled_graph = None


def get_graph():
    """Get or build the compiled graph (singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_agent_pipeline(
    tenant_id: str,
    user_id: str,
    question: str,
    agent_id: str | None = None,
    db=None,
    article_id: str | None = None,
    designation: str | None = None,
) -> dict:
    """Universal entry point for agent conversations.

    Uses LangGraph if available, otherwise runs sequential pipeline
    via agent_service.run_agent_conversation().
    """
    graph = get_graph()

    # When article_id is provided, use sequential pipeline for ontology-driven context
    # LangGraph doesn't carry article_id through its state machine
    if article_id:
        return await run_agent_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            question=question,
            agent_id=agent_id,
            db=db,
            article_id=article_id,
            designation=designation,
        )

    if graph:
        # Run via LangGraph
        try:
            initial_state: AgentState = {
                "question": question,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "active_agent": agent_id or "",
                "agent_name": "",
                "personality": "",
                "context": {},
                "tool_results": [],
                "response": "",
                "classification": {},
                "error": None,
                # Stage 7.1
                "tool_round": 0,
                "needs_more_data": False,
                "additional_tool_request": "",
                # Stage 7.2
                "quality_passed": True,
                "quality_retry_count": 0,
                "quality_feedback": "",
                # Stage 7.5
                "low_confidence_count": 0,
                "escalation_offered": False,
                # Role personalization
                "designation": designation or "",
            }

            if agent_id and agent_id in AGENT_ROSTER:
                initial_state["active_agent"] = agent_id
                initial_state["agent_name"] = AGENT_ROSTER[agent_id]["name"]
                initial_state["personality"] = load_personality(
                    AGENT_ROSTER[agent_id]["personality_key"]
                )

            result = await graph.ainvoke(initial_state)

            return {
                "response": result.get("response", "No response generated."),
                "agent": {
                    "id": result.get("active_agent", "analytics"),
                    "name": result.get("agent_name", "ESG Analytics Agent"),
                },
                "classification": result.get("classification", {}),
                "tools_used": [r["tool"] for r in result.get("tool_results", [])],
                "tool_rounds": result.get("tool_round", 1),
                "quality_passed": result.get("quality_passed", True),
                "escalation_offered": result.get("escalation_offered", False),
                "engine": "langgraph",
            }
        except Exception as e:
            logger.error("langgraph_execution_error", error=str(e))
            # Fall through to sequential

    # Sequential fallback
    return await run_agent_conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        question=question,
        agent_id=agent_id,
        db=db,
        article_id=article_id,
        designation=designation,
    )
