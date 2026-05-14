"""Phase 3 §5.2 — Stage 11 dispatcher tests + writer persistence.

Locks the dispatcher contract so the LLM-prompt swap (per §5.3) is
strictly body-only.

Two sets:
  1. Dispatcher in isolation (3 roles always present, single-role
     failure isolated, JSON-friendly variant).
  2. Writer integration: `role_payloads` dict lands on persisted
     payload alongside `evidence_pack` and `perspectives`.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine.analysis.evidence_pack import (
    CascadeBlock,
    EvidencePack,
    FrameworkHit,
)
from engine.analysis.role_generators import (
    RecommendationStub,
    RoleDistinctPayload,
    dispatch_role_payloads,
    dispatch_role_payloads_as_dict,
    role_keys,
)


# ---------------------------------------------------------------------------
# Dispatcher contract
# ---------------------------------------------------------------------------


def test_role_keys_returns_three_canonical_keys():
    """The frontend / writer / metrics all key off these 3 strings."""
    keys = role_keys()
    assert keys == ("cfo", "ceo", "esg-analyst")


def test_dispatcher_always_returns_three_roles():
    """Even on a fully-empty pack, all 3 roles present so consumers
    can index without defensive `if "cfo" in payloads` checks."""
    out = dispatch_role_payloads(EvidencePack())
    assert set(out.keys()) == {"cfo", "ceo", "esg-analyst"}
    for role, payload in out.items():
        assert isinstance(payload, RoleDistinctPayload)
        assert payload.role == role


def test_dispatcher_propagates_recommendations_through_each_role():
    """Recs flow through unchanged; per-role whitelists fire inside
    the individual generators."""
    pack = EvidencePack(cascade=CascadeBlock(total_cr=500))
    recs = [
        RecommendationStub(title="Hedge USD", type="financial"),
        RecommendationStub(title="Strategic pivot", type="strategic"),
        RecommendationStub(title="File BRSR", type="framework"),
    ]
    out = dispatch_role_payloads(pack, recommendations=recs)
    cfo_types = {r.type for r in out["cfo"].recommendations}
    ceo_types = {r.type for r in out["ceo"].recommendations}
    analyst_types = {r.type for r in out["esg-analyst"].recommendations}
    assert "financial" in cfo_types
    assert "strategic" in ceo_types
    assert "framework" in analyst_types


def test_dispatcher_isolates_single_role_failure():
    """If one generator raises, the other two still produce. The
    failing role gets a placeholder payload (consumers see WHAT failed)."""
    pack = EvidencePack(cascade=CascadeBlock(total_cr=500))

    def _raise(*a, **kw):
        raise RuntimeError("ceo blew up")

    with patch(
        "engine.analysis.role_generators.dispatcher.generate_ceo_payload",
        side_effect=_raise,
    ):
        out = dispatch_role_payloads(pack)

    assert "cfo" in out
    assert "esg-analyst" in out
    # CFO + Analyst are real
    assert "P&L" in out["cfo"].headline or "₹" in out["cfo"].headline
    # CEO is the placeholder
    assert "generation failed" in out["ceo"].headline


def test_dispatcher_company_revenue_propagates_to_cfo_only():
    """company_revenue_cr is a CFO-specific arg (drives '% of revenue
    at stake'). CEO/Analyst generators don't take it; the dispatcher
    must not pass it where it's not accepted."""
    pack = EvidencePack(cascade=CascadeBlock(total_cr=500))
    out = dispatch_role_payloads(pack, company_revenue_cr=10_000)
    cfo_first = out["cfo"].role_takeaways[0]
    # 500 / 10000 = 5.0%
    assert "5.0%" in cfo_first or "% of revenue" in cfo_first


# ---------------------------------------------------------------------------
# JSON-friendly variant
# ---------------------------------------------------------------------------


def test_dispatcher_as_dict_is_json_serialisable():
    import json
    pack = EvidencePack(
        cascade=CascadeBlock(total_cr=500),
        frameworks=[FrameworkHit(code="BRSR:P6:Q14", is_mandatory=True)],
    )
    js = json.dumps(dispatch_role_payloads_as_dict(pack))
    parsed = json.loads(js)
    assert set(parsed.keys()) == {"cfo", "ceo", "esg-analyst"}
    # Each role is a serialised RoleDistinctPayload
    for role in ("cfo", "ceo", "esg-analyst"):
        assert "role" in parsed[role]
        assert "headline" in parsed[role]
        assert "hero_metric" in parsed[role]
        assert "visible_panels" in parsed[role]


# ---------------------------------------------------------------------------
# Writer integration — role_payloads lands on persisted payload
# ---------------------------------------------------------------------------


def _stub_pipeline_result():
    return SimpleNamespace(
        article_id="art-1", title="Test", url="https://e.com/a",
        source="Reuters", published_at="2026-05-10T00:00:00Z",
        company_slug="test-co", image_url="",
        to_dict=lambda: {"frameworks": [], "causal_chains": []},
        risk=None, frameworks=[], causal_chains=[],
    )


def _stub_insight():
    return SimpleNamespace(
        to_dict=lambda: {
            "headline": "Test headline",
            "decision_summary": {
                "financial_exposure": "₹500 Cr (engine estimate)",
                "materiality": "HIGH",
            },
            "event_polarity": "negative",
        },
    )


def _stub_recommendations():
    recs = [
        SimpleNamespace(
            title="Hedge USD exposure", type="financial",
            estimated_budget="₹10 Cr", payback_months=6,
            framework_section="BRSR:P6:Q14",
        ),
        SimpleNamespace(
            title="Strategic capex shift", type="strategic",
            estimated_budget="₹100-150 Cr", payback_months=24,
            framework_section="",
        ),
        SimpleNamespace(
            title="File BRSR P6 disclosure", type="framework",
            estimated_budget="₹0.5 Cr", payback_months=None,
            framework_section="BRSR:P6",
        ),
    ]
    return SimpleNamespace(
        recommendations=recs,
        do_nothing=False,
        to_dict=lambda: {"recommendations": [vars(r) for r in recs]},
    )


def test_writer_stamps_role_payloads_dict_on_payload():
    from engine.output import writer as writer_mod
    captured: dict = {}

    def _capture(path, data):
        if "insights" in str(path):
            captured["payload"] = data
        return path

    with patch.object(writer_mod, "_write", side_effect=_capture), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=_stub_recommendations(),
        )

    payload = captured.get("payload") or {}
    assert "role_payloads" in payload
    rps = payload["role_payloads"]
    assert set(rps.keys()) == {"cfo", "ceo", "esg-analyst"}
    # CFO got the financial rec; CEO got the strategic rec; Analyst got framework
    cfo_rec_types = {r["type"] for r in rps["cfo"]["recommendations"]}
    ceo_rec_types = {r["type"] for r in rps["ceo"]["recommendations"]}
    analyst_rec_types = {r["type"] for r in rps["esg-analyst"]["recommendations"]}
    assert "financial" in cfo_rec_types
    assert "strategic" in ceo_rec_types
    assert "framework" in analyst_rec_types
    # Cross-role distinctness still holds end-to-end
    assert "esg_positioning" not in cfo_rec_types
    assert "compliance" not in ceo_rec_types
    assert "financial" not in analyst_rec_types


def test_writer_role_payloads_carry_parsed_budget():
    """RecommendationStub.budget_cr must be a number after _parse_budget_cr
    chews the legacy '₹X Cr' string."""
    from engine.output import writer as writer_mod
    captured: dict = {}

    def _capture(path, data):
        if "insights" in str(path):
            captured["payload"] = data
        return path

    with patch.object(writer_mod, "_write", side_effect=_capture), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=_stub_recommendations(),
        )

    rps = (captured.get("payload") or {}).get("role_payloads") or {}
    # The financial rec carried "₹10 Cr" → budget_cr should be 10.0
    cfo_recs = rps["cfo"]["recommendations"]
    hedge = next((r for r in cfo_recs if "Hedge" in r["title"]), None)
    assert hedge is not None
    assert hedge["budget_cr"] == 10.0
    # The strategic rec carried "₹100-150 Cr" → upper bound = 150.0
    ceo_recs = rps["ceo"]["recommendations"]
    pivot = next((r for r in ceo_recs if "Strategic" in r["title"]), None)
    assert pivot is not None
    assert pivot["budget_cr"] == 150.0


def test_writer_role_payloads_empty_when_dispatcher_fails():
    """A complete dispatcher failure leaves `role_payloads: {}` rather
    than blowing up the write."""
    from engine.output import writer as writer_mod
    import engine.analysis.role_generators as rg_mod
    captured: dict = {}

    def _capture(path, data):
        if "insights" in str(path):
            captured["payload"] = data
        return path

    def _raise(*a, **kw):
        raise RuntimeError("dispatcher boom")

    with patch.object(writer_mod, "_write", side_effect=_capture), \
         patch.object(writer_mod, "upsert_article"), \
         patch.object(writer_mod, "get_output_dir", return_value=Path("/tmp/test-co"), create=True), \
         patch.object(rg_mod, "dispatch_role_payloads_as_dict", side_effect=_raise):
        writer_mod.write_insight(
            result=_stub_pipeline_result(),
            insight=_stub_insight(),
            perspectives={},
            recommendations=_stub_recommendations(),
        )

    payload = captured.get("payload") or {}
    assert "role_payloads" in payload
    assert payload["role_payloads"] == {}


# ---------------------------------------------------------------------------
# _parse_budget_cr unit tests
# ---------------------------------------------------------------------------


def test_parse_budget_handles_rupee_cr_string():
    from engine.output.writer import _parse_budget_cr
    assert _parse_budget_cr("₹500 Cr") == 500.0
    assert _parse_budget_cr("Rs. 1,200 Cr") == 1200.0
    assert _parse_budget_cr("Rs 250 Cr") == 250.0


def test_parse_budget_handles_range_takes_upper_bound():
    from engine.output.writer import _parse_budget_cr
    assert _parse_budget_cr("₹100-150 Cr") == 150.0
    assert _parse_budget_cr("₹0.5-1 Cr") == 1.0


def test_parse_budget_returns_none_on_no_number():
    from engine.output.writer import _parse_budget_cr
    assert _parse_budget_cr(None) is None
    assert _parse_budget_cr("") is None
    assert _parse_budget_cr("TBD") is None


def test_parse_budget_passes_through_numeric():
    from engine.output.writer import _parse_budget_cr
    assert _parse_budget_cr(500) == 500.0
    assert _parse_budget_cr(0.5) == 0.5
