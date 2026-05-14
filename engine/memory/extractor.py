"""Memory extraction — secondary LLM pass over a finished conversation.

Reads the conversation transcript, prompts an LLM to surface
persistent facts / preferences / decisions / open_threads, writes
each to `tenant_memory`. Source-conversation traceability preserved.

LLM call is wrapped in try/except so a failure never blocks the
conversation lifecycle. When OPENROUTER_API_KEY is unset, runs through
the same OpenAI-direct fallback as everything else.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from engine.chat.messages import load_conversation_history
from engine.memory.store import MemoryRecord, insert_memory

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You read a chat transcript and extract durable
memories that will help future conversations with the same user.

Output JSON with this exact shape:
{
  "memories": [
    {
      "fact_kind": "fact" | "preference" | "decision" | "open_thread",
      "scope": "personal" | "shared",
      "content": "1-sentence statement (max 200 chars)",
      "confidence": 0.0..1.0
    },
    ...
  ]
}

Rules:
- Only extract things that will matter NEXT time, not ephemeral
  exchanges.
- "fact" — durable claim about the world / company
- "preference" — user-stated preference about how they want answers
- "decision" — concrete decision the user made
- "open_thread" — unresolved question or todo
- "personal" scope: tied to this user's mental model / preferences
- "shared" scope: tied to the tenant's company state (everyone in the
  tenant should see it)
- If nothing is worth extracting, return {"memories": []}
- NEVER fabricate. Only extract things explicitly stated in the chat.
"""


def _build_user_prompt(history: list[dict[str, Any]]) -> str:
    """Render a transcript for the extractor LLM."""
    lines = ["CHAT TRANSCRIPT:\n"]
    for m in history:
        role = m.get("role", "user")
        content = (m.get("content") or "")[:1500]
        lines.append(f"[{role}] {content}\n")
    lines.append("\nExtract memories as JSON only.")
    return "".join(lines)


def extract_memories_from_conversation(
    *,
    conversation_id: str,
    tenant_id: str,
    user_id: str,
    max_memories: int = 10,
    client: Any = None,
) -> list[MemoryRecord]:
    """Read the conversation + extract memories via LLM.

    Returns the list of inserted MemoryRecord. On any LLM failure
    returns an empty list (best-effort, never raises).
    """
    msgs = load_conversation_history(
        conversation_id=conversation_id,
        tenant_id=tenant_id, user_id=user_id, limit=500,
    )
    if not msgs:
        return []
    history_dicts = [{"role": m.role, "content": m.content} for m in msgs]

    if client is None:
        try:
            from engine.llm.client import get_llm_client
            client = get_llm_client(task_class="extraction")
        except Exception as exc:
            logger.warning("memory.extractor: client init failed: %s", exc)
            return []

    try:
        response = client.complete(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(history_dicts)},
            ],
            temperature=0.1,
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        raw = response.text
    except Exception as exc:
        logger.warning("memory.extractor: LLM call failed: %s", exc)
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("memory.extractor: malformed JSON: %.200s", raw)
        return []

    items = parsed.get("memories") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return []

    out: list[MemoryRecord] = []
    for item in items[:max_memories]:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        fact_kind = item.get("fact_kind") or "fact"
        scope = item.get("scope") or "personal"
        try:
            confidence = float(item.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        try:
            rec = insert_memory(
                tenant_id=tenant_id,
                user_id=user_id if scope == "personal" else None,
                scope=scope, fact_kind=fact_kind, content=content,
                source_conversation_id=conversation_id,
                confidence=confidence,
            )
            out.append(rec)
        except (ValueError, Exception) as exc:  # noqa: BLE001
            logger.debug("memory.extractor: insert failed: %s", exc)
            continue
    return out
