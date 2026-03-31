"""Agent router — Chat API for ESG specialist agents.

Per MASTER_BUILD_PLAN Phase 5 + Phase 11:
- Agent routing: Claude classifies user intent → selects specialist
- Agent selection visible in chat response
- Conversation threads with Zep memory
- UserAgentContext with auth-parity (Three Parity Rules)
- Confirmation-gated writes
- "Ask about this news" → agent explains causal chain + triggers prediction
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from backend.agent.context import UserAgentContext
from backend.core.dependencies import TenantContext, get_tenant_context
from backend.core.permissions import Permission, require_permission

logger = structlog.get_logger()
router = APIRouter()


# --- Schemas ---

class ChatRequest(BaseModel):
    question: str
    agent_id: str | None = None  # Optional: force a specific agent
    conversation_id: str | None = None  # Optional: thread ID
    article_id: str | None = None  # Optional: article context for ontology-driven analysis


class ChatResponse(BaseModel):
    response: str
    agent: dict  # {"id": "supply_chain", "name": "ESG Supply Chain Analyst"}
    classification: dict
    tools_used: list[str]
    conversation_id: str | None = None
    pending_actions: list[dict] | None = None  # Actions needing confirmation
    handoff_suggestion: dict | None = None  # Stage 7.3: Agent-to-agent handoff
    pipeline: dict | None = None  # Stage 7.6: NEXUS pipeline info


class AgentInfo(BaseModel):
    id: str
    name: str
    keywords: list[str]
    tools: list[str]


class ConversationHistoryResponse(BaseModel):
    messages: list[dict]
    context_summary: str | None = None


class ConfirmActionRequest(BaseModel):
    action_id: str
    conversation_id: str


class AskAboutNewsRequest(BaseModel):
    article_id: str
    question: str | None = None  # Optional follow-up question


class AskAboutNewsResponse(BaseModel):
    response: str
    agent: dict
    causal_chains: list[dict]
    prediction_available: bool
    article_summary: dict


# --- Helpers ---

def _build_agent_context(ctx: TenantContext, conversation_id: str | None = None) -> UserAgentContext:
    """Build a UserAgentContext from TenantContext."""
    return UserAgentContext(
        tenant_id=ctx.tenant_id,
        user=ctx.user,
        conversation_id=conversation_id or f"conv_{ctx.user.user_id}",
        db=ctx.db,
    )


# --- Endpoints ---

@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    req: ChatRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ChatResponse:
    """Chat with an ESG specialist agent.

    The system automatically selects the best specialist based on your question,
    or you can force a specific agent by providing agent_id.

    Per Phase 11: Auth-parity — agent sees only what the user can see.
    """
    if not req.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty",
        )

    agent_ctx = _build_agent_context(ctx, req.conversation_id)

    try:
        from backend.agent.graph import run_agent_pipeline

        result = await run_agent_pipeline(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.user_id,
            question=req.question,
            agent_id=req.agent_id,
            db=ctx.db,
            article_id=req.article_id,
            designation=ctx.user.designation,
        )
    except Exception as e:
        logger.error(
            "agent_chat_error",
            error=str(e),
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.user_id,
            question=req.question[:100],
        )
        # Return a graceful error response instead of 500
        return ChatResponse(
            response=f"I'm sorry, I encountered an issue processing your request. Please try again. (Error: {str(e)[:100]})",
            agent={"id": "analytics", "name": "ESG Analytics Agent"},
            classification={"error": True},
            tools_used=[],
            conversation_id=req.conversation_id,
        )

    logger.info(
        "agent_chat_completed",
        agent=result.get("agent", {}).get("id"),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.user_id,
    )

    # Emit real-time update via Socket.IO
    try:
        from backend.core.socketio import emit_to_tenant
        await emit_to_tenant(ctx.tenant_id, "agent_response", {
            "user_id": ctx.user.user_id,
            "agent": result.get("agent", {}),
            "response_preview": result.get("response", "")[:200],
            "conversation_id": req.conversation_id,
        })
    except Exception as exc:
        logger.debug("socketio_emit_failed", emit_event="agent_response", error=str(exc))

    return ChatResponse(
        response=result.get("response", ""),
        agent=result.get("agent", {"id": "analytics", "name": "ESG Analytics Agent"}),
        classification=result.get("classification", {}),
        tools_used=result.get("tools_used", []),
        conversation_id=req.conversation_id,
        pending_actions=agent_ctx.pending_actions if agent_ctx.pending_actions else None,
        handoff_suggestion=result.get("handoff_suggestion"),
        pipeline=result.get("pipeline"),
    )


@router.post("/ask-about-news", response_model=AskAboutNewsResponse)
async def ask_about_news(
    req: AskAboutNewsRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AskAboutNewsResponse:
    """Ask the agent to analyze a specific news article.

    Per Phase 11: "Ask about this news" → agent explains causal chain + triggers prediction.
    The agent loads the article, finds causal chains, and provides analysis.
    """
    from backend.models.news import Article, ArticleScore, CausalChain

    # Load article (tenant-scoped)
    result = await ctx.db.execute(
        select(Article).where(
            Article.id == req.article_id,
            Article.tenant_id == ctx.tenant_id,
        )
    )
    article = result.scalar_one_or_none()
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article not found",
        )

    # Load causal chains for this article
    chains_result = await ctx.db.execute(
        select(CausalChain).where(
            CausalChain.article_id == req.article_id,
            CausalChain.tenant_id == ctx.tenant_id,
        )
    )
    causal_chains = chains_result.scalars().all()

    # Load article scores
    scores_result = await ctx.db.execute(
        select(ArticleScore).where(
            ArticleScore.article_id == req.article_id,
            ArticleScore.tenant_id == ctx.tenant_id,
        )
    )
    scores = scores_result.scalars().all()

    # Check if prediction exists for this article
    prediction_available = False
    try:
        from backend.models.prediction import PredictionReport
        pred_result = await ctx.db.execute(
            select(PredictionReport.id).where(
                PredictionReport.article_id == req.article_id,
                PredictionReport.tenant_id == ctx.tenant_id,
            ).limit(1)
        )
        prediction_available = pred_result.scalar_one_or_none() is not None
    except Exception as exc:
        logger.debug("prediction_lookup_failed", article_id=req.article_id, error=str(exc))

    # Build article summary
    article_summary = {
        "id": article.id,
        "title": getattr(article, "title", ""),
        "source": getattr(article, "source", ""),
        "published_at": str(getattr(article, "published_at", "")),
        "sentiment": getattr(article, "sentiment", None),
        "entities": getattr(article, "entities", []),
    }

    # Build causal chain data
    chain_data = []
    for chain in causal_chains:
        path = chain.chain_path or []
        chain_data.append({
            "id": chain.id,
            "source_entity": path[0] if path else "",
            "target_entity": path[-1] if path else "",
            "relationship_type": chain.relationship_type or "",
            "hops": chain.hops or 0,
            "impact_score": chain.impact_score or 0.0,
            "explanation": chain.explanation or "",
        })

    # Build the question for the agent
    question = req.question or f"Analyze this news article and explain its ESG impact on our company"
    enriched_question = (
        f"{question}\n\n"
        f"--- Article Context ---\n"
        f"Title: {article_summary['title']}\n"
        f"Source: {article_summary['source']}\n"
        f"Entities: {article_summary.get('entities', [])}\n"
        f"Sentiment: {article_summary.get('sentiment', 'unknown')}\n"
        f"Causal Chains Found: {len(chain_data)}\n"
    )

    if chain_data:
        enriched_question += "\nCausal Chain Paths:\n"
        for chain in chain_data[:5]:
            enriched_question += (
                f"  - {chain['source_entity']} →({chain['relationship_type']})→ "
                f"{chain['target_entity']} (hops: {chain['hops']}, "
                f"impact: {chain['impact_score']:.2f})\n"
            )

    if scores:
        enriched_question += "\nImpact Scores:\n"
        for score in scores[:5]:
            enriched_question += (
                f"  - Company: {getattr(score, 'company_id', 'N/A')}, "
                f"Score: {getattr(score, 'score', 0)}\n"
            )

    # Run agent pipeline with enriched context
    from backend.agent.graph import run_agent_pipeline

    result = await run_agent_pipeline(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.user_id,
        question=enriched_question,
        db=ctx.db,
        designation=ctx.user.designation,
    )

    logger.info(
        "agent_news_analysis",
        article_id=req.article_id,
        agent=result.get("agent", {}).get("id"),
        causal_chains=len(chain_data),
        tenant_id=ctx.tenant_id,
    )

    return AskAboutNewsResponse(
        response=result.get("response", ""),
        agent=result.get("agent", {"id": "analytics", "name": "ESG Analytics Agent"}),
        causal_chains=chain_data,
        prediction_available=prediction_available,
        article_summary=article_summary,
    )


@router.post("/confirm-action")
async def confirm_action(
    req: ConfirmActionRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Confirm and execute a pending agent action.

    Per Phase 11: Confirmation-gated writes. The agent proposes actions,
    the user confirms before execution.
    """
    agent_ctx = _build_agent_context(ctx, req.conversation_id)

    # For stateless HTTP, we need to look up the pending action from the session
    # In production this would be stored in Redis or DB
    # For now, return a clear message
    result = await agent_ctx.execute_confirmed_action(req.action_id)

    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )

    logger.info(
        "agent_action_confirmed",
        action_id=req.action_id,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.user_id,
    )

    # Emit real-time update
    try:
        from backend.core.socketio import emit_to_tenant
        await emit_to_tenant(ctx.tenant_id, "agent_action_executed", {
            "action_id": req.action_id,
            "user_id": ctx.user.user_id,
            "result": result,
        })
    except Exception as exc:
        logger.debug("socketio_emit_failed", emit_event="agent_action_executed", error=str(exc))

    return {"status": "executed", "result": result}


@router.post("/reject-action")
async def reject_action(
    req: ConfirmActionRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Reject a pending agent action."""
    agent_ctx = _build_agent_context(ctx, req.conversation_id)

    result = agent_ctx.reject_action(req.action_id)
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["error"],
        )

    logger.info(
        "agent_action_rejected",
        action_id=req.action_id,
        tenant_id=ctx.tenant_id,
    )

    return result


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AgentInfo]:
    """List all available ESG specialist agents."""
    from backend.services.agent_service import AGENT_ROSTER

    return [
        AgentInfo(
            id=agent_id,
            name=config["name"],
            keywords=config["keywords"][:5],
            tools=config["tools"],
        )
        for agent_id, config in AGENT_ROSTER.items()
    ]


@router.get("/history", response_model=ConversationHistoryResponse)
async def get_conversation_history(
    last_n: int = 20,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ConversationHistoryResponse:
    """Get conversation history for the current user."""
    from backend.agent.memory import memory_manager

    messages = await memory_manager.get_memory(
        ctx.tenant_id, ctx.user.user_id, last_n=last_n,
    )
    summary = await memory_manager.get_context_summary(
        ctx.tenant_id, ctx.user.user_id,
    )

    return ConversationHistoryResponse(
        messages=messages,
        context_summary=summary,
    )


@router.delete("/history")
async def clear_conversation_history(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Clear conversation history for the current user."""
    from backend.agent.memory import memory_manager

    await memory_manager.clear_session(ctx.tenant_id, ctx.user.user_id)
    logger.info("agent_history_cleared", tenant_id=ctx.tenant_id, user_id=ctx.user.user_id)

    return {"status": "cleared", "message": "Conversation history cleared"}


@router.post("/search")
async def search_conversations(
    query: str,
    limit: int = 5,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Semantic search over conversation history."""
    from backend.agent.memory import memory_manager

    results = await memory_manager.search_memory(
        ctx.tenant_id, ctx.user.user_id, query, limit=limit,
    )

    return {"query": query, "results": results}


# --- Stage 7.3: Agent-to-Agent Handoff ---

class HandoffRequest(BaseModel):
    """Request to execute an agent handoff."""
    from_agent: str
    to_agent: str
    context: str
    question: str
    conversation_id: str | None = None


@router.post("/handoff", response_model=ChatResponse)
async def execute_handoff(
    req: HandoffRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ChatResponse:
    """Stage 7.3: Execute an agent-to-agent handoff.

    Transfers context from one specialist to another, preserving
    the handoff context and acceptance criteria.
    """
    from backend.services.agent_service import AGENT_ROSTER

    if req.to_agent not in AGENT_ROSTER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown target agent: {req.to_agent}",
        )

    # Build enriched question with handoff context
    enriched_question = (
        f"{req.question}\n\n"
        f"--- Handoff Context from {req.from_agent} ---\n"
        f"{req.context}"
    )

    from backend.agent.graph import run_agent_pipeline

    result = await run_agent_pipeline(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.user_id,
        question=enriched_question,
        agent_id=req.to_agent,
        db=ctx.db,
        designation=ctx.user.designation,
    )

    logger.info(
        "agent_handoff_executed",
        from_agent=req.from_agent,
        to_agent=req.to_agent,
        tenant_id=ctx.tenant_id,
    )

    return ChatResponse(
        response=result.get("response", ""),
        agent=result.get("agent", {"id": req.to_agent, "name": AGENT_ROSTER[req.to_agent]["name"]}),
        classification=result.get("classification", {}),
        tools_used=result.get("tools_used", []),
        conversation_id=req.conversation_id,
    )


# --- Stage 7.6: NEXUS Pipeline Info ---

@router.get("/pipelines")
async def list_pipelines(
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Stage 7.6: List available NEXUS-Lite multi-agent pipelines."""
    from backend.services.agent_service import NEXUS_PIPELINES, AGENT_ROSTER

    pipelines = []
    for pid, config in NEXUS_PIPELINES.items():
        agents = [
            {"id": a, "name": AGENT_ROSTER[a]["name"]}
            for a in config["agents"]
            if a in AGENT_ROSTER
        ]
        pipelines.append({
            "id": pid,
            "description": config["description"],
            "triggers": config["triggers"],
            "agents": agents,
        })

    return {"pipelines": pipelines}


# --- Stage 7.5: Escalation ---

class EscalationRequest(BaseModel):
    """Request to escalate a conversation."""
    action: str  # "switch_agent", "escalate_human", "raw_data"
    target_agent: str | None = None  # For switch_agent
    conversation_id: str | None = None


@router.post("/escalate")
async def handle_escalation(
    req: EscalationRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Stage 7.5: Handle escalation actions from low-confidence responses."""
    from backend.agent.memory import memory_manager

    if req.action == "switch_agent":
        if not req.target_agent:
            from backend.services.agent_service import AGENT_ROSTER
            available = [
                {"id": aid, "name": cfg["name"]}
                for aid, cfg in AGENT_ROSTER.items()
            ]
            return {"status": "choose_agent", "available_agents": available}

        return {
            "status": "switched",
            "message": f"Switched to {req.target_agent}. Please ask your question again.",
            "new_agent": req.target_agent,
        }

    elif req.action == "escalate_human":
        # In production, this would create a support ticket or notify a human analyst
        logger.info(
            "human_escalation_requested",
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.user_id,
            conversation_id=req.conversation_id,
        )
        return {
            "status": "escalated",
            "message": "Your question has been flagged for review by a human ESG analyst. You'll receive a response within 24 hours.",
        }

    elif req.action == "raw_data":
        # Return recent tool results from memory
        recent = await memory_manager.get_memory(
            ctx.tenant_id, ctx.user.user_id, last_n=5,
        )
        return {
            "status": "raw_data",
            "recent_context": recent,
            "message": "Here is the raw data from recent queries. You can interpret it directly.",
        }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown escalation action: {req.action}. Use: switch_agent, escalate_human, raw_data",
    )
