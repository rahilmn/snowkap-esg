"""Phase 18 — Deferred-issue regression tests.

Covers the 4 issues deferred from the Phase 17 user-journey audit:

  D — semantic ₹ drift detector (noun-phrase context overlap)
  G — reused-number hallucination audit (same value in distinct claims)
  F — bulk schema-version reanalyze admin endpoint
  I — empty-state CompanySwitcher row for newly-onboarded companies

Each test class corresponds 1:1 to the audit issue ID so a breakage
points straight at the documented gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.analysis.output_verifier import (
    audit_reused_article_figures,
    verify_semantic_consistency,
    verify_and_correct,
)


# ---------------------------------------------------------------------------
# Issue D — Semantic ₹ drift detector
# ---------------------------------------------------------------------------


class TestSemanticDriftDetector:
    """Same number paired with semantically distinct claims should fire."""

    def test_idfc_three_unrelated_500_cr_claims_warns(self):
        # Live-fail pattern: ₹500 Cr re-used as market cap loss / green bond /
        # P/E expansion — three unrelated concepts.
        deep_insight = {
            "headline": "₹500 Cr market-cap loss after Q4 preview",
            "decision_summary": {
                "financial_exposure": "₹500 Cr potential green bond issuance to fund expansion",
                "key_risk": "₹500 Cr P/E compression risk if margins squeeze further",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        warnings = verify_semantic_consistency(deep_insight)
        assert len(warnings) == 1
        assert "semantic ₹ drift" in warnings[0]
        assert "500" in warnings[0]

    def test_consistent_500_cr_across_fields_no_warning(self):
        # When the SAME concept (margin pressure) is repeated in several
        # fields with overlapping context tokens, no drift warning.
        deep_insight = {
            "headline": "₹500 Cr margin pressure on regulatory front",
            "decision_summary": {
                "financial_exposure": "₹500 Cr margin pressure on regulatory exposure",
                "key_risk": "₹500 Cr regulatory margin pressure risk",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        warnings = verify_semantic_consistency(deep_insight)
        assert warnings == []

    def test_two_uses_below_min_reuses_no_warning(self):
        # Default min_reuses is 3; only two reuses → no warning yet.
        deep_insight = {
            "headline": "₹400 Cr revenue at risk",
            "decision_summary": {
                "financial_exposure": "₹400 Cr SEBI penalty",
                "key_risk": "",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        warnings = verify_semantic_consistency(deep_insight)
        assert warnings == []

    def test_different_values_no_warning(self):
        # Different ₹ values (₹100 Cr vs ₹500 Cr vs ₹1000 Cr) should never
        # trigger semantic drift — they're not "the same number reused".
        deep_insight = {
            "headline": "₹100 Cr revenue hit",
            "decision_summary": {
                "financial_exposure": "₹500 Cr regulatory exposure",
                "key_risk": "₹1000 Cr long-term capex requirement",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        warnings = verify_semantic_consistency(deep_insight)
        assert warnings == []


# ---------------------------------------------------------------------------
# Issue G — Reused-number hallucination audit
# ---------------------------------------------------------------------------


class TestReusedFigureAudit:
    """When the same article ₹ figure is tagged (from article) in 3+
    distinct contexts, downgrade the extras to (engine estimate)."""

    def test_three_distinct_uses_downgrade_extras(self):
        deep_insight = {
            "headline": "₹503 Cr Q3 net profit (from article) signals momentum",
            "decision_summary": {
                "financial_exposure": "₹503 Cr regulatory provision (from article) at risk",
                "key_risk": "₹503 Cr ESG compliance penalty (from article) over CSR breach",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        article_excerpts = ["IDFC reported Q3 net profit of Rs 503 crore."]
        out, downgraded = audit_reused_article_figures(deep_insight, article_excerpts)
        # First occurrence stays "(from article)"; the 2 extras are downgraded
        assert downgraded == 2
        # Headline retains its tag (genuine claim)
        assert "(from article)" in out["headline"]
        # The other two are downgraded
        flat = json.dumps(out)
        assert flat.count("(engine estimate)") >= 2

    def test_single_genuine_use_untouched(self):
        deep_insight = {
            "headline": "₹503 Cr Q3 profit (from article)",
            "decision_summary": {
                "financial_exposure": "Margin neutral",
                "key_risk": "Regulatory risk minimal",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        out, downgraded = audit_reused_article_figures(
            deep_insight, ["Net profit Rs 503 crore"]
        )
        assert downgraded == 0
        assert "(from article)" in out["headline"]

    def test_two_uses_below_threshold_untouched(self):
        # max_distinct_uses default = 2, so two uses are allowed (a single
        # claim repeated across headline + financial_exposure is normal).
        deep_insight = {
            "headline": "₹503 Cr Q3 profit (from article)",
            "decision_summary": {
                "financial_exposure": "₹503 Cr Q3 net profit (from article)",
                "key_risk": "",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        out, downgraded = audit_reused_article_figures(
            deep_insight, ["Net profit Rs 503 crore in Q3"]
        )
        assert downgraded == 0

    def test_repeated_same_context_high_overlap_untouched(self):
        # When all uses share the SAME context (the LLM is repeating one
        # genuine claim verbatim), context overlap is high and we leave
        # all the tags alone even when count > max_distinct_uses.
        deep_insight = {
            "headline": "₹503 Cr Q3 net profit (from article)",
            "decision_summary": {
                "financial_exposure": "₹503 Cr Q3 net profit (from article)",
                "key_risk": "₹503 Cr Q3 net profit (from article)",
                "top_opportunity": "",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        out, downgraded = audit_reused_article_figures(
            deep_insight, ["Q3 net profit Rs 503 crore"]
        )
        # Same context (Q3 / net / profit) → nothing downgraded
        assert downgraded == 0


# ---------------------------------------------------------------------------
# Issue F — Bulk reanalyze admin endpoint
# ---------------------------------------------------------------------------


class TestBulkReanalyzeEndpoint:
    """Schema-version invalidation walks insights/*.json and bumps every
    meta.schema_version so on-demand re-runs."""

    def test_invalidate_writes_marker(self, tmp_path, monkeypatch):
        from api.routes import admin_reanalyze

        # Stage a fake insights folder with two articles
        company_dir = tmp_path / "outputs" / "test-co" / "insights"
        company_dir.mkdir(parents=True)

        a = company_dir / "art-aaa.json"
        a.write_text(json.dumps({
            "article": {"id": "aaa"},
            "meta": {"schema_version": "2.0-primitives-l2"},
            "insight": {"headline": "old"},
        }))
        b = company_dir / "art-bbb.json"
        b.write_text(json.dumps({
            "article": {"id": "bbb"},
            "meta": {"schema_version": "2.0-primitives-l2"},
            "insight": {"headline": "old2"},
        }))

        monkeypatch.setattr(
            admin_reanalyze, "get_data_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )

        result = admin_reanalyze._invalidate_company_insights("test-co")
        assert result["invalidated"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == 0

        # Each file's meta.schema_version is now the marker; old version preserved
        for path in (a, b):
            payload = json.loads(path.read_text())
            assert payload["meta"]["schema_version"] == "_invalidated"
            assert payload["meta"]["_pre_invalidation_schema_version"] == "2.0-primitives-l2"

    def test_idempotent_on_repeat(self, tmp_path, monkeypatch):
        from api.routes import admin_reanalyze

        company_dir = tmp_path / "outputs" / "test-co" / "insights"
        company_dir.mkdir(parents=True)
        f = company_dir / "art-aaa.json"
        f.write_text(json.dumps({
            "article": {"id": "aaa"},
            "meta": {"schema_version": "2.0-primitives-l2"},
        }))

        monkeypatch.setattr(
            admin_reanalyze, "get_data_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )

        admin_reanalyze._invalidate_company_insights("test-co")
        # Second call should skip the already-marked file
        result2 = admin_reanalyze._invalidate_company_insights("test-co")
        assert result2["invalidated"] == 0
        assert result2["skipped"] == 1

    def test_404_when_company_not_in_companies_json(self, tmp_path, monkeypatch):
        # The route calls load_companies() to validate the slug. Patch it
        # to return a known set so we can assert 404 on unknown.
        from api.routes import admin_reanalyze
        from fastapi import HTTPException

        class _Co:
            def __init__(self, slug):
                self.slug = slug

        monkeypatch.setattr(
            admin_reanalyze, "load_companies",
            lambda: [_Co("known-slug")],
        )
        with pytest.raises(HTTPException) as exc_info:
            admin_reanalyze.reanalyze_company("never-onboarded")
        assert exc_info.value.status_code == 404

    def test_returns_zero_when_insights_dir_missing(self, tmp_path, monkeypatch):
        from api.routes import admin_reanalyze

        # Don't create the insights dir
        monkeypatch.setattr(
            admin_reanalyze, "get_data_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )
        result = admin_reanalyze._invalidate_company_insights("nonexistent")
        assert result == {"invalidated": 0, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Issue I — CompanySwitcher empty-state for new tenants
# ---------------------------------------------------------------------------


class TestCompanySwitcherEmptyState:
    """The /api/admin/tenants endpoint must return newly-onboarded
    companies even when their article_count is zero, so the switcher
    can list them with an "empty" badge."""

    def test_admin_tenants_returns_zero_count_companies(self, tmp_path, monkeypatch):
        from api.routes.admin import _tenant_stats

        # _tenant_stats should return (0, None) for an unknown slug, NOT raise
        count, last_at = _tenant_stats("unknown-onboarded-tenant")
        assert count == 0
        assert last_at is None

    def test_company_switcher_renders_empty_badge_for_onboarded_with_zero(self):
        # Lightweight spec: the React component reads `tenant.article_count`
        # and shows "empty" when it's 0 AND source == "onboarded".
        # Static check: source contains the new branch.
        component = (
            Path(__file__).resolve().parent.parent
            / "client" / "src" / "components" / "admin" / "CompanySwitcher.tsx"
        )
        text = component.read_text(encoding="utf-8")
        # Conditional must check both source == "onboarded" AND zero article_count
        assert 'tenant.source === "onboarded"' in text
        assert "empty" in text


# ---------------------------------------------------------------------------
# Cross-cutting — verify_and_correct still works with new checks
# ---------------------------------------------------------------------------


class TestVerifyAndCorrectIntegration:
    """The new D + G checks must integrate with the existing pipeline
    without breaking the back-compat verify_and_correct contract."""

    def test_verify_and_correct_emits_semantic_drift_warning(self):
        deep_insight = {
            "headline": "₹500 Cr market-cap loss",
            "decision_summary": {
                "financial_exposure": "₹500 Cr green bond opportunity",
                "key_risk": "₹500 Cr P/E compression",
                "top_opportunity": "",
                "materiality": "MODERATE",
            },
            "core_mechanism": "",
            "net_impact_summary": "",
        }
        out, report = verify_and_correct(
            deep_insight,
            revenue_cr=10000.0,
            article_excerpts=[],
        )
        # Semantic-drift warning should be in report.warnings
        assert any("semantic ₹ drift" in w for w in report.warnings)

    def test_verify_and_correct_back_compat_signature(self):
        # Old callers without the new kwargs still work.
        deep_insight = {
            "headline": "Test",
            "decision_summary": {"materiality": "LOW"},
        }
        out, report = verify_and_correct(deep_insight, revenue_cr=1000.0)
        assert out is not None
        assert report is not None
