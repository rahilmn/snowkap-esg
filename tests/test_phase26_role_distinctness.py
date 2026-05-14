"""Phase 3 §5.7 — role distinctness acceptance tests.

Validates the two new structural distinctness gates:
  1. Recommendation type whitelist enforcement (§5.4)
     - CFO: zero esg_positioning, strategic, brand
     - CEO: zero compliance, kpi_tracking, audit
     - Analyst: zero capital_allocation, financial, brand
  2. Cross-role ₹ drift detection (§5.5)
     - Same metric across roles must drift < 5% else flagged

These complement the existing perspective_dedup (n-gram overlap)
detector to catch the "85% identical" failure mode at the structural
level — wrong-role recs and inconsistent ₹ figures across CXO views.
"""
from __future__ import annotations

from engine.analysis.cross_role_drift import (
    DEFAULT_DRIFT_THRESHOLD,
    compute_drift,
    extract_role_figures,
    serialise_report,
)
from engine.analysis.recommendation_type_whitelist import (
    ALLOWED_BY_ROLE,
    REJECTED_BY_ROLE,
    filter_recommendations_for_role,
    is_allowed,
    is_rejected,
    split_recommendations_by_role,
)


# ---------------------------------------------------------------------------
# §5.4 — recommendation type whitelist
# ---------------------------------------------------------------------------


def test_cfo_allows_financial_operational_compliance():
    for t in ("financial", "operational", "compliance"):
        assert is_allowed(t, "cfo"), t
        assert not is_rejected(t, "cfo"), t


def test_cfo_rejects_esg_positioning_and_strategic_and_brand():
    for t in ("esg_positioning", "strategic", "brand"):
        assert is_rejected(t, "cfo"), t
        assert not is_allowed(t, "cfo"), t


def test_ceo_allows_strategic_brand_capital_allocation():
    for t in ("strategic", "esg_positioning", "brand", "capital_allocation"):
        assert is_allowed(t, "ceo"), t


def test_ceo_rejects_compliance_kpi_audit():
    for t in ("compliance", "kpi_tracking", "audit"):
        assert is_rejected(t, "ceo"), t


def test_analyst_allows_framework_disclosure_kpi_audit():
    for t in ("framework", "disclosure", "kpi_tracking", "audit"):
        assert is_allowed(t, "esg_analyst"), t
        assert is_allowed(t, "analyst"), t  # alias works


def test_analyst_rejects_capital_allocation_financial_brand():
    for t in ("capital_allocation", "financial", "brand"):
        assert is_rejected(t, "esg_analyst"), t
        assert is_rejected(t, "analyst"), t


def test_unknown_role_does_not_filter():
    assert is_allowed("compliance", "random_role")
    assert not is_rejected("compliance", "random_role")


def test_filter_recommendations_for_role_splits_correctly():
    recs = [
        {"id": 1, "type": "financial", "title": "Hedge ₹500 Cr"},
        {"id": 2, "type": "esg_positioning", "title": "Refresh ESG narrative"},
        {"id": 3, "type": "operational", "title": "Process redesign"},
    ]
    allowed, rejected = filter_recommendations_for_role(recs, "cfo")
    assert [r["id"] for r in allowed] == [1, 3]
    assert [r["id"] for r in rejected] == [2]
    assert rejected[0]["rejected_for_role"] == "cfo"
    assert "esg_positioning" in rejected[0]["rejected_reason"]


def test_filter_does_not_mutate_input_list():
    recs = [{"id": 1, "type": "financial"}]
    original = list(recs)
    filter_recommendations_for_role(recs, "cfo")
    assert recs == original
    assert "rejected_for_role" not in recs[0]


def test_split_recommendations_by_role_routes_each_action():
    """Same source list → 3 role-specific allowlists. The forbidden types
    drop out per role; allowed types stay."""
    recs = [
        {"id": 1, "type": "financial"},          # CFO ✓, CEO ✗, Analyst ✗
        {"id": 2, "type": "strategic"},          # CFO ✗, CEO ✓, Analyst —
        {"id": 3, "type": "framework"},          # CFO —, CEO —, Analyst ✓
        {"id": 4, "type": "compliance"},         # CFO ✓, CEO ✗, Analyst —
        {"id": 5, "type": "capital_allocation"}, # CFO —, CEO ✓, Analyst ✗
    ]
    out = split_recommendations_by_role(recs)
    cfo_ids = [r["id"] for r in out["cfo"]]
    ceo_ids = [r["id"] for r in out["ceo"]]
    ana_ids = [r["id"] for r in out["esg_analyst"]]
    assert 1 in cfo_ids and 2 not in cfo_ids
    assert 2 in ceo_ids and 4 not in ceo_ids
    assert 3 in ana_ids and 1 not in ana_ids


def test_whitelist_is_case_insensitive():
    assert is_allowed("FINANCIAL", "cfo")
    assert is_allowed("Financial", "CFO")


def test_whitelist_handles_empty_inputs():
    # Untyped recs flow through unchanged (back-compat)
    assert is_allowed("", "cfo")
    assert is_allowed("financial", "")
    allowed, rejected = filter_recommendations_for_role([{}, {"type": ""}], "cfo")
    assert len(allowed) == 2
    assert rejected == []


def test_allowed_and_rejected_sets_are_disjoint_per_role():
    """Sanity: a single type cannot be both allowed AND rejected for the
    same role. (They CAN both be empty-set membership for unknown types.)"""
    for role in ("cfo", "ceo", "esg_analyst"):
        intersection = ALLOWED_BY_ROLE[role] & REJECTED_BY_ROLE[role]
        assert intersection == frozenset(), f"{role}: {intersection}"


# ---------------------------------------------------------------------------
# §5.5 — cross-role ₹ drift detector
# ---------------------------------------------------------------------------


def test_extract_role_figures_finds_rupees_in_strings():
    payload = {
        "headline": "Margin pressure ₹500 Cr on Q4",
        "key_takeaways": [
            "Energy cost ₹807 Cr over 12 months",
            "No ₹ figure here",
        ],
        "nested": {"foo": "Cascade ₹14.4 Cr."},
    }
    rf = extract_role_figures("cfo", payload)
    assert sorted(rf.figures) == [14.4, 500.0, 807.0]
    assert rf.max_cr == 807.0
    assert "headline" in rf.by_field
    assert any(p.startswith("key_takeaways[") for p in rf.by_field)


def test_compute_drift_clean_when_all_roles_agree():
    payloads = {
        "cfo": {"headline": "₹500 Cr exposure"},
        "ceo": {"headline": "₹510 Cr at stake"},  # 2% drift, under 5% threshold
        "esg_analyst": {"headline": "₹495 Cr"},
    }
    report = compute_drift(payloads)
    assert not report.has_violations
    assert report.canonical_cr == 510.0


def test_compute_drift_flags_when_above_threshold():
    payloads = {
        "cfo": {"headline": "₹500 Cr exposure"},
        "ceo": {"headline": "₹450 Cr at stake"},
        "esg_analyst": {"confidence_bounds": [{"figure": "₹560 Cr"}]},  # 12%+
    }
    report = compute_drift(payloads)
    assert report.has_violations
    assert report.canonical_cr == 560.0
    # CFO ↔ Analyst (500 vs 560) = 10.7%, CEO ↔ Analyst (450 vs 560) = 19.6%
    pairs = {(v.role_a, v.role_b) for v in report.violations}
    assert ("ceo", "esg_analyst") in pairs


def test_compute_drift_uses_max_per_role():
    """When a role mentions multiple ₹ figures (e.g. headline + per-line),
    drift uses the max as the canonical claim per role."""
    payloads = {
        "cfo": {"headline": "₹500 Cr", "key_risk": "of which ₹100 Cr Q1"},
        "ceo": {"headline": "₹510 Cr"},
    }
    report = compute_drift(payloads)
    # CFO max = 500, CEO max = 510 → 1.96% drift, no violation
    assert not report.has_violations
    assert report.by_role["cfo"].max_cr == 500.0


def test_compute_drift_threshold_is_configurable():
    payloads = {
        "cfo": {"headline": "₹500 Cr"},
        "ceo": {"headline": "₹510 Cr"},  # 1.96% drift
    }
    # Stricter threshold catches the 1.96% drift
    strict = compute_drift(payloads, threshold=0.01)
    assert strict.has_violations
    # Default 5% threshold lets it through
    default = compute_drift(payloads)
    assert not default.has_violations


def test_compute_drift_handles_role_with_zero_figures():
    """A role with no ₹ figures (e.g. CEO paragraph after Phase 3 'no rupees
    in role_paragraph' rule) is excluded from the pairwise check."""
    payloads = {
        "cfo": {"headline": "₹500 Cr"},
        "ceo": {"role_paragraph": "Strategic positioning, board alignment."},
        "esg_analyst": {"headline": "₹510 Cr"},
    }
    report = compute_drift(payloads)
    assert not report.has_violations
    assert report.by_role["ceo"].max_cr is None
    assert report.canonical_cr == 510.0


def test_compute_drift_empty_payloads_safe():
    report = compute_drift({})
    assert not report.has_violations
    assert report.canonical_cr is None


def test_serialise_report_is_json_friendly():
    """Output of serialise_report must be plain-JSON serialisable for
    cross_role_drift.jsonl logging."""
    import json

    payloads = {
        "cfo": {"headline": "₹500 Cr"},
        "ceo": {"headline": "₹450 Cr"},
    }
    report = compute_drift(payloads)
    serialised = serialise_report(report)
    # Round-trip JSON serialisation must succeed without TypeError
    js = json.dumps(serialised)
    parsed = json.loads(js)
    assert "canonical_cr" in parsed
    assert "by_role" in parsed
    assert "violations" in parsed
    assert isinstance(parsed["violations"], list)


def test_threshold_default_is_5_percent():
    """Locked-in default per plan §5.5 ('drift > 5% → regenerate')."""
    assert DEFAULT_DRIFT_THRESHOLD == 0.05


# ---------------------------------------------------------------------------
# Wiring tests — prove the modules are actually firing in the pipeline
# ---------------------------------------------------------------------------


def test_verify_and_correct_emits_cross_role_drift_sidecar():
    """The verify_and_correct integration stamps __cross_role_drift onto
    the insight even on a clean (no-violation) run, so an audit tool can
    confirm the check ran."""
    from engine.analysis.output_verifier import verify_and_correct

    insight = {
        "decision_summary": {"financial_exposure": "₹500 Cr"},
        "perspectives": {
            "cfo": {"headline": "₹500 Cr exposure"},
            "ceo": {"headline": "₹510 Cr at stake"},
            "esg_analyst": {"headline": "₹495 Cr"},
        },
    }
    out, _ = verify_and_correct(insight, revenue_cr=5000.0)
    assert "__cross_role_drift" in out
    assert out["__cross_role_drift"]["has_violations"] is False
    # canonical_cr should be the global max (510)
    assert out["__cross_role_drift"]["canonical_cr"] == 510.0


def test_verify_and_correct_flags_drift_when_roles_disagree():
    from engine.analysis.output_verifier import verify_and_correct

    insight = {
        "decision_summary": {"financial_exposure": "₹500 Cr"},
        "perspectives": {
            "cfo": {"headline": "₹500 Cr exposure"},
            "ceo": {"headline": "₹400 Cr at stake"},  # 20% drift vs CFO
        },
    }
    out, report = verify_and_correct(insight, revenue_cr=5000.0)
    assert out["__cross_role_drift"]["has_violations"] is True
    # Warning surfaced in report (advisory)
    drift_warning_present = any(
        "cross-role" in w.lower() for w in (report.warnings + report.corrections)
    )
    assert drift_warning_present


def test_recommendation_rankings_filter_by_whitelist():
    """Direct check that the per-role rankings shrink when the ranking
    contains forbidden types. We bypass the LLM by constructing
    Recommendation objects manually and calling _build_perspective_rankings
    + the filter the same way generate_recommendations does."""
    from engine.analysis.recommendation_engine import (
        Recommendation,
        _build_perspective_rankings,
    )
    from engine.analysis.recommendation_type_whitelist import is_rejected

    def _r(rec_type: str, title: str) -> Recommendation:
        return Recommendation(
            title=title,
            description="…",
            type=rec_type,
            responsible_party="CFO",
            framework_section="BRSR:P6",
            deadline="2026-12-31",
            estimated_budget="₹10 Cr",
            profitability_link="±",
            priority="HIGH",
            urgency="immediate",
            estimated_impact="High",
            risk_of_inaction=7,
        )

    recs = [
        _r("financial", "Hedge cost"),          # CFO ✓ CEO ✗ Analyst ✗
        _r("strategic", "Capture upside"),      # CFO ✗ CEO ✓ Analyst —
        _r("framework", "Disclose under BRSR"), # CFO — CEO — Analyst ✓
        _r("compliance", "File SEBI report"),   # CFO ✓ CEO ✗ Analyst —
    ]

    raw_rankings = _build_perspective_rankings(recs)

    # Apply the same whitelist filter generate_recommendations applies
    filtered: dict[str, list[int]] = {}
    for role_key, idx_list in raw_rankings.items():
        filtered[role_key] = [
            i for i in idx_list if not is_rejected(recs[i].type, role_key)
        ]

    # CFO must have NO strategic recs
    cfo_types = [recs[i].type for i in filtered["cfo"]]
    assert "strategic" not in cfo_types
    # CEO must have NO compliance recs
    ceo_types = [recs[i].type for i in filtered["ceo"]]
    assert "compliance" not in ceo_types
    # Analyst must have NO financial recs
    ana_types = [recs[i].type for i in filtered["esg-analyst"]]
    assert "financial" not in ana_types
