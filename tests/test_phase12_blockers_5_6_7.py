"""Phase 12 blockers 5, 6, 7 — regression tests.

Discovered during the 3-article stress test on 2026-04-24:

  #5 Canonical exposure drift — different sections quoted different ₹
     figures for the same event (headline ₹477.5 Cr, ESG Analyst ₹14.4 Cr,
     margin impact ₹33.4 Cr). Not a hallucination, but it undermines credibility.
     Fix: `verify_cross_section_consistency` checker.

  #6 Precedent anchoring — the CEO-narrative system prompt shipped a
     hardcoded "Vedanta Konkola Child Labour" example, so the LLM anchored
     on it even for unrelated events. Fix: replaced with generic placeholders
     + explicit "NONE AVAILABLE → set null" guidance in user prompt.

  #7 Hallucination in source tags — the LLM wrote
     "₹353.6 Cr direct revenue hit (from article)" for the Waaree
     anti-dumping article even though the article body contained ZERO ₹
     figures. Fix: `audit_source_tags` independently verifies every
     (from article) tag and downgrades unsupported claims to (engine
     estimate).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Blocker #7 — Hallucination detector
# ---------------------------------------------------------------------------


def test_hallucinated_from_article_tag_is_downgraded() -> None:
    """₹ figure tagged (from article) but the article contains NO ₹ figures
    at all — the auditor must downgrade to (engine estimate)."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {
        "decision_summary": {
            "financial_exposure": (
                "₹353.6 Cr direct revenue hit (from article) + "
                "₹250.7 Cr indirect contingent exposure (engine estimate)"
            ),
            "key_risk": "₹353.6 Cr direct revenue loss (from article)",
        },
    }
    excerpts = [
        "Waaree Energies and Other Solar Stocks Fall by up to 5% Today",
        "US imposed preliminary anti-dumping duties on Indian solar imports",
    ]
    audited, count = audit_source_tags(insight, excerpts)
    assert count >= 2, f"Expected ≥2 downgrades, got {count}"
    # The hallucinated claim is now tagged (engine estimate)
    assert "(from article)" not in audited["decision_summary"]["financial_exposure"].split("+")[0]
    assert "(engine estimate)" in audited["decision_summary"]["key_risk"]


def test_real_article_figure_stays_tagged_from_article() -> None:
    """The ICICI ₹45,000 Cr FII outflow figure IS in the real article
    ("Rs 45,000 crore") — the auditor must NOT downgrade it."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {
        "decision_summary": {"financial_exposure": "₹45,000 Cr FII outflow (from article)"},
    }
    excerpts = ["ICICI outflows of nearly Rs 45,000 crore in Q4"]
    audited, count = audit_source_tags(insight, excerpts)
    assert count == 0, f"Expected 0 downgrades (figure is real), got {count}"
    assert "(from article)" in audited["decision_summary"]["financial_exposure"]


def test_idfc_bottom_line_figure_stays_tagged() -> None:
    """IDFC ₹503 Cr bottom-line IS in the article — must stay tagged."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {
        "decision_summary": {"financial_exposure": "₹503 Cr Q3 profit (from article)"},
    }
    excerpts = ["The bank reported a bottom-line of Rs 503 crore against Rs 339 crore"]
    audited, count = audit_source_tags(insight, excerpts)
    assert count == 0
    assert "(from article)" in audited["decision_summary"]["financial_exposure"]


def test_sebi_penalty_figure_with_rupee_symbol_stays_tagged() -> None:
    """SEBI ₹50 Cr penalty with ₹ symbol — ₹ / Rs / INR variants must all match."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {"decision_summary": {"key_risk": "SEBI imposed ₹50 Cr penalty (from article)"}}
    excerpts = ["SEBI today imposed a ₹50 crore penalty on the company."]
    audited, count = audit_source_tags(insight, excerpts)
    assert count == 0


def test_no_article_excerpts_downgrades_everything() -> None:
    """When article_excerpts is empty/None, every (from article) claim is
    by definition unsupported — downgrade all."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {"decision_summary": {"financial_exposure": "₹500 Cr exposure (from article)"}}
    audited, count = audit_source_tags(insight, [])
    assert count == 1


def test_audit_preserves_engine_estimate_tags() -> None:
    """Tags that are already (engine estimate) must not be touched."""
    from engine.analysis.output_verifier import audit_source_tags

    insight = {"decision_summary": {"financial_exposure": "₹500 Cr exposure (engine estimate)"}}
    audited, count = audit_source_tags(insight, [])
    assert count == 0
    assert "(engine estimate)" in audited["decision_summary"]["financial_exposure"]


# ---------------------------------------------------------------------------
# Blocker #5 — Cross-section canonical exposure
# ---------------------------------------------------------------------------


def test_cross_section_consistency_flags_large_drift() -> None:
    """Headline says ₹477.5 Cr, ESG section says ₹14.4 Cr — a 30×
    discrepancy. The verifier must emit a drift warning."""
    from engine.analysis.output_verifier import verify_cross_section_consistency

    insight = {
        "headline": "Waaree secures auction win, adding ₹477.5 Cr revenue",
        "decision_summary": {
            "financial_exposure": "₹14.4 Cr direct revenue uplift",
            "key_risk": "Minimal risk at ₹5 Cr disclosure cost",
            "top_opportunity": "Green bond at ₹500 Cr",
        },
        "net_impact_summary": "Net impact ₹14.4 Cr revenue gain with margin uplift.",
    }
    canonical, warnings = verify_cross_section_consistency(insight, tolerance_pct=0.35)
    assert canonical == 500.0  # biggest figure is ₹500 Cr green bond
    # Should flag both the ₹14.4 Cr readings as drifted from canonical
    drift_count = sum(1 for w in warnings if "cross-section ₹ drift" in w)
    assert drift_count >= 2, f"Expected ≥2 drift warnings, got: {warnings}"


def test_cross_section_consistency_clean_when_figures_align() -> None:
    """All sections cite ₹500 Cr — no drift warning."""
    from engine.analysis.output_verifier import verify_cross_section_consistency

    insight = {
        "headline": "Event triggers ₹500 Cr exposure",
        "decision_summary": {
            "financial_exposure": "₹500 Cr direct impact",
            "key_risk": "₹500 Cr risk",
            "top_opportunity": "",
        },
    }
    canonical, warnings = verify_cross_section_consistency(insight)
    assert canonical == 500.0
    assert len(warnings) == 0


def test_cross_section_skips_when_only_one_field_has_figures() -> None:
    """With only one field carrying ₹ figures, there's nothing to compare."""
    from engine.analysis.output_verifier import verify_cross_section_consistency

    insight = {"headline": "Event with ₹100 Cr impact"}
    canonical, warnings = verify_cross_section_consistency(insight)
    assert canonical == 100.0
    assert len(warnings) == 0


def test_extract_all_cr_amounts_captures_multiple_figures() -> None:
    from engine.analysis.output_verifier import _extract_all_cr_amounts

    text = "₹477.5 Cr revenue + ₹33.4 Cr margin + ₹500 Cr green bond, plus 250 crore reserve"
    figures = _extract_all_cr_amounts(text)
    assert set(figures) >= {477.5, 33.4, 500.0, 250.0}


# ---------------------------------------------------------------------------
# Blocker #6 — Precedent matching / anchoring
# ---------------------------------------------------------------------------


def test_ceo_prompt_no_longer_hardcodes_vedanta_konkola() -> None:
    """The system prompt shipped a named default ("Vedanta Konkola Child
    Labour NGO Escalation") that the LLM anchored on. Must be replaced with
    generic placeholders."""
    from engine.analysis.ceo_narrative_generator import _SYSTEM_PROMPT as SYSTEM_PROMPT

    assert "Vedanta Konkola" not in SYSTEM_PROMPT, (
        "System prompt still contains hardcoded 'Vedanta Konkola' example — "
        "LLM will anchor on it and cite the same precedent for unrelated events."
    )
    assert "Infosys 2017" not in SYSTEM_PROMPT
    # Must tell the LLM what to do when no precedent is available
    assert "null" in SYSTEM_PROMPT.lower()


def test_ceo_prompt_uses_generic_placeholders() -> None:
    """Must use `<...>` style placeholders, not named cases."""
    from engine.analysis.ceo_narrative_generator import _SYSTEM_PROMPT as SYSTEM_PROMPT

    assert "<name from PRECEDENTS block>" in SYSTEM_PROMPT
    assert "<company from PRECEDENTS block>" in SYSTEM_PROMPT
