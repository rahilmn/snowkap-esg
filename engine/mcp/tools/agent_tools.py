"""MCP adapters for agent-beliefs-get / agent-state-get.

Read-only views of the L7 CompanyAgent's persistent state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.governance.company_agent import CompanyAgent


def handle_agent_beliefs_get(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    tenant = payload["tenant"]
    agent = CompanyAgent.load_from_disk(tenant=tenant, audit_dir=base_data)
    return {
        "tenant": tenant,
        "beliefs": [
            {
                "name": b.name,
                "value": b.value,
                "confidence": b.confidence,
                "rationale": b.rationale,
                "actor": b.actor,
                "updated_at": b.updated_at,
            }
            for b in agent.beliefs.values()
        ],
    }


def handle_agent_state_get(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Return the 5-state lifecycle value + recent transitions.

    `load_from_disk` only rehydrates beliefs in the current scaffold, so
    a freshly-loaded agent reports `StageInitializing`. This is the
    intended behaviour for now — durable state persistence is a deferred
    follow-up (see CHANGES_L2_HANDOFF.md).
    """
    tenant = payload["tenant"]
    agent = CompanyAgent.load_from_disk(tenant=tenant, audit_dir=base_data)
    recent_actions = [
        {
            "action_type": a.action_type,
            "payload": a.payload,
            "created_at": a.created_at,
        }
        for a in (agent.actions[-10:] if agent.actions else [])
    ]
    return {
        "tenant": tenant,
        "state": agent.state,
        "lifecycle_started_at": agent.lifecycle_started_at,
        "last_transition_at": agent.last_transition_at,
        "recent_actions": recent_actions,
        "belief_count": len(agent.beliefs),
    }
