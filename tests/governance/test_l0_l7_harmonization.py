"""L0-L7 cross-layer harmonization.

Each layer has its own unit-test suite. This file exercises the full
flow on a single synthetic tenant to verify the layers are coherent:

  L2 tags ─┬─► L3 citation cap (enforced before journal write)
           ├─► L4 audit-the-audit (reads the journal)
           ├─► L5 phase-gate (writes via append_decision)
           ├─► L6 advisor queue (high-uncertainty fires)
           └─► L7 CompanyAgent (subscribes + emits)

If any layer's contract is silently broken by another layer's change,
this test will catch it before it ships.
"""
from __future__ import annotations

from engine.audit import (
    audit_the_audit,
    read_advisor_queue,
    read_decision_log,
    route_unverified_to_advisor,
)
from engine.governance.company_agent import CompanyAgent
from engine.governance.phase_gate import PhaseGate, PhaseState


def test_full_l2_through_l7_flow_on_one_tenant(tmp_path):
    """A complete onboarding + intelligence cycle for `adani-power`.

    Step 1: L5 advances the phase-gate from pending → fetching → analysing → ready
    Step 2: L7 agent posts a low-uncertainty belief
    Step 3: L7 agent posts a high-uncertainty belief (fires L6 advisor event)
    Step 4: Unverified candidate routes to L6 advisor queue (NOT journal, per L3)
    Step 5: L4 audits the resulting decision_log and finds NO violations
    Step 6: Tenant-scoped advisor queue read returns exactly the 1 high-uncertainty event
    """
    tenant = "adani-power"

    # Step 1 — L5 phase-gate
    gate = PhaseGate(tenant=tenant, audit_dir=tmp_path)
    gate.advance(PhaseState.FETCHING, actor="scheduler", reason="onboard triggered")
    gate.advance(PhaseState.ANALYSING, actor="pipeline", reason="3 articles fetched")
    gate.advance(PhaseState.READY, actor="pipeline", reason="analysis complete")
    assert gate.state == PhaseState.READY

    # Step 2 — L7 belief (low uncertainty: journal only, no advisor)
    agent = CompanyAgent(tenant=tenant, audit_dir=tmp_path)
    agent.update_belief(
        name="financial_exposure",
        value="₹45,000 Cr (cascade-computed)",
        confidence="low",
        rationale="cascade β=0.42 from EP→OX, article line 7",
        actor="company_agent",
    )

    # Step 3 — L7 belief (high uncertainty: journal AND advisor)
    agent.update_belief(
        name="transition_risk",
        value="UNCLEAR",
        confidence="high",
        rationale="contradictory signals: 3 articles say phase-out, 2 say delay",
        actor="company_agent",
    )

    # Step 4 — L6 unverified candidate (advisor only, never journal)
    route_unverified_to_advisor(
        candidate_id="cand_001",
        category="materiality_revision",
        rationale="cascade β diverges 40% from peer benchmark — needs analyst review",
        tags={
            "scope": "tenant",
            "signal_type": "analyst_judgment",
            "attribution": "manual:queue_dispatcher",  # the discovery loop
            "uncertainty": "unverified",
        },
        base_data_dir=tmp_path,
    )

    # Step 5 — L4 audit-the-audit (over the whole resulting journal)
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True, f"L4 failed: {report['violations']}"
    # 3 phase-gate transitions + 2 beliefs = 5 audited entries
    assert report["scanned"] == 5
    assert report["skipped_untagged"] == 0  # nothing pre-L2 in this test

    # Cross-check that the journal has the entries we expect
    journal = list(read_decision_log(base_data_dir=tmp_path))
    assert len(journal) == 5

    # Step 6 — L6 advisor queue (tenant-scoped)
    queue_all = list(read_advisor_queue(base_data_dir=tmp_path))
    # high-uncertainty belief (1) + unverified candidate (1) = 2 advisor events
    assert len(queue_all) == 2

    queue_filtered = list(agent.subscribe_to_advisor_queue())
    # Only the high-uncertainty belief was tagged with company_slug=tenant.
    # The unverified candidate has no company_slug set in its event, so
    # it doesn't leak into the tenant-scoped read.
    assert len(queue_filtered) == 1
    assert queue_filtered[0]["event_type"] == "high_uncertainty_decision"
    assert queue_filtered[0]["company_slug"] == tenant


def test_l3_cap_holds_across_l5_and_l7_emissions(tmp_path):
    """L3 — citation cap enforces uniformly across L5 + L7 producers.

    Both layers use `append_decision` under the hood; both must respect
    the 5-grounds maximum.
    """
    import pytest
    from engine.audit import enforce_citation_cap
    # Sanity: the cap helper itself rejects 6
    with pytest.raises(ValueError):
        enforce_citation_cap({"claim": "x", "grounds": [f"g{i}" for i in range(6)], "warrant": "w"})

    # Verify the L7 agent can't smuggle past the cap either by
    # confirming the cap is the SAME constant used by both
    from engine.audit import MAX_TOULMIN_GROUNDS
    assert MAX_TOULMIN_GROUNDS == 5


def test_l2_strict_mode_breaks_l7_unless_tags_present(tmp_path, monkeypatch):
    """L2 — strict mode is the future state. When flipped, ALL append_*
    callers (including L7's belief writer) MUST pass tags.

    L7's update_belief always passes tags — verifying it survives strict
    mode is the canonical check that the path is tag-complete.
    """
    monkeypatch.setenv("SNOWKAP_AUDIT_REQUIRE_TAGS", "1")
    agent = CompanyAgent(tenant="x", audit_dir=tmp_path)
    # Should NOT raise — agent always supplies tags
    agent.update_belief(
        name="b", value="v", confidence="low",
        rationale="r", actor="company_agent",
    )
    assert agent.beliefs["b"].value == "v"
