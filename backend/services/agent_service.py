"""Agent service — LangGraph + Agency agents.

Per MASTER_BUILD_PLAN Phase 5:
- 9 runtime Agency agent personalities as LangGraph specialist nodes
- Agent routing: Claude classifies user intent → selects specialist
- Personality loading from markdown files
- Full conversation pipeline with tool execution
"""

import json
from pathlib import Path
from typing import Any

import structlog

from backend.core.config import settings

logger = structlog.get_logger()

# Directory containing agent personality markdown files
PERSONALITIES_DIR = Path(__file__).parent.parent / "agent" / "personalities"


def load_personality(agent_key: str) -> str:
    """Load an agent's personality prompt from its markdown file."""
    md_path = PERSONALITIES_DIR / f"{agent_key}.md"
    if md_path.exists():
        return md_path.read_text(encoding="utf-8")
    logger.warning("personality_not_found", agent=agent_key, path=str(md_path))
    return f"You are an ESG specialist agent. Provide helpful analysis."


# Agent roster per MASTER_BUILD_PLAN Part 3B: Runtime Agents
AGENT_ROSTER: dict[str, dict[str, Any]] = {
    "supply_chain": {
        "name": "ESG Supply Chain Analyst",
        "personality_key": "supply_chain",
        "keywords": ["supply chain", "supplier", "scope 3", "upstream", "downstream", "procurement", "tier 1", "tier 2", "commodity", "logistics"],
        "tools": ["sparql", "database", "causal_chain"],
    },
    "compliance": {
        "name": "ESG Compliance Monitor",
        "personality_key": "compliance",
        "keywords": ["compliance", "brsr", "gri", "tcfd", "esrs", "regulation", "disclosure", "framework", "reporting", "audit", "gap analysis"],
        "tools": ["sparql", "database", "ontology_rules"],
    },
    "analytics": {
        "name": "ESG Analytics Agent",
        "personality_key": "analytics",
        "keywords": ["analytics", "report", "dashboard", "kpi", "metrics", "trend", "benchmark", "score", "performance", "data"],
        "tools": ["database", "sparql", "prediction"],
    },
    "executive": {
        "name": "CXO Briefing Agent",
        "personality_key": "executive",
        "keywords": ["executive", "summary", "briefing", "cxo", "board", "strategic", "ceo", "cfo", "investor", "financial impact"],
        "tools": ["database", "prediction", "causal_chain"],
    },
    "trend": {
        "name": "ESG Trend Scout",
        "personality_key": "trend",
        "keywords": ["trend", "forecast", "emerging", "future", "prediction", "signal", "outlook", "upcoming", "next year"],
        "tools": ["database", "prediction", "causal_chain"],
    },
    "stakeholder": {
        "name": "Stakeholder Voice Agent",
        "personality_key": "stakeholder",
        "keywords": ["stakeholder", "feedback", "investor", "rating", "esg score", "perception", "reputation", "community", "employee"],
        "tools": ["database", "sparql"],
    },
    "opportunity": {
        "name": "ESG Opportunity Finder",
        "personality_key": "opportunity",
        "keywords": ["opportunity", "growth", "green revenue", "market", "advantage", "roi", "investment", "carbon credit", "sustainable finance"],
        "tools": ["database", "prediction", "sparql"],
    },
    "content": {
        "name": "ESG Report Writer",
        "personality_key": "content",
        "keywords": ["content", "write", "newsletter", "sustainability report", "draft", "narrative", "disclosure", "section"],
        "tools": ["database", "sparql", "ontology_rules"],
    },
    "legal": {
        "name": "Regulatory Intelligence Agent",
        "personality_key": "legal",
        "keywords": ["legal", "regulatory", "cbam", "sebi", "epa", "law", "policy", "penalty", "deadline", "mandate"],
        "tools": ["database", "sparql", "ontology_rules"],
    },
}


def route_to_specialist(question: str) -> str:
    """Route user question to the best specialist agent based on keyword scoring.

    Uses weighted keyword matching. Falls back to 'analytics' as the default generalist.
    """
    question_lower = question.lower()
    best_match = "analytics"  # Default agent
    best_score = 0

    for agent_id, config in AGENT_ROSTER.items():
        score = sum(1 for kw in config["keywords"] if kw in question_lower)
        if score > best_score:
            best_score = score
            best_match = agent_id

    logger.info("agent_routed", agent=best_match, score=best_score, question_preview=question[:80])
    return best_match


async def classify_intent_with_llm(question: str, conversation_context: str = "") -> dict:
    """Use Claude to classify user intent and select the best specialist.

    Falls back to keyword routing if LLM is unavailable.
    """
    if not settings.ANTHROPIC_API_KEY:
        return {
            "agent": route_to_specialist(question),
            "method": "keyword",
            "intent": "general_query",
        }

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        agent_descriptions = "\n".join(
            f"- {aid}: {cfg['name']} — keywords: {', '.join(cfg['keywords'][:5])}"
            for aid, cfg in AGENT_ROSTER.items()
        )

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system="You are a routing classifier. Given a user question about ESG/sustainability, select the most appropriate specialist agent. Respond with ONLY valid JSON.",
            messages=[{
                "role": "user",
                "content": f"""Available specialist agents:
{agent_descriptions}

User question: "{question}"
{f'Conversation context: {conversation_context[:500]}' if conversation_context else ''}

Respond with JSON: {{"agent": "<agent_id>", "intent": "<brief_intent>", "confidence": 0.0-1.0}}""",
            }],
        )

        text = response.content[0].text.strip()
        # Parse JSON from response
        if text.startswith("{"):
            result = json.loads(text)
            if result.get("agent") in AGENT_ROSTER:
                return {**result, "method": "llm"}

    except Exception as e:
        logger.warning("llm_classification_failed", error=str(e))

    # Fallback to keyword routing
    return {
        "agent": route_to_specialist(question),
        "method": "keyword_fallback",
        "intent": "general_query",
    }


async def run_agent_conversation(
    tenant_id: str,
    user_id: str,
    question: str,
    agent_id: str | None = None,
    db=None,
) -> dict:
    """Run a full agent conversation turn.

    1. Classify intent → select agent (or use provided agent_id)
    2. Load agent personality
    3. Gather relevant data via tools
    4. Generate response with Claude using agent personality

    Returns the agent's response with metadata.
    """
    from backend.agent.memory import memory_manager

    # Step 1: Route to specialist
    if agent_id and agent_id in AGENT_ROSTER:
        classification = {"agent": agent_id, "method": "user_selected", "intent": "direct"}
    else:
        # Get conversation context for better routing
        recent = await memory_manager.get_memory(tenant_id, user_id, last_n=3)
        context = " ".join(m.get("content", "") for m in recent)
        classification = await classify_intent_with_llm(question, context)

    selected_agent = classification["agent"]
    agent_config = AGENT_ROSTER[selected_agent]

    # Step 2: Load personality
    personality = load_personality(agent_config["personality_key"])

    # Step 3: Gather tool data
    tool_context = await _gather_tool_context(
        tenant_id=tenant_id,
        question=question,
        tools=agent_config["tools"],
        db=db,
    )

    # Step 4: Get conversation memory
    memory_context = await memory_manager.get_context_summary(tenant_id, user_id)
    recent_messages = await memory_manager.get_memory(tenant_id, user_id, last_n=5)

    # Step 5: Generate response
    response = await _generate_response(
        personality=personality,
        question=question,
        tool_context=tool_context,
        memory_context=memory_context,
        recent_messages=recent_messages,
        tenant_id=tenant_id,
    )

    # Step 6: Store conversation turn in memory
    await memory_manager.add_message(
        tenant_id, user_id, "user", question,
        metadata={"agent": selected_agent},
    )
    await memory_manager.add_message(
        tenant_id, user_id, "assistant", response,
        metadata={"agent": selected_agent, "classification": classification},
    )

    return {
        "response": response,
        "agent": {
            "id": selected_agent,
            "name": agent_config["name"],
        },
        "classification": classification,
        "tools_used": list(tool_context.keys()),
    }


async def _gather_tool_context(
    tenant_id: str,
    question: str,
    tools: list[str],
    db=None,
) -> dict[str, Any]:
    """Run relevant tools to gather context for the agent's response."""
    from backend.agent.tools import TOOL_REGISTRY

    context = {}
    question_lower = question.lower()

    for tool_name in tools:
        tool_info = TOOL_REGISTRY.get(tool_name)
        if not tool_info:
            continue

        try:
            if tool_name == "sparql":
                # Only run SPARQL if question seems to need ontology data
                ontology_keywords = ["ontology", "knowledge graph", "supply chain", "causal", "framework", "company"]
                if any(kw in question_lower for kw in ontology_keywords):
                    result = await tool_info["fn"](
                        tenant_id=tenant_id,
                        query=f"""
                        SELECT ?s ?p ?o WHERE {{
                            GRAPH <urn:snowkap:tenant:{tenant_id}> {{
                                ?s ?p ?o
                            }}
                        }} LIMIT 20
                        """,
                    )
                    context["sparql"] = result

            elif tool_name == "database":
                # Fetch recent articles and company info
                if db:
                    articles = await tool_info["fn"](
                        tenant_id=tenant_id,
                        table="articles",
                        filters={},
                        db=db,
                    )
                    context["recent_articles"] = {
                        "count": articles.get("count", 0),
                        "sample": articles.get("records", [])[:5],
                    }

            elif tool_name == "causal_chain":
                # Extract entity from question for causal chain lookup
                # Simple extraction: look for quoted terms or capitalized words
                import re
                quoted = re.findall(r'"([^"]+)"', question)
                if quoted:
                    result = await tool_info["fn"](
                        tenant_id=tenant_id,
                        entity_name=quoted[0],
                    )
                    context["causal_chains"] = result

            elif tool_name == "prediction":
                if db:
                    result = await tool_info["fn"](
                        tenant_id=tenant_id,
                        db=db,
                    )
                    context["predictions"] = result

            elif tool_name == "ontology_rules":
                result = await tool_info["fn"](
                    tenant_id=tenant_id,
                    action="list",
                )
                context["ontology_rules"] = result

        except Exception as e:
            logger.warning("tool_context_error", tool=tool_name, error=str(e))
            context[tool_name] = {"error": str(e)}

    return context


async def _generate_response(
    personality: str,
    question: str,
    tool_context: dict,
    memory_context: str | None,
    recent_messages: list[dict],
    tenant_id: str,
) -> str:
    """Generate the agent's response using Claude with personality and context."""
    if not settings.ANTHROPIC_API_KEY:
        return f"I'd be happy to help with your ESG question, but the AI service is not configured. Please set the ANTHROPIC_API_KEY environment variable."

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Build system prompt with personality + context
        system_parts = [
            personality,
            "\n## Platform Context",
            "You are part of the SNOWKAP ESG Intelligence Platform.",
            "You have access to the company's knowledge graph, news articles, predictions, and ontology rules.",
            f"Current tenant: {tenant_id}",
        ]

        if memory_context:
            system_parts.append(f"\n## Conversation Summary\n{memory_context}")

        if tool_context:
            system_parts.append("\n## Available Data")
            for tool_name, data in tool_context.items():
                if isinstance(data, dict) and "error" not in data:
                    # Truncate large data
                    data_str = json.dumps(data, default=str)
                    if len(data_str) > 2000:
                        data_str = data_str[:2000] + "... [truncated]"
                    system_parts.append(f"\n### {tool_name}\n```json\n{data_str}\n```")

        system_prompt = "\n".join(system_parts)

        # Build messages (include recent conversation)
        messages = []
        for msg in recent_messages[-5:]:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
            })
        messages.append({"role": "user", "content": question})

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=system_prompt,
            messages=messages,
        )

        return response.content[0].text

    except Exception as e:
        logger.error("agent_response_error", error=str(e))
        return f"I encountered an error while analyzing your question: {str(e)}"
