"""Phase C — CompanyAgent 5-state lifecycle + AgentAction tests."""
from __future__ import annotations

import pytest

from engine.governance.company_agent import (
    STATE_DISPATCHING,
    STATE_INITIALIZING,
    STATE_RECOMMENDING,
    STATE_RESOLVING,
    STATE_WATCHING,
    AgentAction,
    CompanyAgent,
    InvalidTransition,
    ToulminMissing,
)


def test_initial_state_is_initializing(tmp_path):
    a = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    assert a.state == STATE_INITIALIZING


def test_transition_initializing_to_watching(tmp_path):
    a = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    action = a.transition_to(
        STATE_WATCHING, actor="company_agent", reason="bootstrap complete",
    )
    assert a.state == STATE_WATCHING
    assert action.action_type == "transition"
    assert action.payload["from"] == STATE_INITIALIZING
    assert action.payload["to"] == STATE_WATCHING


def test_illegal_transition_raises(tmp_path):
    """Cannot skip from Initializing → Recommending."""
    a = CompanyAgent(tenant="x", audit_dir=tmp_path, auto_persist=False)
    with pytest.raises(InvalidTransition, match="illegal transition"):
        a.transition_to(STATE_RECOMMENDING, actor="x", reason="y")


def test_full_lifecycle_happy_path(tmp_path):
    a = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    a.transition_to(STATE_WATCHING, actor="company_agent", reason="bootstrap")
    a.transition_to(STATE_RECOMMENDING, actor="company_agent", reason="signal detected")
    a.transition_to(STATE_DISPATCHING, actor="company_agent", reason="user approved")
    a.transition_to(STATE_WATCHING, actor="company_agent", reason="dispatched")
    a.transition_to(STATE_RESOLVING, actor="manual:alice@snowkap.com", reason="incident escalation")
    assert a.state == STATE_RESOLVING
    assert len(a.actions) == 5
    assert all(act.action_type == "transition" for act in a.actions)


def test_unknown_state_raises(tmp_path):
    a = CompanyAgent(tenant="x", audit_dir=tmp_path, auto_persist=False)
    with pytest.raises(InvalidTransition, match="unknown state"):
        a.transition_to("Galactic", actor="x", reason="y")


def test_agent_action_requires_toulmin():
    with pytest.raises(ToulminMissing):
        AgentAction(
            agent_id="x", action_type="dispatch",
            payload={"x": 1},
            toulmin_chain={"claim": "x"},  # missing grounds, warrant
        )


def test_record_action_logs_dispatch(tmp_path):
    a = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    action = a.record_action(
        action_type="dispatch",
        payload={"recipient": "user", "content": "alert"},
        toulmin={
            "claim": "alert user about water risk",
            "grounds": ["article 14"],
            "warrant": "criticality score > 0.8",
        },
        actor="company_agent",
    )
    assert action.action_type == "dispatch"
    assert len(a.actions) == 1


def test_record_action_rejects_transition_type(tmp_path):
    a = CompanyAgent(tenant="x", audit_dir=tmp_path, auto_persist=False)
    with pytest.raises(ValueError, match="transition_to"):
        a.record_action(
            action_type="transition", payload={},
            toulmin={"claim": "x", "grounds": ["y"], "warrant": "z"},
            actor="x",
        )


def test_state_persists_across_dump_and_load(tmp_path):
    """Phase C — dump_to_disk + load_from_disk roundtrip the lifecycle state."""
    a = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    a.transition_to(STATE_WATCHING, actor="company_agent", reason="bootstrap")
    a.transition_to(STATE_RECOMMENDING, actor="company_agent", reason="signal")
    path = a.dump_to_disk()
    assert path.exists()

    rehydrated = CompanyAgent.load_from_disk(tenant="adani-power", audit_dir=tmp_path)
    assert rehydrated.state == STATE_RECOMMENDING
    assert rehydrated.last_transition_at is not None
    assert rehydrated.lifecycle_started_at == a.lifecycle_started_at


def test_load_from_disk_defaults_to_initializing_for_legacy_snapshot(tmp_path):
    """Old snapshots without a state field load cleanly as Initializing."""
    import json
    base = tmp_path / "agents" / "legacy-tenant"
    base.mkdir(parents=True)
    (base / "beliefs.json").write_text(
        json.dumps({"tenant": "legacy-tenant", "beliefs": {}}),
        encoding="utf-8",
    )
    rehydrated = CompanyAgent.load_from_disk(
        tenant="legacy-tenant", audit_dir=tmp_path,
    )
    assert rehydrated.state == STATE_INITIALIZING


def test_load_from_disk_ignores_invalid_state_value(tmp_path):
    """Malformed state values fall back to Initializing rather than raising."""
    import json
    base = tmp_path / "agents" / "bad-tenant"
    base.mkdir(parents=True)
    (base / "beliefs.json").write_text(
        json.dumps({
            "tenant": "bad-tenant", "beliefs": {},
            "state": "StageGalactic",  # invalid
        }),
        encoding="utf-8",
    )
    rehydrated = CompanyAgent.load_from_disk(
        tenant="bad-tenant", audit_dir=tmp_path,
    )
    assert rehydrated.state == STATE_INITIALIZING
