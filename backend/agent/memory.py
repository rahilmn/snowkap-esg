"""TenantMemoryManager — Zep Cloud memory for agent conversations.

Per MASTER_BUILD_PLAN Phase 5 + Stage 7.4:
- Shared Zep Cloud memory with MiroFish agents
- Per-tenant, per-user conversation memory
- Agent context persistence across sessions
- Stage 7.4: Memory tagging by agent_id + topic for cross-agent context
"""

from typing import Any

import structlog

from backend.core.config import settings

logger = structlog.get_logger()

# Topic extraction keywords mapped to canonical topics
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "supply_chain": ["supply chain", "supplier", "scope 3", "upstream", "downstream", "procurement"],
    "compliance": ["compliance", "brsr", "gri", "tcfd", "esrs", "disclosure", "gap analysis"],
    "risk": ["risk", "penalty", "exposure", "vulnerability", "threat"],
    "climate": ["climate", "emissions", "carbon", "ghg", "net zero", "decarbonization"],
    "financial": ["financial", "revenue", "cost", "roi", "investment", "market cap"],
    "regulatory": ["regulation", "sebi", "cbam", "epa", "mandate", "law", "legal"],
    "stakeholder": ["stakeholder", "investor", "community", "employee", "rating"],
    "prediction": ["prediction", "forecast", "trend", "outlook", "future"],
    "opportunity": ["opportunity", "green revenue", "carbon credit", "growth"],
}


def extract_topics(text: str) -> list[str]:
    """Extract canonical topics from text content."""
    text_lower = text.lower()
    topics = []
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            topics.append(topic)
    return topics[:5]  # Cap at 5 topics


class TenantMemoryManager:
    """Manages conversation memory per tenant+user via Zep Cloud.

    Falls back to in-memory storage when Zep is unavailable.
    Stage 7.4: All messages tagged with agent_id + topics for cross-agent retrieval.
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
        """Store a conversation message with agent_id and topic tags (Stage 7.4)."""
        session_id = self._session_id(tenant_id, user_id)
        meta = metadata or {}

        # Stage 7.4: Auto-tag with topics extracted from content
        if "topics" not in meta:
            meta["topics"] = extract_topics(content)

        zep = await self._get_zep()

        if zep:
            try:
                from zep_cloud.types import Message
                await zep.memory.add(
                    session_id=session_id,
                    messages=[Message(
                        role_type=role,
                        content=content,
                        metadata=meta,
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
            "metadata": meta,
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

    async def get_agent_memory(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        last_n: int = 5,
    ) -> list[dict]:
        """Retrieve memory entries tagged with a specific agent_id (Stage 7.4).

        Used for cross-agent context: when routing to a new agent, retrieve
        entries from the previous agent's domain for continuity.
        """
        session_id = self._session_id(tenant_id, user_id)
        zep = await self._get_zep()

        if zep:
            try:
                memory = await zep.memory.get(session_id=session_id)
                filtered = [
                    {
                        "role": msg.role_type,
                        "content": msg.content,
                        "metadata": msg.metadata or {},
                    }
                    for msg in (memory.messages or [])
                    if (msg.metadata or {}).get("agent") == agent_id
                ]
                return filtered[-last_n:]
            except Exception as e:
                logger.warning("zep_get_agent_memory_failed", error=str(e))

        # Fallback
        all_msgs = self._fallback.get(session_id, [])
        filtered = [m for m in all_msgs if m.get("metadata", {}).get("agent") == agent_id]
        return filtered[-last_n:]

    async def get_topic_memory(
        self,
        tenant_id: str,
        user_id: str,
        topics: list[str],
        last_n: int = 5,
    ) -> list[dict]:
        """Retrieve memory entries matching any of the given topics (Stage 7.4).

        Used for cross-agent context sharing: e.g., executive agent retrieves
        supply_chain-tagged memory when preparing a board briefing.
        """
        session_id = self._session_id(tenant_id, user_id)
        topic_set = set(topics)

        zep = await self._get_zep()

        if zep:
            try:
                memory = await zep.memory.get(session_id=session_id)
                filtered = []
                for msg in (memory.messages or []):
                    msg_topics = set((msg.metadata or {}).get("topics", []))
                    if msg_topics & topic_set:
                        filtered.append({
                            "role": msg.role_type,
                            "content": msg.content,
                            "metadata": msg.metadata or {},
                        })
                return filtered[-last_n:]
            except Exception as e:
                logger.warning("zep_get_topic_memory_failed", error=str(e))

        # Fallback
        all_msgs = self._fallback.get(session_id, [])
        filtered = []
        for m in all_msgs:
            msg_topics = set(m.get("metadata", {}).get("topics", []))
            if msg_topics & topic_set:
                filtered.append(m)
        return filtered[-last_n:]

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
