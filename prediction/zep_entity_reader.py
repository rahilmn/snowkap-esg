"""Zep entity reader — reads company entities from shared Zep memory.

Per MASTER_BUILD_PLAN Phase 4:
- Zep Cloud as shared agent memory
- Reads company context for MiroFish simulation agents
- Provides persistent memory across simulation sessions
"""

import structlog

from prediction.config import mirofish_settings

logger = structlog.get_logger()


class ZepEntityReader:
    """Reads and manages entities in Zep Cloud memory for simulation agents."""

    def __init__(self) -> None:
        self.api_key = mirofish_settings.ZEP_API_KEY
        self._client = None

    async def _get_client(self):
        """Lazy initialization of Zep client."""
        if self._client is None and self.api_key:
            try:
                from zep_cloud.client import AsyncZep
                self._client = AsyncZep(api_key=self.api_key)
            except ImportError:
                logger.warning("zep_cloud_not_installed")
            except Exception as e:
                logger.error("zep_init_failed", error=str(e))
        return self._client

    async def get_company_memory(self, company_id: str, tenant_id: str) -> dict | None:
        """Retrieve persisted company knowledge from Zep memory.

        Stores accumulated ESG intelligence about a company across simulations.
        """
        client = await self._get_client()
        if not client:
            return None

        session_id = f"company_{tenant_id}_{company_id}"
        try:
            memory = await client.memory.get(session_id)
            if memory and memory.context:
                return {
                    "session_id": session_id,
                    "context": memory.context,
                    "facts": [f.fact for f in memory.relevant_facts] if memory.relevant_facts else [],
                }
            return None
        except Exception as e:
            logger.debug("zep_memory_not_found", session_id=session_id, error=str(e))
            return None

    async def store_simulation_context(
        self,
        company_id: str,
        tenant_id: str,
        simulation_summary: str,
        key_findings: list[str],
    ) -> bool:
        """Store simulation results in Zep memory for future simulations.

        This creates persistent memory that enriches future predictions.
        """
        client = await self._get_client()
        if not client:
            return False

        session_id = f"company_{tenant_id}_{company_id}"
        try:
            from zep_cloud.types import Message
            messages = [
                Message(
                    role="assistant",
                    role_type="assistant",
                    content=f"Simulation summary: {simulation_summary}",
                ),
            ]
            for finding in key_findings[:5]:
                messages.append(
                    Message(
                        role="assistant",
                        role_type="assistant",
                        content=f"Key finding: {finding}",
                    ),
                )

            await client.memory.add(session_id, messages=messages)
            logger.info("zep_context_stored", session_id=session_id)
            return True
        except Exception as e:
            logger.error("zep_store_failed", session_id=session_id, error=str(e))
            return False

    async def get_agent_memory(self, agent_id: str, simulation_id: str) -> dict | None:
        """Get an individual agent's memory from a simulation."""
        client = await self._get_client()
        if not client:
            return None

        session_id = f"agent_{simulation_id}_{agent_id}"
        try:
            memory = await client.memory.get(session_id)
            return {"context": memory.context} if memory else None
        except Exception:
            return None

    async def store_agent_memory(
        self,
        agent_id: str,
        simulation_id: str,
        analysis: str,
        recommendation: str,
    ) -> bool:
        """Store an agent's analysis in Zep for cross-round memory."""
        client = await self._get_client()
        if not client:
            return False

        session_id = f"agent_{simulation_id}_{agent_id}"
        try:
            from zep_cloud.types import Message
            await client.memory.add(
                session_id,
                messages=[
                    Message(role="assistant", role_type="assistant", content=f"Analysis: {analysis}"),
                    Message(role="assistant", role_type="assistant", content=f"Recommendation: {recommendation}"),
                ],
            )
            return True
        except Exception as e:
            logger.debug("agent_memory_store_failed", agent_id=agent_id, error=str(e))
            return False


# Singleton
zep_reader = ZepEntityReader()
