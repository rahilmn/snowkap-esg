"""L5 — SOP phase-gate state machine.

Replaces the implicit "pending → fetching → analysing → ready | failed"
state transitions scattered through `company_onboarder.py` and
`admin_onboard.py` with an explicit, audited state machine.

Every transition:
  1. Validates the from→to edge against the legal-transition graph
  2. Emits an `append_decision` with tags.signal_type=cascade_computation
     and tags.scope=tenant so L4 audit-the-audit can verify it
  3. Records who/why via Toulmin (subject to L3 citation cap)

This scaffold ships the state machine + tests. The company_onboarder
refactor that consumes it is a separate, mechanical PR.
"""
from __future__ import annotations

import pytest

from engine.governance.phase_gate import (
    LEGAL_TRANSITIONS,
    PhaseGate,
    PhaseGateError,
    PhaseState,
)


def test_phase_states_match_onboarding_status_model():
    """L5 — The 5 states match the existing `OnboardingStatus` model
    in [engine/models/onboarding_status.py] so the refactor is a
    drop-in replacement, not a renaming churn."""
    names = {s.value for s in PhaseState}
    assert names == {"pending", "fetching", "analysing", "ready", "failed"}


def test_legal_transitions_graph_locked():
    """L5 — These are the legal edges. Any change MUST be deliberate
    (PR review, not a typo).
    """
    expected = {
        PhaseState.PENDING: {PhaseState.FETCHING, PhaseState.FAILED},
        PhaseState.FETCHING: {PhaseState.ANALYSING, PhaseState.FAILED},
        PhaseState.ANALYSING: {PhaseState.READY, PhaseState.FAILED},
        PhaseState.READY: set(),     # terminal
        PhaseState.FAILED: set(),    # terminal
    }
    assert LEGAL_TRANSITIONS == expected


def test_phase_gate_initial_state_is_pending():
    """L5 — Every gate starts at PENDING."""
    gate = PhaseGate(tenant="adani-power")
    assert gate.state == PhaseState.PENDING


def test_phase_gate_advance_happy_path(tmp_path):
    """L5 — Happy path: pending → fetching → analysing → ready.

    Audit emits one decision per transition.
    """
    gate = PhaseGate(tenant="adani-power", audit_dir=tmp_path)
    gate.advance(PhaseState.FETCHING, actor="scheduler", reason="onboard triggered")
    gate.advance(PhaseState.ANALYSING, actor="pipeline", reason="fetch complete")
    gate.advance(PhaseState.READY, actor="pipeline", reason="analysis complete")
    assert gate.state == PhaseState.READY

    from engine.audit import read_decision_log
    entries = list(read_decision_log(base_data_dir=tmp_path))
    assert len(entries) == 3
    # All 3 transitions tagged + Toulmin'd
    for e in entries:
        assert e["tags"]["signal_type"] == "cascade_computation"
        assert e["tags"]["scope"] == "tenant"
        assert e["company_slug"] == "adani-power"
        assert "toulmin" in e


def test_phase_gate_rejects_illegal_transition():
    """L5 — Skipping a state (pending → ready) is illegal."""
    gate = PhaseGate(tenant="adani-power")
    with pytest.raises(PhaseGateError, match="illegal transition"):
        gate.advance(PhaseState.READY, actor="x", reason="y")


def test_phase_gate_terminal_state_blocks_further_advance():
    """L5 — READY and FAILED are terminal. No further advances."""
    gate = PhaseGate(tenant="adani-power")
    gate.advance(PhaseState.FAILED, actor="scheduler", reason="domain not resolvable")
    with pytest.raises(PhaseGateError, match="terminal"):
        gate.advance(PhaseState.PENDING, actor="x", reason="retry")


def test_phase_gate_failed_from_any_active_state():
    """L5 — Failed can be entered from PENDING, FETCHING, or ANALYSING."""
    for entry_state in (PhaseState.PENDING, PhaseState.FETCHING, PhaseState.ANALYSING):
        gate = PhaseGate(tenant="adani-power")
        # Walk to entry_state if not already there
        if entry_state != PhaseState.PENDING:
            gate.advance(PhaseState.FETCHING, actor="t", reason="r")
        if entry_state == PhaseState.ANALYSING:
            gate.advance(PhaseState.ANALYSING, actor="t", reason="r")
        gate.advance(PhaseState.FAILED, actor="t", reason="r")
        assert gate.state == PhaseState.FAILED


def test_phase_gate_emits_l4_compatible_audit(tmp_path):
    """L5 — The emitted decisions must pass L4 audit-the-audit
    (load-bearing: confirms phase-gate transitions are governable)."""
    from engine.audit import audit_the_audit
    gate = PhaseGate(tenant="adani-power", audit_dir=tmp_path)
    gate.advance(PhaseState.FETCHING, actor="scheduler", reason="onboard triggered")
    gate.advance(PhaseState.ANALYSING, actor="pipeline", reason="fetch complete")
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True, f"L4 failed on L5 emissions: {report['violations']}"
    assert report["scanned"] == 2
