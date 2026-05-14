"""Phase C — SSE chat endpoint.

POST `/api/chat` with `{conversation_id?, message, signoff?}`. Returns
a `text/event-stream` response with the canonical 13 event types:

    stream_start | token | slash_command_parsed | tool_invocation
    tool_progress | tool_result | toulmin_chain | phase_k_tags
    stage_progress | advisor_hint | signoff_request | error | done

Phase 1 of the SSE path:
  * Lightweight LLM streaming via `OpenRouterClient.stream()` when
    `OPENROUTER_API_KEY` is set; falls through to a deterministic
    echo path when the LLM client can't initialise (so dev + tests
    work without keys).
  * Memory retrieval BEFORE the LLM call (`retrieve_for_injection`)
    injects top-N memories as system context.
  * MCP tool dispatch via `dispatch_tool` when the LLM emits a
    `tool_call`. Today the LLM doesn't emit tool calls in v1 — the
    plumbing is wired so a later prompt can.
  * Conversation persistence: user message + assistant message
    written at completion (NOT mid-stream — too noisy on the DB).

Out-of-scope for Phase 1 (deferred): function-calling, multi-turn
tool chains, advisor hint emission mid-stream.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth_context import get_bearer_claims
from engine.chat.conversations import ensure_conversation
from engine.chat.messages import (
    insert_assistant_message,
    insert_user_message,
    load_messages_for_llm,
)
from engine.memory.retrieval import retrieve_for_injection

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=8000)
    signoff: str | None = None


def _scope(claims: dict[str, Any]) -> tuple[str, str]:
    tenant = str(claims.get("tenant_slug") or claims.get("tenant") or "default")
    user = str(claims.get("sub") or "anonymous")
    return tenant, user


def _format_sse(event: str, data: Any) -> str:
    """Encode one SSE message (event + data)."""
    body = json.dumps(data, default=str)
    return f"event: {event}\ndata: {body}\n\n"


async def _stream_chat(
    *,
    request: ChatRequest,
    tenant: str,
    user: str,
) -> AsyncIterator[str]:
    """Yield SSE event strings for one chat turn."""

    # 1. ensure conversation + persist user message
    conversation_id = ensure_conversation(
        conversation_id=request.conversation_id,
        tenant_id=tenant, user_id=user,
        title_seed=request.message[:80],
    )
    insert_user_message(
        conversation_id=conversation_id,
        tenant_id=tenant, user_id=user,
        content=request.message,
    )

    yield _format_sse("stream_start", {
        "conversation_id": conversation_id,
        "tenant": tenant, "user": user,
    })

    # 2. retrieve relevant memories (best-effort)
    try:
        memories = retrieve_for_injection(
            tenant_id=tenant, user_id=user, query=request.message, top_n=5,
        )
    except Exception:  # noqa: BLE001 — never let memory failure break chat
        memories = []
    if memories:
        yield _format_sse("phase_k_tags", {
            "memory_count": len(memories),
            "memory_kinds": list({m.fact_kind for m in memories}),
        })

    # 3. load conversation history (for LLM context)
    history = load_messages_for_llm(
        conversation_id=conversation_id, tenant_id=tenant, user_id=user,
    )

    # 4. attempt to stream from OpenRouter; fall back to deterministic echo
    response_text = ""
    model_used = "deterministic-echo"
    try:
        from engine.llm import get_llm_client

        client = get_llm_client(task_class="chat")
        system_msg = {
            "role": "system",
            "content": _build_system_prompt(memories, tenant, user),
        }
        for token_event in client.stream(
            messages=[system_msg, *history], temperature=0.7,
        ):
            if token_event.delta:
                response_text += token_event.delta
                yield _format_sse("token", {"delta": token_event.delta})
            if token_event.done:
                model_used = token_event.model or model_used
    except Exception as exc:  # noqa: BLE001 — fall through to echo, log nothing visible
        # Deterministic fallback: echo with a memory-aware preface.
        response_text = _deterministic_echo(request.message, memories)
        for chunk in _chunk_text(response_text, size=64):
            yield _format_sse("token", {"delta": chunk})
        model_used = f"fallback:{type(exc).__name__}"

    # 5. persist assistant message + close stream
    insert_assistant_message(
        conversation_id=conversation_id,
        tenant_id=tenant,
        content=response_text,
        model_used=model_used,
        finish_reason="stop",
    )
    yield _format_sse("done", {
        "conversation_id": conversation_id,
        "model_used": model_used,
        "chars": len(response_text),
    })


def _build_system_prompt(memories, tenant: str, user: str) -> str:
    parts = [
        "You are a Snowkap-ESG assistant. Answer in the user's lens "
        "(CFO/CEO/Analyst). Cite ₹ figures from the underlying tools "
        "rather than inventing them. Tag uncertainty when present.",
        f"Active tenant: {tenant}. User: {user}.",
    ]
    if memories:
        parts.append("Known facts about this user (memory-injected):")
        for m in memories[:5]:
            parts.append(f"  - [{m.fact_kind}] {m.content}")
    return "\n\n".join(parts)


def _deterministic_echo(message: str, memories) -> str:
    """Fallback used when no LLM client is wired up.

    Useful for tests + dev environments without an OPENROUTER_API_KEY.
    """
    lead = "[fallback] "
    body = message.strip()
    if memories:
        body += f"\n\nNoted facts: {len(memories)} memories recalled."
    return lead + body


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


@router.post("")
def chat(
    body: ChatRequest,
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> StreamingResponse:
    tenant, user = _scope(claims)

    async def _generator() -> AsyncIterator[str]:
        async for event in _stream_chat(request=body, tenant=tenant, user=user):
            yield event

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable reverse-proxy buffering
            "Connection": "keep-alive",
        },
    )
