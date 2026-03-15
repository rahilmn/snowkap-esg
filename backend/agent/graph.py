"""LangGraph state machine for ESG AI agent.

Per MASTER_BUILD_PLAN Phase 5:
  load_context → classify_intent → route_to_specialist → query → synthesise

Per CLAUDE.md: 9 specialist agents embedded in product via LangGraph.
Each agent: Agency personality prompt + SNOWKAP tools.

This module defines the state machine graph that orchestrates the
agent conversation pipeline. It can run with or without the langgraph
package — falls back to sequential execution.
"""

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

    return {
        "context": {
            "loaded": True,
            "memory": memory,
            "context_summary": context_summary,
        },
    }


async def classify_intent(state: AgentState) -> dict:
    """Classify user intent and route to specialist agent.

    Uses Claude for classification, falls back to keyword matching.
    """
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
    """Execute tools (SPARQL, DB, prediction) with the selected specialist agent."""
    from backend.services.agent_service import _gather_tool_context

    agent_config = AGENT_ROSTER.get(state["active_agent"], {})
    tools = agent_config.get("tools", [])

    tool_results = await _gather_tool_context(
        tenant_id=state["tenant_id"],
        question=state["question"],
        tools=tools,
        db=None,  # DB session not available in pure graph mode
    )

    logger.info("agent_tools_executed", agent=state["active_agent"], tools=list(tool_results.keys()))

    return {
        "tool_results": [{"tool": k, "data": v} for k, v in tool_results.items()],
    }


async def synthesise(state: AgentState) -> dict:
    """Generate final response using specialist agent personality."""
    from backend.services.agent_service import _generate_response

    tool_context = {r["tool"]: r["data"] for r in state.get("tool_results", [])}

    response = await _generate_response(
        personality=state.get("personality", "You are an ESG analyst."),
        question=state["question"],
        tool_context=tool_context,
        memory_context=state.get("context", {}).get("context_summary"),
        recent_messages=state.get("context", {}).get("memory", []),
        tenant_id=state["tenant_id"],
    )

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
        metadata={"agent": state["active_agent"]},
    )

    logger.info("agent_response_generated", agent=state["active_agent"])

    return {"response": response}


# --- Graph Construction ---

def build_graph():
    """Build the LangGraph state machine.

    Returns a compiled graph if langgraph is available, otherwise None.
    Use run_agent_pipeline() as the universal entry point.
    """
    try:
        from langgraph.graph import END, StateGraph

        graph = StateGraph(AgentState)

        # Add nodes
        graph.add_node("load_context", load_context)
        graph.add_node("classify_intent", classify_intent)
        graph.add_node("query_tools", query_tools)
        graph.add_node("synthesise", synthesise)

        # Define edges: linear pipeline
        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "classify_intent")
        graph.add_edge("classify_intent", "query_tools")
        graph.add_edge("query_tools", "synthesise")
        graph.add_edge("synthesise", END)

        compiled = graph.compile()
        logger.info("langgraph_compiled")
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
) -> dict:
    """Universal entry point for agent conversations.

    Uses LangGraph if available, otherwise runs sequential pipeline
    via agent_service.run_agent_conversation().
    """
    graph = get_graph()

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
            }

            if agent_id and agent_id in AGENT_ROSTER:
                # Pre-set the agent if specified
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
    )
