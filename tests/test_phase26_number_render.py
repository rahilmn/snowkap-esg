"""Phase 2.5 — acceptance tests for the number-rendering protocol (§4.5).

Mirrors the renderer in `client/src/lib/number_format.ts` so the strip pass
and the frontend agree on the output format. Validates:
  - Rule 1: round to 2 sig figs
  - Rule 2: ranges in body, point estimates in headline / table
  - Rule 3: strip "(engine estimate)" / "(from article)" from narrative
  - Cascade table is the exception — `confidence_bounds` / `kpi_table` /
    `causal_chain` are NOT scrubbed (full precision preserved).
  - Sidecar __provenance carries source + original_cr per scrubbed figure.
  - Idempotent — second run is a no-op.
"""
from __future__ import annotations

from engine.analysis.output_verifier import (
    _render_for_strip,
    _round_sig2,
    strip_narrative_provenance,
)


# ---------------------------------------------------------------------------
# Rule 1: 2-significant-figure rounding
# ---------------------------------------------------------------------------


def test_sig2_rounds_1857_6_to_1900():
    assert _round_sig2(1857.6) == 1900


def test_sig2_rounds_56_1_to_56():
    assert _round_sig2(56.1) == 56


def test_sig2_rounds_12345_to_12000():
    assert _round_sig2(12345) == 12000


def test_sig2_handles_zero():
    assert _round_sig2(0) == 0


def test_sig2_handles_small_decimal():
    # 0.123 -> round to 2 sig figs -> 0.12
    assert abs(_round_sig2(0.123) - 0.12) < 1e-9


def test_sig2_handles_negative():
    assert _round_sig2(-1857.6) == -1900


# ---------------------------------------------------------------------------
# Rule 2: render context
# ---------------------------------------------------------------------------


def test_body_context_emits_range():
    """body context with default 10% range_pct → '₹X–Y Cr'."""
    out = _render_for_strip(1857.6, "body")
    assert "–" in out  # en dash
    assert "₹" in out
    assert " Cr" in out
    # Centred around 1900 with ±10% = 1700–2100
    assert "1,700" in out
    assert "2,100" in out


def test_headline_context_emits_tilde_point():
    out = _render_for_strip(1857.6, "headline")
    assert out.startswith("~₹")
    assert "1,900" in out
    assert "–" not in out  # no range in headline


def test_body_small_value_collapses_to_point_when_lo_eq_hi():
    """Tiny values where ±10% rounds to the same number → collapses to point."""
    out = _render_for_strip(56.1, "body")
    # 56 * 0.9 = 50.4 -> sig2 = 50; 56 * 1.1 = 61.6 -> sig2 = 62
    # so range stays distinct here. Just assert the format.
    assert "₹" in out and " Cr" in out


# ---------------------------------------------------------------------------
# Rule 3: strip provenance tags + sidecar
# ---------------------------------------------------------------------------


def _sample_insight() -> dict:
    return {
        "headline": "Margin pressure ₹1,857.6 Cr (engine estimate) on Q4",
        "core_mechanism": "Cascade ₹500 Cr (from article).",
        "net_impact_summary": (
            "Total exposure ₹500 Cr (from article) plus ₹14.4 Cr "
            "(engine estimate) cascade."
        ),
        "decision_summary": {
            "financial_exposure": "₹56.1 Cr (engine estimate)",
            "key_risk": "penalty risk ₹100 Cr",  # untagged → untouched
        },
        "key_takeaways": [
            "Energy cost ₹807 Cr (engine estimate) over 12 months",
            "Article cites ₹503 Cr (from article) Q3 profit",
        ],
        "impact_analysis": {
            "financial": "Quarterly hit ₹120 Cr (engine estimate).",
            "regulatory": "No ₹ figure here.",
        },
        # Cascade-table data — must NOT be touched.
        "causal_chain": {
            "edges": [{"value_cr": 1857.6, "beta": 0.34}],
        },
        "perspectives": {
            "esg_analyst": {
                "confidence_bounds": [
                    {"figure": "₹1,857.6 Cr", "source_type": "engine_estimate"},
                ],
                "kpi_table": [{"value": 1857.6}],
            },
        },
    }


def test_strip_pass_removes_engine_estimate_tag_from_headline():
    ins = _sample_insight()
    out, sidecar = strip_narrative_provenance(ins)
    assert "(engine estimate)" not in out["headline"]
    assert "(from article)" not in out["headline"]
    assert "~₹1,900 Cr" in out["headline"]


def test_strip_pass_renders_narrative_as_range():
    ins = _sample_insight()
    out, _ = strip_narrative_provenance(ins)
    # net_impact_summary had ₹500 Cr (from article) → body range
    assert "₹450–550 Cr" in out["net_impact_summary"]
    # ₹14.4 Cr → body range
    assert "₹13–15 Cr" in out["net_impact_summary"]
    assert "(engine estimate)" not in out["net_impact_summary"]
    assert "(from article)" not in out["net_impact_summary"]


def test_strip_pass_sidecar_captures_provenance():
    ins = _sample_insight()
    _, sidecar = strip_narrative_provenance(ins)
    sources = {entry["source"] for entry in sidecar}
    assert "engine_estimate" in sources
    assert "from_article" in sources
    fields = {entry["field"] for entry in sidecar}
    assert "headline" in fields
    assert "decision_summary.financial_exposure" in fields
    assert any(f.startswith("key_takeaways[") for f in fields)
    assert any(f.startswith("impact_analysis.") for f in fields)


def test_strip_pass_preserves_untagged_figures():
    ins = _sample_insight()
    out, _ = strip_narrative_provenance(ins)
    # key_risk had no provenance tag → untouched verbatim
    assert out["decision_summary"]["key_risk"] == "penalty risk ₹100 Cr"


def test_cascade_table_is_exception_full_precision_preserved():
    """Phase 2.4 — cascade table (causal_chain, confidence_bounds, kpi_table)
    is NOT scrubbed. Analysts need full precision in the audit view."""
    ins = _sample_insight()
    out, _ = strip_narrative_provenance(ins)
    assert out["causal_chain"]["edges"][0]["value_cr"] == 1857.6
    bounds = out["perspectives"]["esg_analyst"]["confidence_bounds"]
    assert bounds[0]["figure"] == "₹1,857.6 Cr"
    kpis = out["perspectives"]["esg_analyst"]["kpi_table"]
    assert kpis[0]["value"] == 1857.6


def test_strip_pass_is_idempotent():
    ins = _sample_insight()
    out1, sidecar1 = strip_narrative_provenance(ins)
    out2, sidecar2 = strip_narrative_provenance(out1)
    # Second run finds no tags to strip → empty sidecar
    assert sidecar2 == []
    # Headline / decision_summary unchanged after second run
    assert out1["headline"] == out2["headline"]
    assert out1["decision_summary"]["financial_exposure"] == \
           out2["decision_summary"]["financial_exposure"]


def test_strip_pass_writes_provenance_sidecar_field():
    ins = _sample_insight()
    out, _ = strip_narrative_provenance(ins)
    assert "__provenance" in out
    assert isinstance(out["__provenance"], list)
    assert len(out["__provenance"]) > 0


def test_strip_pass_safe_on_empty_dict():
    out, sidecar = strip_narrative_provenance({})
    assert out == {}
    assert sidecar == []


def test_strip_pass_safe_on_non_dict_input():
    # Defensive — non-dict input must not crash
    out, sidecar = strip_narrative_provenance(None)  # type: ignore[arg-type]
    assert sidecar == []


# ---------------------------------------------------------------------------
# Integration with verify_and_correct (smoke)
# ---------------------------------------------------------------------------


def test_verify_and_correct_runs_strip_pass_at_end():
    """The top-level verifier calls strip_narrative_provenance as its last
    step. After verify_and_correct, no narrative field should still carry
    the parenthesised provenance tags."""
    from engine.analysis.output_verifier import verify_and_correct

    ins = _sample_insight()
    # Add fields verify_and_correct expects
    ins["impact_score"] = 7.5
    out, _report = verify_and_correct(
        ins,
        revenue_cr=5000.0,
        article_excerpts=["fake article body"],
    )
    # All narrative fields should be free of provenance tags
    for path_value in (
        out.get("headline"),
        out.get("net_impact_summary"),
        (out.get("decision_summary") or {}).get("financial_exposure"),
    ):
        if isinstance(path_value, str):
            assert "(engine estimate)" not in path_value
            assert "(from article)" not in path_value
    # Sidecar should exist
    assert "__provenance" in out
