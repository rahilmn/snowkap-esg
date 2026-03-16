"""Agent service — LangGraph + Agency agents.

Per MASTER_BUILD_PLAN Phase 5 + Stage 7:
- 9 runtime Agency agent personalities as LangGraph specialist nodes
- Agent routing: Claude classifies user intent → selects specialist
- Personality loading from markdown files
- Full conversation pipeline with tool execution
- Stage 7.3: Agent-to-agent handoff protocol
- Stage 7.6: NEXUS-Lite multi-agent orchestration pipelines
"""

import json
import re
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
    from backend.core import llm as llm_client

    if not llm_client.is_configured():
        return {
            "agent": route_to_specialist(question),
            "method": "keyword",
            "intent": "general_query",
        }

    try:
        agent_descriptions = "\n".join(
            f"- {aid}: {cfg['name']} — keywords: {', '.join(cfg['keywords'][:5])}"
            for aid, cfg in AGENT_ROSTER.items()
        )

        text = await llm_client.chat(
            system="You are a routing classifier. Given a user question about ESG/sustainability, select the most appropriate specialist agent. Respond with ONLY valid JSON.",
            messages=[{
                "role": "user",
                "content": f"""Available specialist agents:
{agent_descriptions}

User question: "{question}"
{f'Conversation context: {conversation_context[:500]}' if conversation_context else ''}

Respond with JSON: {{"agent": "<agent_id>", "intent": "<brief_intent>", "confidence": 0.0-1.0}}""",
            }],
            max_tokens=200,
        )

        text = text.strip()
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

    1. Check for NEXUS pipeline trigger (Stage 7.6)
    2. Classify intent → select agent (or use provided agent_id)
    3. Load agent personality
    4. Gather relevant data via tools
    5. Generate response with Claude using agent personality
    6. Detect handoff suggestions (Stage 7.3)

    Returns the agent's response with metadata.
    """
    from backend.agent.memory import memory_manager

    # Stage 7.6: Check for NEXUS pipeline trigger
    if not agent_id:
        pipeline_id = detect_nexus_pipeline(question)
        if pipeline_id:
            logger.info("nexus_pipeline_detected", pipeline=pipeline_id)
            return await run_nexus_pipeline(
                tenant_id=tenant_id,
                user_id=user_id,
                question=question,
                pipeline_id=pipeline_id,
                db=db,
            )

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

    # Stage 7.3: Add handoff awareness to personality
    adjacent = _HANDOFF_MAP.get(selected_agent, [])
    if adjacent:
        adjacent_names = [AGENT_ROSTER[a]["name"] for a in adjacent if a in AGENT_ROSTER]
        personality += (
            f"\n\n## Handoff Awareness"
            f"\nIf the user's question crosses into another domain, note it in your response."
            f"\nAdjacent specialists: {', '.join(adjacent_names)}"
        )

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

    # Stage 7.4: Enrich with cross-agent topic context
    from backend.agent.memory import extract_topics
    question_topics = extract_topics(question)
    if question_topics:
        topic_memory = await memory_manager.get_topic_memory(
            tenant_id, user_id, question_topics, last_n=3,
        )
        if topic_memory:
            cross_context = "\n".join(
                f"[{m.get('metadata', {}).get('agent', '?')}] {m.get('content', '')[:200]}"
                for m in topic_memory
            )
            if memory_context:
                memory_context += f"\n\n## Related Prior Context\n{cross_context}"
            else:
                memory_context = f"## Related Prior Context\n{cross_context}"

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
        metadata={
            "agent": selected_agent,
            "classification": classification,
            "confidence": classification.get("confidence", 0.8),
        },
    )

    # Stage 7.3: Detect handoff suggestion
    handoff = detect_handoff(selected_agent, response, question)

    result = {
        "response": response,
        "agent": {
            "id": selected_agent,
            "name": agent_config["name"],
        },
        "classification": classification,
        "tools_used": list(tool_context.keys()),
    }

    if handoff:
        result["handoff_suggestion"] = handoff
        logger.info(
            "handoff_detected",
            from_agent=handoff["from"],
            to_agent=handoff["to"],
            signals=handoff["signals"],
        )

    return result


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
    from backend.core import llm as llm_client

    if not llm_client.is_configured():
        return f"I'd be happy to help with your ESG question, but the AI service is not configured. Please set the OPENAI_API_KEY environment variable."

    try:
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

        return await llm_client.chat(
            system=system_prompt,
            messages=messages,
            max_tokens=2000,
        )

    except Exception as e:
        logger.error("agent_response_error", error=str(e))
        return f"I encountered an error while analyzing your question: {str(e)}"


# --- Stage 7.3: Agent-to-Agent Handoff Protocol ---

# Domain adjacency: which agents commonly hand off to each other
_HANDOFF_MAP: dict[str, list[str]] = {
    "compliance": ["legal", "content"],
    "legal": ["compliance", "executive"],
    "supply_chain": ["analytics", "opportunity"],
    "analytics": ["executive", "trend"],
    "executive": ["analytics", "stakeholder"],
    "trend": ["analytics", "opportunity"],
    "stakeholder": ["executive", "content"],
    "opportunity": ["analytics", "executive"],
    "content": ["compliance", "legal"],
}

# Keywords that signal a handoff is needed
_HANDOFF_SIGNALS: dict[str, list[str]] = {
    "legal": ["penalty", "regulation", "sebi", "cbam", "lawsuit", "enforcement", "legal risk"],
    "compliance": ["gap analysis", "disclosure", "brsr section", "framework mapping", "audit"],
    "executive": ["board", "cxo", "briefing", "strategic", "investor presentation"],
    "analytics": ["quantify", "data", "metrics", "benchmark", "score"],
    "supply_chain": ["supplier", "supply chain", "scope 3", "tier 1"],
    "trend": ["emerging", "forecast", "next year", "outlook"],
    "stakeholder": ["investor sentiment", "rating", "reputation", "community"],
    "opportunity": ["green revenue", "carbon credit", "roi", "investment"],
    "content": ["draft", "write", "narrative", "disclosure section", "report"],
}


def detect_handoff(
    agent_id: str,
    response: str,
    question: str,
) -> dict | None:
    """Stage 7.3: Detect if the current agent's response suggests a handoff.

    Returns a structured handoff recommendation or None.
    """
    response_lower = response.lower()
    question_lower = question.lower()
    adjacent = _HANDOFF_MAP.get(agent_id, [])

    for target_agent in adjacent:
        signals = _HANDOFF_SIGNALS.get(target_agent, [])
        matching_signals = [s for s in signals if s in response_lower or s in question_lower]

        if len(matching_signals) >= 2:
            target_config = AGENT_ROSTER.get(target_agent, {})
            return {
                "from": agent_id,
                "to": target_agent,
                "to_name": target_config.get("name", target_agent),
                "context": f"Based on discussion about: {', '.join(matching_signals[:3])}",
                "acceptance_criteria": _build_acceptance_criteria(target_agent, matching_signals),
                "signals": matching_signals,
            }

    return None


def _build_acceptance_criteria(target_agent: str, signals: list[str]) -> list[str]:
    """Build acceptance criteria for a handoff based on target agent capabilities."""
    criteria_map = {
        "legal": ["Identify specific regulation/law", "Quantify penalty range", "Provide compliance timeline"],
        "compliance": ["Map to framework section", "Identify disclosure gap", "Suggest remediation steps"],
        "executive": ["Summarize in SCQA format", "Lead with financial impact", "Provide 3 recommendations"],
        "analytics": ["Quantify with specific metrics", "Include trend analysis", "Benchmark against peers"],
        "supply_chain": ["Map supplier risk tiers", "Quantify Scope 3 impact", "Identify geographic exposure"],
        "trend": ["Identify 3+ signals", "Provide counter-signals", "Suggest monitoring cadence"],
        "stakeholder": ["Map stakeholder positions", "Identify perception gaps", "Track rating changes"],
        "opportunity": ["Estimate ROI range", "List risk factors", "Categorize as quick-win or strategic"],
        "content": ["Draft in framework format", "Include data tables", "Match audience tone"],
    }
    return criteria_map.get(target_agent, ["Provide detailed analysis"])[:3]


# --- Stage 7.6: NEXUS-Lite Multi-Agent Pipelines ---

# Pipeline definitions: ordered list of agents for complex queries
NEXUS_PIPELINES: dict[str, dict[str, Any]] = {
    "board_briefing": {
        "description": "Prepare a board briefing on ESG risk",
        "triggers": ["board briefing", "board presentation", "cxo briefing", "prepare a briefing"],
        "agents": ["supply_chain", "analytics", "executive"],
        "final_agent": "executive",
    },
    "compliance_review": {
        "description": "Full compliance review with legal assessment",
        "triggers": ["full compliance review", "compliance audit", "regulatory review", "gap analysis with legal"],
        "agents": ["compliance", "legal", "content"],
        "final_agent": "content",
    },
    "investment_case": {
        "description": "Build ESG investment case",
        "triggers": ["investment case", "green investment", "esg business case", "sustainability roi"],
        "agents": ["opportunity", "analytics", "executive"],
        "final_agent": "executive",
    },
    "risk_assessment": {
        "description": "Comprehensive ESG risk assessment",
        "triggers": ["comprehensive risk", "full risk assessment", "risk report", "enterprise risk"],
        "agents": ["supply_chain", "trend", "analytics"],
        "final_agent": "analytics",
    },
}


def detect_nexus_pipeline(question: str) -> str | None:
    """Stage 7.6: Check if a question triggers a multi-agent NEXUS pipeline."""
    question_lower = question.lower()
    for pipeline_id, config in NEXUS_PIPELINES.items():
        if any(trigger in question_lower for trigger in config["triggers"]):
            return pipeline_id
    return None


async def run_nexus_pipeline(
    tenant_id: str,
    user_id: str,
    question: str,
    pipeline_id: str,
    db=None,
) -> dict:
    """Stage 7.6: Execute a multi-agent NEXUS-Lite pipeline.

    Each agent's output feeds the next agent as context.
    The final agent produces the user-facing response.
    """
    from backend.agent.memory import memory_manager

    pipeline = NEXUS_PIPELINES[pipeline_id]
    agent_sequence = pipeline["agents"]
    final_agent = pipeline["final_agent"]

    logger.info(
        "nexus_pipeline_start",
        pipeline=pipeline_id,
        agents=agent_sequence,
        tenant_id=tenant_id,
    )

    accumulated_context: list[dict[str, str]] = []
    all_tools_used: list[str] = []

    for i, agent_id in enumerate(agent_sequence):
        agent_config = AGENT_ROSTER[agent_id]
        personality = load_personality(agent_config["personality_key"])

        # Build the prompt for this agent in the pipeline
        if i == 0:
            agent_question = question
        else:
            # Feed previous agents' output as context
            prior_context = "\n\n".join(
                f"--- {ctx['agent_name']} Analysis ---\n{ctx['response']}"
                for ctx in accumulated_context
            )
            if agent_id == final_agent:
                agent_question = (
                    f"Based on the following specialist analyses, {question}\n\n"
                    f"{prior_context}\n\n"
                    f"Synthesize these into your final deliverable."
                )
            else:
                agent_question = (
                    f"{question}\n\n"
                    f"Previous analysis:\n{prior_context}\n\n"
                    f"Add your specialist perspective to build on this analysis."
                )

        # Gather tools for this agent
        tool_context = await _gather_tool_context(
            tenant_id=tenant_id,
            question=agent_question,
            tools=agent_config["tools"],
            db=db,
        )
        all_tools_used.extend(tool_context.keys())

        # Get memory context
        memory_context = await memory_manager.get_context_summary(tenant_id, user_id)

        # Generate this agent's response
        response = await _generate_response(
            personality=personality,
            question=agent_question,
            tool_context=tool_context,
            memory_context=memory_context,
            recent_messages=[],
            tenant_id=tenant_id,
        )

        accumulated_context.append({
            "agent_id": agent_id,
            "agent_name": agent_config["name"],
            "response": response,
        })

        logger.info(
            "nexus_pipeline_step",
            pipeline=pipeline_id,
            step=i + 1,
            agent=agent_id,
            response_len=len(response),
        )

    # Store the full pipeline interaction in memory
    await memory_manager.add_message(
        tenant_id, user_id, "user", question,
        metadata={"agent": "nexus", "pipeline": pipeline_id},
    )

    final_response = accumulated_context[-1]["response"] if accumulated_context else "Pipeline produced no output."

    # Add pipeline provenance to response
    agent_names = [AGENT_ROSTER[a]["name"] for a in agent_sequence]
    provenance = f"\n\n---\n*Pipeline: {' → '.join(agent_names)}*"

    await memory_manager.add_message(
        tenant_id, user_id, "assistant", final_response,
        metadata={
            "agent": final_agent,
            "pipeline": pipeline_id,
            "pipeline_agents": agent_sequence,
        },
    )

    return {
        "response": final_response + provenance,
        "agent": {
            "id": final_agent,
            "name": AGENT_ROSTER[final_agent]["name"],
        },
        "classification": {
            "agent": final_agent,
            "method": "nexus_pipeline",
            "pipeline": pipeline_id,
            "pipeline_agents": agent_sequence,
        },
        "tools_used": list(set(all_tools_used)),
        "pipeline": {
            "id": pipeline_id,
            "agents": [
                {"id": ctx["agent_id"], "name": ctx["agent_name"], "response_preview": ctx["response"][:200]}
                for ctx in accumulated_context
            ],
        },
    }
