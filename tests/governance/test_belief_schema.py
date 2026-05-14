"""L7 — Typed belief schema validation + CompanyAgent integration tests."""
from __future__ import annotations

import pytest

from engine.audit import audit_the_audit
from engine.governance.belief_schema import (
    FYCascadeSnapshotBelief,
    FinancialExposureBelief,
    FrameworkComplianceBelief,
    PainpointSeverityBelief,
    RiskBandBelief,
    TransitionStanceBelief,
)
from engine.governance.company_agent import CompanyAgent


# ---------------------------------------------------------------------------
# RiskBandBelief
# ---------------------------------------------------------------------------


def test_risk_band_belief_accepts_valid():
    b = RiskBandBelief(topic="climate", band="HIGH", confidence_band="moderate")
    assert b.kind == "risk_band"
    assert b.band == "HIGH"
    assert b.topic == "climate"


def test_risk_band_belief_rejects_bad_band():
    with pytest.raises(ValueError, match="band="):
        RiskBandBelief(topic="climate", band="EXTREME")  # type: ignore[arg-type]


def test_risk_band_belief_rejects_empty_topic():
    with pytest.raises(ValueError, match="topic"):
        RiskBandBelief(topic="", band="HIGH")


# ---------------------------------------------------------------------------
# FinancialExposureBelief
# ---------------------------------------------------------------------------


def test_financial_exposure_accepts_valid_range():
    b = FinancialExposureBelief(
        scenario="climate_transition_2030",
        exposure_cr_lo=100,
        exposure_cr_hi=500,
        method="cascade",
    )
    assert b.kind == "financial_exposure"
    assert b.exposure_cr_lo == 100


def test_financial_exposure_rejects_inverted_range():
    with pytest.raises(ValueError, match="exposure_cr_hi"):
        FinancialExposureBelief(
            scenario="x", exposure_cr_lo=500, exposure_cr_hi=100,
        )


def test_financial_exposure_rejects_negative_value():
    with pytest.raises(ValueError, match="non-negative"):
        FinancialExposureBelief(scenario="x", exposure_cr_lo=-1, exposure_cr_hi=100)


# ---------------------------------------------------------------------------
# TransitionStance / FrameworkCompliance / PainpointSeverity
# ---------------------------------------------------------------------------


def test_transition_stance_validates_enum():
    TransitionStanceBelief(stance="leader", horizon_fy="FY27")
    with pytest.raises(ValueError, match="stance="):
        TransitionStanceBelief(stance="ahead-of-curve")  # type: ignore[arg-type]


def test_framework_compliance_validates_status_and_id():
    FrameworkComplianceBelief(framework_id="BRSR", status="in_progress")
    with pytest.raises(ValueError, match="framework_id"):
        FrameworkComplianceBelief(framework_id="", status="in_progress")
    with pytest.raises(ValueError, match="status="):
        FrameworkComplianceBelief(framework_id="BRSR", status="done")  # type: ignore[arg-type]


def test_painpoint_severity_clamps_zero_to_one():
    PainpointSeverityBelief(painpoint_topic="climate", severity=0.0)
    PainpointSeverityBelief(painpoint_topic="climate", severity=1.0)
    PainpointSeverityBelief(painpoint_topic="climate", severity=0.5)
    with pytest.raises(ValueError, match="severity"):
        PainpointSeverityBelief(painpoint_topic="climate", severity=1.1)
    with pytest.raises(ValueError, match="severity"):
        PainpointSeverityBelief(painpoint_topic="climate", severity=-0.1)


# ---------------------------------------------------------------------------
# CompanyAgent.update_typed_belief integration
# ---------------------------------------------------------------------------


def test_update_typed_belief_routes_through_audit_chain(tmp_path):
    """L7 — typed belief update audits cleanly via the L4 gate."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_typed_belief(
        RiskBandBelief(topic="climate", band="HIGH", confidence_band="moderate"),
        rationale="3 articles in 7d on coal phase-out",
        actor="company_agent",
    )
    # Belief stored under the discriminating name
    assert "risk_band:climate" in agent.beliefs
    # Audit trail is L4-clean
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True, report["violations"]
    assert report["scanned"] == 1


def test_fy_cascade_snapshot_validates_fields():
    """L7 — FY-cascade belief enforces non-empty fy + primitive + non-negative base."""
    b = FYCascadeSnapshotBelief(
        fy="FY27", primitive="OX", delta_cr=120.5, base_value_cr=35000,
        method="cascade",
    )
    assert b.kind == "fy_cascade_snapshot"
    assert b.fy == "FY27"
    assert b.primitive == "OX"

    with pytest.raises(ValueError, match="fy"):
        FYCascadeSnapshotBelief(fy="", primitive="OX")
    with pytest.raises(ValueError, match="primitive"):
        FYCascadeSnapshotBelief(fy="FY27", primitive="")
    with pytest.raises(ValueError, match="base_value_cr"):
        FYCascadeSnapshotBelief(fy="FY27", primitive="OX", base_value_cr=-1)
    # Negative delta is ALLOWED — primitives can move down (e.g. emissions reduction)
    FYCascadeSnapshotBelief(fy="FY27", primitive="GE", delta_cr=-50.0, base_value_cr=1000)


def test_fy_cascade_snapshot_routes_through_agent_with_fy_primitive_discriminator(tmp_path):
    """L7 — `<fy>:<primitive>` discriminator separates per-year snapshots."""
    from engine.governance.company_agent import CompanyAgent
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path, auto_persist=False)
    agent.update_typed_belief(
        FYCascadeSnapshotBelief(fy="FY27", primitive="OX", delta_cr=120.5, base_value_cr=35000),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        FYCascadeSnapshotBelief(fy="FY28", primitive="OX", delta_cr=180.0, base_value_cr=37000),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        FYCascadeSnapshotBelief(fy="FY27", primitive="GE", delta_cr=-50.0, base_value_cr=1000),
        rationale="r", actor="company_agent",
    )
    # 3 distinct slots; each (fy, primitive) cell has its own belief
    names = set(agent.beliefs.keys())
    assert names == {
        "fy_cascade_snapshot:FY27:OX",
        "fy_cascade_snapshot:FY28:OX",
        "fy_cascade_snapshot:FY27:GE",
    }


def test_update_typed_belief_supports_all_five_kinds(tmp_path):
    """L7 — sanity that all 5 typed kinds route through to the agent."""
    agent = CompanyAgent(tenant="adani-power", audit_dir=tmp_path)
    agent.update_typed_belief(
        RiskBandBelief(topic="climate", band="HIGH"),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        FinancialExposureBelief(
            scenario="climate_transition_2030",
            exposure_cr_lo=100, exposure_cr_hi=500,
            method="cascade",
        ),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        TransitionStanceBelief(stance="lagging", horizon_fy="FY28"),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        FrameworkComplianceBelief(framework_id="BRSR", status="gap_identified"),
        rationale="r", actor="company_agent",
    )
    agent.update_typed_belief(
        PainpointSeverityBelief(painpoint_topic="water", severity=0.75),
        rationale="r", actor="company_agent",
    )
    assert len(agent.beliefs) == 5
    # Names follow the kind:discriminator convention
    names = set(agent.beliefs.keys())
    assert names == {
        "risk_band:climate",
        "financial_exposure:climate_transition_2030",
        "transition_stance",          # no discriminator → bare kind
        "framework_compliance:BRSR",
        "painpoint_severity:water",
    }


def test_update_typed_belief_rejects_non_typed_input(tmp_path):
    """L7 — defensive: passing a dict / string / None must raise."""
    agent = CompanyAgent(tenant="x", audit_dir=tmp_path)
    for bad in ({}, "stance:leader", 42, None):
        with pytest.raises(ValueError, match="TypedBelief"):
            agent.update_typed_belief(bad, rationale="r", actor="a")  # type: ignore[arg-type]
