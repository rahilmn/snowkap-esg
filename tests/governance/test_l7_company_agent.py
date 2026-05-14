"""L7 — CompanyAgent stateful intelligence (scaffold).

Per-tenant agent that maintains an in-process state graph of "what we
currently believe about company X". Subscribes to the L6 advisor queue
for events affecting its tenant; calls `append_decision` itself when
its beliefs change.

Scope of this scaffold:
  - `Belief` dataclass: a single named claim about a tenant with
    confidence + last-update timestamp + provenance
  - `CompanyAgent.update_belief(name, value, ...)` — audited state change
  - `CompanyAgent.subscribe_to_advisor_queue()` — reads events from L6,
    filters to this tenant, returns the relevant ones
  - All belief mutations audit via L2 tags / L3 Toulmin / L4-passable

Out-of-scope (deferred to a fresh session):
  - Full domain belief model (Φ → ESG state, FY-level cascade snapshots)
  - LLM-driven belief revision logic
  - API endpoints for surfacing belief state
  - Persistence of belief state across restarts (JSON dump/load)
"""
from __future__ import annotations

import pytest

from engine.audit import (
    append_decision,
    audit_the_audit,
    make_toulmin,
    route_unverified_to_advisor,
)
from engine.governance.company_agent import (
    Belief,
    CompanyAgent,
)


def test_company_agent_starts_with_no_beliefs():
    """L7 — A fresh agent has empty state."""
    agent = CompanyAgent(tenant="adani-power")
    assert agent.beliefs == {}


def test_update_belief_stores_value(tmp_path):
    """L7 — `update_belief` persists the claim with confidence + provenance."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_transition_risk",
        value="HIGH",
        confidence="moderate",
        rationale="3 articles in last 7d on coal-phase-out delays",
        actor="company_agent",
    )
    b = agent.beliefs["climate_transition_risk"]
    assert b.value == "HIGH"
    assert b.confidence == "moderate"
    assert b.rationale == "3 articles in last 7d on coal-phase-out delays"


def test_update_belief_emits_audit(tmp_path):
    """L7 — Every belief change emits an L4-compatible audit entry."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_transition_risk",
        value="HIGH",
        confidence="moderate",
        rationale="3 articles last 7d on coal phase-out",
        actor="company_agent",
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True, report["violations"]
    assert report["scanned"] == 1


def test_update_belief_with_high_confidence_fires_advisor(tmp_path):
    """L7 — When the agent posts a HIGH-uncertainty belief, the advisor
    queue gets the event (consistency with L6's high-uncertainty rule)."""
    from engine.audit import read_advisor_queue
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_transition_risk",
        value="UNCLEAR",
        confidence="high",  # = high uncertainty → advisor
        rationale="contradictory signals across 5 articles",
        actor="company_agent",
    )
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert len(events) == 1
    assert events[0]["event_type"] == "high_uncertainty_decision"
    assert events[0]["company_slug"] == "adani-power"


def test_subscribe_to_advisor_queue_filters_to_tenant(tmp_path):
    """L7 — Agent reads only events relevant to its tenant.

    Other tenants' events must NOT leak through (multi-tenancy invariant).
    """
    # Two tenants emit high-uncertainty decisions
    for tenant, art in (("adani-power", "art_a"), ("jsw-energy", "art_j")):
        append_decision(
            "materiality_downgrade",
            article_id=art,
            company_slug=tenant,
            toulmin=make_toulmin("x", ["y"], "z", qualifier="q"),
            tags={
                "scope": "tenant",
                "signal_type": "analyst_judgment",
                "attribution": "criticality_scorer",
                "uncertainty": "high",
            },
            base_data_dir=tmp_path,
        )

    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    events = list(agent.subscribe_to_advisor_queue())
    assert len(events) == 1
    assert events[0]["company_slug"] == "adani-power"


def test_subscribe_to_advisor_queue_returns_empty_when_no_events(tmp_path):
    """L7 — Empty queue → empty iterator (no crash)."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    assert list(agent.subscribe_to_advisor_queue()) == []


def test_company_agent_multiple_beliefs_per_tenant(tmp_path):
    """L7 — Agent maintains many beliefs in parallel; later updates
    replace earlier ones for the same name (not append)."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_risk", value="HIGH",
        confidence="low", rationale="r1", actor="company_agent",
    )
    agent.update_belief(
        name="financial_exposure", value="₹45,000 Cr",
        confidence="low", rationale="r2", actor="company_agent",
    )
    # Update the first one
    agent.update_belief(
        name="climate_risk", value="MODERATE",
        confidence="low", rationale="r3 — after coal phase-out delay",
        actor="company_agent",
    )
    assert agent.beliefs["climate_risk"].value == "MODERATE"
    assert agent.beliefs["financial_exposure"].value == "₹45,000 Cr"
    assert len(agent.beliefs) == 2  # not 3


def test_company_agent_state_audit_passes_l4(tmp_path):
    """L7 load-bearing — full agent lifecycle produces an L4-passable trail.

    This is the integration check that L2 → L3 → L4 → L6 are all coherent
    when L7 is the producer.
    """
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_belief(
        name="climate_risk", value="HIGH",
        confidence="low", rationale="initial assessment",
        actor="company_agent",
    )
    agent.update_belief(
        name="climate_risk", value="CRITICAL",
        confidence="moderate", rationale="article 14 disclosed FY27 phase-out",
        actor="company_agent",
    )
    # Now a manual analyst override
    agent.update_belief(
        name="climate_risk", value="HIGH",
        confidence="low", rationale="analyst review: phase-out pushed to FY29",
        actor="manual:alice@snowkap.com",
    )

    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True, f"L4 failed: {report['violations']}"
    assert report["scanned"] == 3
