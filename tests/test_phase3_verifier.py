"""Phase 3 tests: output verifier, precedent query, ROI cap flagging."""

from __future__ import annotations

from engine.analysis.output_verifier import (
    CFO_MAX_WORDS,
    enforce_source_tags,
    flag_roi_cap,
    flag_roi_caps_bulk,
    inject_framework_rationales,
    sanitise_cfo_headline,
    verify_and_correct,
    verify_margin_math,
)


# ---------------------------------------------------------------------------
# Margin math verifier
# ---------------------------------------------------------------------------


def test_margin_math_passes_when_consistent():
    """₹50 Cr on ₹50,000 Cr revenue = 10 bps. Consistent figures stay unchanged."""
    insight = {
        "financial_timeline": {
            "immediate": {
                "headline": "ICICI faces ₹50 Cr GST demand",
                "margin_pressure": "Margin pressure 10 bps on ₹50 Cr event",
            }
        }
    }
    out, report = verify_margin_math(insight, revenue_cr=50000)
    assert report.math_ok is True
    assert not report.corrections


def test_margin_math_corrects_off_by_70_pct():
    """The actual Adani Power bug: ₹33.8 Cr on ₹45K Cr rev should be ~7.5 bps, not 4.4."""
    insight = {
        "financial_timeline": {
            "immediate": {
                "headline": "Adani Power margin hit ₹33.8 Cr exposure",
                "margin_pressure": "4.4 bps margin pressure from event",
            }
        }
    }
    out, report = verify_margin_math(insight, revenue_cr=45000)
    assert report.math_ok is False
    # Expected 33.8 / 45000 * 10000 = 7.511 bps
    assert report.margin_bps_corrected is not None
    assert 7.0 < report.margin_bps_corrected < 8.0
    assert "computed_override" in out["financial_timeline"]["immediate"]["margin_pressure"]


def test_margin_math_skips_when_revenue_zero():
    insight = {
        "financial_timeline": {"immediate": {"margin_pressure": "₹50 Cr ⇒ 10 bps"}}
    }
    out, report = verify_margin_math(insight, revenue_cr=0)
    assert report.math_ok is True  # fail-open
    assert any("revenue_cr" in w for w in report.warnings)


def test_margin_math_skips_when_no_bps_in_text():
    """No bps cited anywhere → nothing to verify."""
    insight = {"financial_timeline": {"immediate": {"headline": "₹50 Cr GST demand"}}}
    out, report = verify_margin_math(insight, revenue_cr=50000)
    assert report.math_ok is True


# ---------------------------------------------------------------------------
# Source tag enforcement
# ---------------------------------------------------------------------------


def test_source_tag_added_when_missing():
    insight = {"decision_summary": {"financial_exposure": "₹180 Cr margin at risk"}}
    article_excerpts = ["Adani Power faces costs but no figure mentioned."]
    out, added = enforce_source_tags(insight, article_excerpts)
    assert added == 1
    assert "(engine estimate)" in out["decision_summary"]["financial_exposure"]


def test_source_tag_recognises_article_figure():
    """If the same ₹ figure appears in the article text, tag as from article."""
    insight = {"decision_summary": {"financial_exposure": "₹180 Cr exposure"}}
    article_excerpts = ["SEBI demand of ₹180 Cr was issued last week."]
    out, added = enforce_source_tags(insight, article_excerpts)
    assert added == 1
    assert "(from article)" in out["decision_summary"]["financial_exposure"]


def test_source_tag_noop_when_already_tagged():
    insight = {"x": "₹50 Cr (engine estimate) plus tax"}
    out, added = enforce_source_tags(insight)
    assert added == 0
    assert out == insight


def test_source_tag_walks_nested_structures():
    insight = {
        "a": {"b": ["₹10 Cr at risk", {"c": "₹20 Cr exposure"}]},
    }
    out, added = enforce_source_tags(insight)
    assert added == 2


# ---------------------------------------------------------------------------
# CFO headline hygiene
# ---------------------------------------------------------------------------


def test_cfo_headline_strips_greek_letters():
    headline = "β = 0.24, lag 0-3m, ΔOpex ₹180 Cr at risk"
    clean, modified = sanitise_cfo_headline(headline)
    assert modified is True
    assert "β" not in clean
    assert "Δ" not in clean
    assert "₹180 Cr" in clean


def test_cfo_headline_strips_framework_ids():
    headline = "BRSR:P6 margin at risk: ₹50 Cr"
    clean, modified = sanitise_cfo_headline(headline)
    assert modified is True
    assert "BRSR" not in clean
    assert "₹50 Cr" in clean


def test_cfo_headline_truncates_at_word_cap():
    long = " ".join(["word"] * (CFO_MAX_WORDS + 10))
    clean, modified = sanitise_cfo_headline(long)
    assert modified is True
    assert len(clean.split()) <= CFO_MAX_WORDS + 1  # + ellipsis


def test_cfo_headline_empty_input():
    assert sanitise_cfo_headline("") == ("", False)


def test_cfo_headline_clean_input_untouched():
    headline = "Adani Power margin at risk: ₹180 Cr exposure, Q2 action required"
    clean, modified = sanitise_cfo_headline(headline)
    assert modified is False
    assert clean == headline


# ---------------------------------------------------------------------------
# Framework rationale injection
# ---------------------------------------------------------------------------


def test_framework_rationale_added():
    insight = {"impact_analysis": {"compliance": "GRI:207 is triggered"}}
    lookup = {"GRI:207": "Tax transparency failure → regulatory penalty"}
    out, added = inject_framework_rationales(insight, lookup)
    assert added == 1
    assert "Tax transparency" in out["impact_analysis"]["compliance"]


def test_framework_rationale_skip_when_already_present():
    insight = {
        "impact_analysis": {
            "compliance": "GRI:207 (rationale: already explained) triggered"
        }
    }
    lookup = {"GRI:207": "some other rationale"}
    out, added = inject_framework_rationales(insight, lookup)
    assert added == 0  # already has rationale


def test_framework_rationale_handles_no_lookup():
    insight = {"x": "GRI:207 triggered"}
    out, added = inject_framework_rationales(insight, None)
    assert added == 0
    assert out == insight


# ---------------------------------------------------------------------------
# ROI cap flagging
# ---------------------------------------------------------------------------


def test_roi_cap_flagged_when_at_ceiling():
    rec = {"type": "compliance", "roi_percentage": 500.0, "title": "Audit"}
    out = flag_roi_cap(rec)
    assert out["roi_capped"] is True
    assert "500%" in out["roi_cap_reason"]


def test_roi_cap_not_flagged_below_ceiling():
    rec = {"type": "compliance", "roi_percentage": 300.0}
    out = flag_roi_cap(rec)
    assert "roi_capped" not in out or out["roi_capped"] is False


def test_roi_cap_noop_when_no_roi():
    rec = {"type": "operational", "roi_percentage": None}
    out = flag_roi_cap(rec)
    assert out == rec


def test_roi_cap_bulk_counts():
    recs = [
        {"type": "compliance", "roi_percentage": 500.0},  # capped
        {"type": "financial", "roi_percentage": 150.0},   # not capped
        {"type": "operational", "roi_percentage": 200.0}, # capped
    ]
    out, count = flag_roi_caps_bulk(recs)
    assert count == 2


# ---------------------------------------------------------------------------
# verify_and_correct top-level orchestration
# ---------------------------------------------------------------------------


def test_verify_and_correct_runs_all_checks():
    insight = {
        "financial_timeline": {
            "immediate": {
                "headline": "β compression of ₹33.8 Cr",
                "margin_pressure": "4.4 bps pressure on ₹33.8 Cr event",
            }
        },
        "decision_summary": {"financial_exposure": "₹180 Cr at risk"},
        "perspectives": {
            "cfo": {"headline": "BRSR:P6 — ₹33 Cr P&L risk Δ margin"}
        },
    }
    out, report = verify_and_correct(
        insight,
        revenue_cr=45000,
        article_excerpts=["Adani Power announces ₹33.8 Cr regulatory event."],
        rationale_lookup={"BRSR:P6": "Supply chain labour audit requirements"},
    )
    # Math corrected
    assert report.math_ok is False
    # Source tags added
    assert report.source_tags_added >= 1
    # CFO headline sanitised
    assert report.headline_truncated is True
    assert "β" not in out["perspectives"]["cfo"]["headline"]
    assert "BRSR:P6" not in out["perspectives"]["cfo"]["headline"]


def test_verify_and_correct_idempotent():
    """Running twice produces same result (no double-tagging)."""
    insight = {"decision_summary": {"financial_exposure": "₹180 Cr"}}
    out1, _ = verify_and_correct(insight, revenue_cr=1000)
    out2, _ = verify_and_correct(out1, revenue_cr=1000)
    assert out1 == out2


# ---------------------------------------------------------------------------
# Precedent query (requires loaded ontology)
# ---------------------------------------------------------------------------


def test_precedents_returned_for_known_event():
    from engine.ontology.graph import reset_graph
    from engine.ontology.intelligence import query_precedents_for_event

    reset_graph()  # force full graph load including precedents.ttl
    precedents = query_precedents_for_event(
        "event_social_violation", industry="Power/Energy", limit=3
    )
    assert len(precedents) >= 1
    # Vedanta Konkola should be in results (exact event+industry match)
    vedanta = [p for p in precedents if "Vedanta" in p.company]
    assert vedanta, f"expected Vedanta in results, got {[p.company for p in precedents]}"


def test_precedents_citation_format():
    from engine.ontology.intelligence import PrecedentCase

    p = PrecedentCase(
        name="Test Case",
        company="TestCo",
        date="2020-03-01",
        jurisdiction="India",
        cost_cr=100.0,
        duration_months=12.0,
        outcome="Stock -40%; ratings downgrade; 6-month spread widening",
        recovery_path="New CEO, audit, 18 month recovery",
        source="Test",
        event_type="event_test",
        industry="Test/Industry",
    )
    citation = p.as_citation()
    assert "TestCo" in citation
    assert "2020" in citation
    assert "₹100 Cr" in citation
    assert "12m" in citation
