"""TenantMemoryManager — Zep Cloud memory for agent conversations.

Per MASTER_BUILD_PLAN Phase 5:
- Shared Zep Cloud memory with MiroFish agents
- Per-tenant, per-user conversation memory
- Agent context persistence across sessions
"""

from typing import Any

import structlog

from backend.core.config import settings

logger = structlog.get_logger()


class TenantMemoryManager:
    """Manages conversation memory per tenant+user via Zep Cloud.

    Falls back to in-memory storage when Zep is unavailable.
    """

    def __init__(self) -> None:
        self._zep_client = None
        self._fallback: dict[str, list[dict]] = {}

    async def _get_zep(self):
        """Lazy-init Zep client."""
        if self._zep_client is None and settings.ZEP_API_KEY:
            try:
                from zep_cloud.client import AsyncZep
                self._zep_client = AsyncZep(api_key=settings.ZEP_API_KEY)
            except Exception as e:
                logger.warning("zep_init_failed", error=str(e))
        return self._zep_client

    def _session_id(self, tenant_id: str, user_id: str) -> str:
        """Generate a unique session ID for tenant+user."""
        return f"snowkap:{tenant_id}:{user_id}"

    async def add_message(
        self,
        tenant_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store a conversation message."""
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                from zep_cloud.types import Message
                await zep.memory.add(
                    session_id=session_id,
                    messages=[Message(
                        role_type=role,
                        content=content,
                        metadata=metadata or {},
                    )],
                )
                return
            except Exception as e:
                logger.warning("zep_add_message_failed", error=str(e))

        # Fallback to in-memory
        if session_id not in self._fallback:
            self._fallback[session_id] = []
        self._fallback[session_id].append({
            "role": role,
            "content": content,
            "metadata": metadata or {},
        })
        # Keep only last 50 messages in fallback
        self._fallback[session_id] = self._fallback[session_id][-50:]

    async def get_memory(
        self,
        tenant_id: str,
        user_id: str,
        last_n: int = 10,
    ) -> list[dict]:
        """Retrieve recent conversation memory."""
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                memory = await zep.memory.get(session_id=session_id)
                messages = []
                for msg in (memory.messages or [])[-last_n:]:
                    messages.append({
                        "role": msg.role_type,
                        "content": msg.content,
                        "metadata": msg.metadata or {},
                    })
                return messages
            except Exception as e:
                logger.warning("zep_get_memory_failed", error=str(e))

        # Fallback
        return self._fallback.get(session_id, [])[-last_n:]

    async def get_context_summary(
        self,
        tenant_id: str,
        user_id: str,
    ) -> str | None:
        """Get Zep's auto-generated context summary for a session."""
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                memory = await zep.memory.get(session_id=session_id)
                if memory.context:
                    return memory.context
            except Exception as e:
                logger.warning("zep_get_context_failed", error=str(e))

        return None

    async def search_memory(
        self,
        tenant_id: str,
        user_id: str,
        query: str,
        limit: int = 5,
    ) -> list[dict]:
        """Semantic search over conversation history."""
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                results = await zep.memory.search(
                    session_id=session_id,
                    text=query,
                    limit=limit,
                )
                return [
                    {
                        "content": r.message.content if r.message else "",
                        "score": r.score,
                        "metadata": r.message.metadata if r.message else {},
                    }
                    for r in (results or [])
                ]
            except Exception as e:
                logger.warning("zep_search_failed", error=str(e))

        # Fallback: simple keyword search
        session_msgs = self._fallback.get(session_id, [])
        query_lower = query.lower()
        matches = [m for m in session_msgs if query_lower in m.get("content", "").lower()]
        return matches[-limit:]

    async def clear_session(self, tenant_id: str, user_id: str) -> None:
        """Clear conversation memory for a session."""
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                await zep.memory.delete(session_id=session_id)
            except Exception as e:
                logger.warning("zep_clear_failed", error=str(e))

        self._fallback.pop(session_id, None)


# Singleton instance
memory_manager = TenantMemoryManager()
