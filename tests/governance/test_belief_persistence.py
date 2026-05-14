"""L7 — Belief persistence (dump/load round-trip) + read endpoint."""
from __future__ import annotations

import pytest

from engine.governance.belief_schema import RiskBandBelief
from engine.governance.company_agent import CompanyAgent


def test_dump_to_disk_writes_belief_snapshot(tmp_path):
    """L7 — `dump_to_disk` persists current state to JSON."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_risk", value="HIGH",
        confidence="moderate", rationale="r", actor="company_agent",
    )
    path = agent.dump_to_disk()
    assert path.exists()
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tenant"] == "adani-power"
    assert "climate_risk" in payload["beliefs"]
    assert payload["beliefs"]["climate_risk"]["value"] == "HIGH"


def test_load_from_disk_rehydrates_beliefs(tmp_path):
    """L7 — `load_from_disk` is the inverse of `dump_to_disk`."""
    src = CompanyAgent(tenant="x", audit_dir=tmp_path)
    src.update_belief(
        name="b1", value=42, confidence="low",
        rationale="r1", actor="company_agent",
    )
    src.update_belief(
        name="b2", value={"complex": "payload"}, confidence="moderate",
        rationale="r2", actor="company_agent",
    )
    src.dump_to_disk()
    # Fresh agent picks up the snapshot
    loaded = CompanyAgent.load_from_disk(tenant="x", audit_dir=tmp_path)
    assert set(loaded.beliefs.keys()) == {"b1", "b2"}
    assert loaded.beliefs["b1"].value == 42
    assert loaded.beliefs["b2"].value == {"complex": "payload"}


def test_load_from_disk_returns_empty_when_no_snapshot(tmp_path):
    """L7 — Missing snapshot file returns a fresh agent (not an error)."""
    agent = CompanyAgent.load_from_disk(tenant="never-onboarded", audit_dir=tmp_path)
    assert agent.beliefs == {}


def test_load_from_disk_tolerates_malformed_json(tmp_path):
    """L7 — Corrupt snapshot doesn't crash the read; returns empty state."""
    agent = CompanyAgent(tenant="corrupt", audit_dir=tmp_path)
    path = agent.beliefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = CompanyAgent.load_from_disk(tenant="corrupt", audit_dir=tmp_path)
    assert loaded.beliefs == {}


def test_update_belief_auto_persists_by_default(tmp_path):
    """L7 — `update_belief` writes to disk automatically.

    This is what makes the read endpoint useful: a worker that mutates
    state and a separate API process that reads it don't share memory,
    so the disk snapshot is the integration seam.
    """
    agent = CompanyAgent(tenant="auto-tenant", audit_dir=tmp_path)
    assert agent.auto_persist is True
    agent.update_belief(
        name="b1", value="v1", confidence="low",
        rationale="r", actor="company_agent",
    )
    # File exists WITHOUT calling dump_to_disk
    assert agent.beliefs_path().exists()


def test_auto_persist_can_be_disabled(tmp_path):
    """L7 — `auto_persist=False` keeps the filesystem untouched.

    Used by unit tests that don't care about persistence + want to
    avoid spurious file writes.
    """
    agent = CompanyAgent(tenant="silent-tenant", audit_dir=tmp_path, auto_persist=False)
    agent.update_belief(
        name="b1", value="v1", confidence="low",
        rationale="r", actor="company_agent",
    )
    assert not agent.beliefs_path().exists()


def test_round_trip_preserves_typed_belief_payload(tmp_path):
    """L7 — Typed beliefs round-trip through JSON without losing fields."""
    src = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    src.update_typed_belief(
        RiskBandBelief(topic="climate", band="HIGH", confidence_band="moderate"),
        rationale="r", actor="company_agent",
    )
    src.dump_to_disk()
    loaded = CompanyAgent.load_from_disk(tenant="adani-power", audit_dir=tmp_path)
    belief = loaded.beliefs["risk_band:climate"]
    assert belief.value["kind"] == "risk_band"
    assert belief.value["band"] == "HIGH"
    assert belief.value["topic"] == "climate"
