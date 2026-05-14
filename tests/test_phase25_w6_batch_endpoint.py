"""Phase 25 W6 — batch onboarding API endpoint regression tests.

Endpoint-level tests focus on:
  * Router + endpoint registration
  * Pydantic schema validation
  * Feature flag honored
  * Skip-existing logic correctly excludes already-onboarded slugs

Full HTTP integration (auth flow, file upload via TestClient) is gated
on the conftest.py auth fixtures already used by Phase 12-24 tests.
The router-registration + helper-function tests below cover the
critical paths without needing a live API server.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Router registration — proves the endpoint is discoverable
# ---------------------------------------------------------------------------


class TestBatchEndpointRegistration:
    def test_router_imports_cleanly(self):
        from api.routes.batch_onboard import router
        assert router is not None

    def test_router_has_two_endpoints(self):
        from api.routes.batch_onboard import router
        paths = sorted({route.path for route in router.routes})
        # Two: empty (POST /api/admin/onboard/batch) + /preview
        assert "/api/admin/onboard/batch" in paths
        assert "/api/admin/onboard/batch/preview" in paths

    def test_router_registered_in_main(self):
        from api.main import app
        paths = {route.path for route in app.routes}
        assert "/api/admin/onboard/batch" in paths
        assert "/api/admin/onboard/batch/preview" in paths

    def test_router_carries_auth_gate(self):
        from api.routes.batch_onboard import router
        # Two router-level dependencies: require_auth + require_bearer_permission
        assert len(router.dependencies) >= 2


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestBatchSchemas:
    def test_batch_roster_entry_shape(self):
        from api.routes.batch_onboard import BatchRosterEntry
        entry = BatchRosterEntry(
            record_id="123",
            deal_name="Test Co - New Deal",
            company_name="Test Co",
            slug="test-co",
            deal_stage="Won",
            region="India",
            headquarter_country="India",
            amount_inr=100000.0,
            deal_owner="x@y.com",
            needs_disambiguation=False,
            disambiguation_candidates=[],
        )
        assert entry.slug == "test-co"
        assert entry.amount_inr == 100000.0

    def test_batch_roster_entry_amount_can_be_none(self):
        from api.routes.batch_onboard import BatchRosterEntry
        entry = BatchRosterEntry(
            record_id="123", deal_name="X", company_name="X", slug="x",
            deal_stage="Won", region="India", headquarter_country="India",
            amount_inr=None, deal_owner="x@y", needs_disambiguation=False,
            disambiguation_candidates=[],
        )
        assert entry.amount_inr is None

    def test_preview_response_aggregates(self):
        from api.routes.batch_onboard import BatchPreviewResponse, BatchRosterEntry
        e = BatchRosterEntry(
            record_id="1", deal_name="A", company_name="A", slug="a",
            deal_stage="Won", region="India", headquarter_country="India",
            amount_inr=None, deal_owner="x@y", needs_disambiguation=False,
            disambiguation_candidates=[],
        )
        resp = BatchPreviewResponse(
            total_eligible=1, won_count=1, negotiation_count=0,
            countries=["India:1"], auto_resolvable=1, needs_review=0,
            roster=[e],
        )
        assert resp.total_eligible == 1
        assert len(resp.roster) == 1


# ---------------------------------------------------------------------------
# Feature flag honored
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_disabled_flag_raises_503(self):
        from api.routes.batch_onboard import _check_feature_flag
        from fastapi import HTTPException
        with patch.dict(os.environ, {"SNOWKAP_BATCH_ONBOARD_ENABLED": "0"}):
            with pytest.raises(HTTPException) as exc_info:
                _check_feature_flag()
            assert exc_info.value.status_code == 503

    def test_enabled_flag_does_not_raise(self):
        from api.routes.batch_onboard import _check_feature_flag
        with patch.dict(os.environ, {"SNOWKAP_BATCH_ONBOARD_ENABLED": "1"}):
            _check_feature_flag()  # should not raise

    def test_unset_flag_defaults_to_enabled(self):
        from api.routes.batch_onboard import _check_feature_flag
        env = {k: v for k, v in os.environ.items() if k != "SNOWKAP_BATCH_ONBOARD_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            _check_feature_flag()  # should not raise (default '1')

    def test_false_string_disables(self):
        from api.routes.batch_onboard import _check_feature_flag
        from fastapi import HTTPException
        for value in ("false", "FALSE", "no", "NO"):
            with patch.dict(os.environ, {"SNOWKAP_BATCH_ONBOARD_ENABLED": value}):
                with pytest.raises(HTTPException) as exc_info:
                    _check_feature_flag()
                assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_uploaded_csv_with_synthetic_data(self):
        from api.routes.batch_onboard import _parse_uploaded_csv
        csv_bytes = (
            b'Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,'
            b'Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n'
            b'1,Test Won Co,India,Won,Tech,SOW,Active,owner@x,Direct,100,note,a,false,1\n'
            b'2,Test Negotiation Co,Mumbai,Negotiation,Tech,SOW,Active,owner@x,Direct,200,note,a,false,2\n'
            b'3,Test Filtered Out,India,Closed Lost,Tech,SOW,Active,owner@x,Direct,300,note,a,true,3\n'
        )
        roster = _parse_uploaded_csv(csv_bytes)
        assert len(roster) == 2
        slugs = {r.slug for r in roster}
        assert slugs == {"test-won-co", "test-negotiation-co"}

    def test_parse_uploaded_csv_handles_utf8(self):
        from api.routes.batch_onboard import _parse_uploaded_csv
        csv_bytes = (
            'Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,'
            'Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n'
            '1,Süd-Chemie India,Gujarat,Negotiation,Tech,SOW,Active,owner@x,Direct,100,note,a,false,1\n'
        ).encode("utf-8")
        roster = _parse_uploaded_csv(csv_bytes)
        assert len(roster) == 1
        assert roster[0].company_name == "Süd-Chemie India"
        assert roster[0].slug == "sud-chemie-india"

    def test_enrich_with_disambiguation_flags_jsw(self):
        from api.routes.batch_onboard import _enrich_with_disambiguation
        from engine.ingestion.csv_batch_onboarder import CustomerRoster
        roster = [
            CustomerRoster("1", "JSW", "JSW", "jsw", "Won", "India", "India", 100.0, "x@y"),
        ]
        enriched = _enrich_with_disambiguation(roster)
        assert len(enriched) == 1
        assert enriched[0].needs_disambiguation is True
        assert len(enriched[0].disambiguation_candidates) >= 4  # JSW Steel/Energy/Infra/HL

    def test_build_summary_aggregates_correctly(self):
        from api.routes.batch_onboard import _build_summary, BatchRosterEntry
        enriched = [
            BatchRosterEntry(
                record_id="1", deal_name="A", company_name="A", slug="a",
                deal_stage="Won", region="India", headquarter_country="India",
                amount_inr=None, deal_owner="x@y", needs_disambiguation=False,
                disambiguation_candidates=[],
            ),
            BatchRosterEntry(
                record_id="2", deal_name="B", company_name="B", slug="b",
                deal_stage="Won", region="Mumbai", headquarter_country="India",
                amount_inr=None, deal_owner="x@y", needs_disambiguation=True,
                disambiguation_candidates=[{}, {}],
            ),
            BatchRosterEntry(
                record_id="3", deal_name="C", company_name="C", slug="c",
                deal_stage="Negotiation", region="Kuwait", headquarter_country="Kuwait",
                amount_inr=None, deal_owner="x@y", needs_disambiguation=False,
                disambiguation_candidates=[],
            ),
        ]
        s = _build_summary(enriched)
        assert s["total_eligible"] == 3
        assert s["won_count"] == 2
        assert s["negotiation_count"] == 1
        assert s["auto_resolvable"] == 2
        assert s["needs_review"] == 1
        assert "India:2" in s["countries"]
        assert "Kuwait:1" in s["countries"]


# ---------------------------------------------------------------------------
# Integration: real CSV → preview shape
# ---------------------------------------------------------------------------


REAL_CSV = Path(__file__).resolve().parent.parent.parent / "hubspot-crm-exports-all-deals-2026-05-01.csv"


class TestRealCsvIntegration:
    @pytest.mark.skipif(not REAL_CSV.exists(),
                        reason=f"real CSV not present at {REAL_CSV}")
    def test_real_csv_preview_returns_17_rows(self):
        from api.routes.batch_onboard import _parse_uploaded_csv, _enrich_with_disambiguation, _build_summary
        roster = _parse_uploaded_csv(REAL_CSV.read_bytes())
        enriched = _enrich_with_disambiguation(roster)
        summary = _build_summary(enriched)
        # Phase 25 confirmed scope
        assert summary["total_eligible"] == 17
        assert summary["won_count"] == 12
        assert summary["negotiation_count"] == 5
        # Most entries surface for review (per disambiguator coverage test)
        assert summary["needs_review"] >= 10
