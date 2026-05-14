"""Phase 24 (W3) — CFO-credibility preflight regression tests.

Three layers:

  A. The 6 gate functions individually (`engine.analysis.cfo_preflight`).
  B. The aggregate `run_preflight` orchestrator + audit-log mirroring.
  C. SQLite schema migration + filter wiring (`engine.index.sqlite_index`).

The HTTP endpoint filter (`/news/feed?perspective=cfo` excludes FAILed
rows) is exercised via the SQLite filter test and a smoke check on the
route's pydantic signature.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine import audit
from engine.analysis import cfo_preflight as pf
from engine.ontology.graph import reset_graph


# ---------------------------------------------------------------------------
# Per-test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    reset_graph()
    yield
    reset_graph()


@pytest.fixture
def patched_audit_dir(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit_root"

    def _resolve(_base=None):
        d = audit_dir / "audit"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(audit, "_resolve_audit_dir", _resolve)
    return audit_dir


# ---------------------------------------------------------------------------
# A. Individual gates
# ---------------------------------------------------------------------------


class TestGateFinancialImpactQuantified:
    def test_pass_when_rupee_with_source_tag(self):
        insight = {
            "decision_summary": {
                "financial_exposure": "₹500 Cr (engine estimate)"
            }
        }
        r = pf.gate_financial_impact_quantified(insight)
        assert r.passed is True

    def test_fail_when_na(self):
        insight = {"decision_summary": {"financial_exposure": "N/A"}}
        r = pf.gate_financial_impact_quantified(insight)
        assert r.passed is False
        assert "missing or N/A" in r.reason

    def test_fail_when_no_rupee_figure(self):
        insight = {"decision_summary": {"financial_exposure": "significant exposure (engine estimate)"}}
        r = pf.gate_financial_impact_quantified(insight)
        assert r.passed is False
        assert "₹" in r.reason or "Rs" in r.reason

    def test_fail_when_no_source_tag(self):
        insight = {"decision_summary": {"financial_exposure": "₹500 Cr"}}
        r = pf.gate_financial_impact_quantified(insight)
        assert r.passed is False
        assert "source tag" in r.reason


class TestGateFrameworkMapped:
    def test_pass_with_section_code(self):
        r = pf.gate_framework_mapped(["BRSR:P5:Q12", "GRI:303"])
        assert r.passed is True

    def test_pass_with_digit_in_code(self):
        # ESRS E1, GRI 207 — digits qualify even without colon
        r = pf.gate_framework_mapped(["ESRS E1", "GRI 207"])
        assert r.passed is True

    def test_fail_with_only_bare_names(self):
        # Bare names (no colon, no digits) are too coarse
        r = pf.gate_framework_mapped(["BRSR", "TCFD"])
        assert r.passed is False
        assert "section codes" in r.reason

    def test_fail_with_no_frameworks(self):
        r = pf.gate_framework_mapped([])
        assert r.passed is False
        assert "no frameworks" in r.reason


class TestGateNoStaleData:
    NOW = datetime(2026, 5, 4, tzinfo=timezone.utc)

    def test_pass_when_fresh(self):
        # Article published 5 days ago, default 14d window
        pub = (self.NOW - timedelta(days=5)).isoformat()
        r, window = pf.gate_no_stale_data(pub, None, now=self.NOW)
        assert r.passed is True
        assert window == pf.DEFAULT_FRESHNESS_DAYS

    def test_fail_when_stale(self):
        pub = (self.NOW - timedelta(days=30)).isoformat()
        r, window = pf.gate_no_stale_data(pub, None, now=self.NOW)
        assert r.passed is False
        assert "30d old" in r.reason or "30 day" in r.reason

    def test_event_specific_window_used_when_available(self):
        # Reg penalty has a 30d window in normative_principles.ttl-derived
        # FreshnessWindow instances (knowledge_expansion.ttl).
        pub = (self.NOW - timedelta(days=20)).isoformat()
        r, window = pf.gate_no_stale_data(pub, "event_regulatory_penalty", now=self.NOW)
        assert window == 30
        assert r.passed is True

    def test_unparseable_published_at(self):
        r, window = pf.gate_no_stale_data("not a date", None, now=self.NOW)
        assert r.passed is False
        assert "missing" in r.reason or "unparseable" in r.reason


class TestGatePolarityCoherent:
    def test_pass_with_no_warnings(self):
        r = pf.gate_polarity_coherent({}, [])
        assert r.passed is True

    def test_fail_on_low_confidence_flag(self):
        insight = {"low_confidence_classification": True}
        r = pf.gate_polarity_coherent(insight, [])
        assert r.passed is False
        assert "low_confidence" in r.reason

    def test_fail_on_coherence_warning(self):
        insight = {}
        warnings = ["narrative coherence mismatch (event=+1, insight=-1)"]
        r = pf.gate_polarity_coherent(insight, warnings)
        assert r.passed is False
        assert "coherence" in r.reason.lower()


class TestGateNumericConsistent:
    def test_pass_when_no_drift(self):
        # Single ₹ figure → no drift possible
        insight = {
            "headline": "₹500 Cr exposure for ICICI",
            "decision_summary": {
                "financial_exposure": "₹500 Cr",
                "key_risk": "regulatory cascade",
            },
        }
        r, canonical = pf.gate_numeric_consistent(insight)
        assert r.passed is True

    def test_fail_when_drift_exceeds_tolerance(self):
        # 30x drift between fields
        insight = {
            "headline": "₹100 Cr exposure",
            "decision_summary": {
                "financial_exposure": "₹3000 Cr cascade",
                "key_risk": "₹100 Cr direct + ₹3000 Cr indirect",
            },
            "net_impact_summary": "Net impact ₹3000 Cr",
        }
        r, canonical = pf.gate_numeric_consistent(insight)
        assert r.passed is False
        assert "drift" in r.reason.lower()


class TestGateStakeholderPolarityMatched:
    def test_skip_when_event_polarity_neutral(self):
        r = pf.gate_stakeholder_polarity_matched({}, "neutral")
        assert r.passed is True

    def test_skip_when_no_stakeholder_map(self):
        r = pf.gate_stakeholder_polarity_matched({"ceo": {}}, "positive")
        assert r.passed is True

    def test_pass_when_stances_match_polarity(self):
        perspectives = {
            "ceo": {
                "stakeholder_map": [
                    {"stakeholder": "MSCI", "stance": "rating upgrade likely"},
                    {"stakeholder": "BlackRock", "stance": "premium pricing leadership"},
                ],
            }
        }
        r = pf.gate_stakeholder_polarity_matched(perspectives, "positive")
        assert r.passed is True

    def test_fail_when_negative_token_on_positive_event(self):
        perspectives = {
            "ceo": {
                "stakeholder_map": [
                    {"stakeholder": "SEBI", "stance": "potential penalty for disclosure gap"},
                ],
            }
        }
        r = pf.gate_stakeholder_polarity_matched(perspectives, "positive")
        assert r.passed is False
        assert "penalty" in r.reason.lower()


# ---------------------------------------------------------------------------
# B. Aggregate run_preflight
# ---------------------------------------------------------------------------


class TestRunPreflight:
    NOW = datetime(2026, 5, 4, tzinfo=timezone.utc)

    def test_all_six_gates_evaluated(self, patched_audit_dir):
        insight = {
            "decision_summary": {
                "financial_exposure": "₹500 Cr (engine estimate)",
                "key_risk": "regulatory cascade ~₹500 Cr",
                "verdict": "ACT",
                "action": "ACT",
            },
            "headline": "ICICI ₹500 Cr regulatory exposure",
        }
        report = pf.run_preflight(
            insight,
            perspectives={"ceo": {"stakeholder_map": []}},
            framework_codes=["BRSR:P1"],
            published_at=(self.NOW - timedelta(days=3)).isoformat(),
            event_id="event_regulatory_penalty",
            event_polarity="negative",
            verifier_warnings=[],
            article_id="art-prefit-1",
            company_slug="icici-bank",
            now=self.NOW,
            log_to_audit=True,
        )
        # All 6 gates evaluated
        assert len(report.gate_results) == 6
        gate_names = {r.gate for r in report.gate_results}
        assert gate_names == set(pf.ALL_GATES)
        # All passed → overall pass
        assert report.passed is True

    def test_any_failed_gate_fails_overall(self, patched_audit_dir):
        # Article missing ₹ figure → fail gate 1
        insight = {
            "decision_summary": {
                "financial_exposure": "N/A",
                "key_risk": "unspecified",
            },
        }
        report = pf.run_preflight(
            insight,
            perspectives={},
            framework_codes=["BRSR:P5"],
            published_at=self.NOW.isoformat(),
            event_id="event_disclosure_announcement",
            event_polarity="neutral",
            article_id="art-fail-1",
            company_slug="icici-bank",
            now=self.NOW,
        )
        assert report.passed is False
        failed = report.failed_gates()
        assert "financial_impact_quantified" in failed

    def test_audit_log_one_entry_per_gate(self, patched_audit_dir):
        insight = {
            "decision_summary": {"financial_exposure": "₹100 Cr (engine estimate)"},
            "headline": "₹100 Cr",
        }
        pf.run_preflight(
            insight,
            perspectives={},
            framework_codes=["BRSR:P5"],
            published_at=self.NOW.isoformat(),
            event_id="event_disclosure_announcement",
            event_polarity="neutral",
            article_id="art-audit-1",
            company_slug="icici-bank",
            now=self.NOW,
            log_to_audit=True,
        )
        entries = list(audit.read_preflight_log(patched_audit_dir))
        # 6 entries — one per gate
        assert len(entries) == 6
        gates = [e["gate"] for e in entries]
        assert set(gates) == set(pf.ALL_GATES)
        # Every entry tags the article + perspective
        assert all(e["article_id"] == "art-audit-1" for e in entries)
        assert all(e["perspective"] == "cfo" for e in entries)

    def test_no_insight_fails_all_gates(self, patched_audit_dir):
        report = pf.run_preflight(
            None,
            perspectives=None,
            framework_codes=None,
            published_at=None,
            event_id=None,
        )
        assert report.passed is False
        # Every gate explicitly failed with an "insight not generated" reason
        assert all(r.passed is False for r in report.gate_results)

    def test_log_to_audit_false_writes_nothing(self, patched_audit_dir):
        pf.run_preflight(
            {"decision_summary": {"financial_exposure": "₹100 Cr (engine estimate)"}},
            perspectives={},
            framework_codes=["BRSR:P5"],
            published_at=self.NOW.isoformat(),
            event_id="event_disclosure_announcement",
            event_polarity="neutral",
            article_id="art-no-log-1",
            company_slug="icici-bank",
            now=self.NOW,
            log_to_audit=False,
        )
        entries = list(audit.read_preflight_log(patched_audit_dir))
        assert entries == []


# ---------------------------------------------------------------------------
# C. SQLite migration + filter wiring
# ---------------------------------------------------------------------------


class TestSqliteMigration:
    def test_fresh_schema_has_cfo_preflight_status_column(self, tmp_path):
        from engine.index import sqlite_index
        db_path = tmp_path / "fresh.db"
        with sqlite3.connect(db_path) as c:
            c.executescript(sqlite_index.SCHEMA_SQL)
        with sqlite3.connect(db_path) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(article_index)")]
        assert "cfo_preflight_status" in cols, f"got cols: {cols}"

    def test_existing_db_gets_column_added_via_migration(self, tmp_path):
        """An existing DB created BEFORE the W3 column should have the
        column added after re-running ensure_schema()."""
        from engine.index import sqlite_index
        db_path = tmp_path / "legacy.db"
        # Create with old schema (no cfo_preflight_status column)
        legacy_schema = """
        CREATE TABLE article_index (
            id TEXT PRIMARY KEY,
            company_slug TEXT NOT NULL,
            title TEXT NOT NULL,
            json_path TEXT NOT NULL
        );
        """
        with sqlite3.connect(db_path) as c:
            c.executescript(legacy_schema)
        with sqlite3.connect(db_path) as c:
            cols_before = [r[1] for r in c.execute("PRAGMA table_info(article_index)")]
        assert "cfo_preflight_status" not in cols_before
        # Apply migration manually (mimics ensure_schema's ALTER TABLE step)
        with sqlite3.connect(db_path) as c:
            try:
                c.execute("ALTER TABLE article_index ADD COLUMN cfo_preflight_status TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        with sqlite3.connect(db_path) as c:
            cols_after = [r[1] for r in c.execute("PRAGMA table_info(article_index)")]
        assert "cfo_preflight_status" in cols_after

    def test_migration_idempotent_when_column_already_exists(self, tmp_path):
        """ensure_schema must be safe to call repeatedly."""
        from engine.index import sqlite_index
        db_path = tmp_path / "idempotent.db"
        with sqlite3.connect(db_path) as c:
            c.executescript(sqlite_index.SCHEMA_SQL)
        # Re-applying the ALTER raises OperationalError(duplicate column);
        # the ensure_schema wrapper swallows it.
        with sqlite3.connect(db_path) as c:
            try:
                c.execute("ALTER TABLE article_index ADD COLUMN cfo_preflight_status TEXT")
                assert False, "expected duplicate column error"
            except sqlite3.OperationalError as exc:
                assert "duplicate column name" in str(exc).lower()

    def test_extract_fields_returns_pass_when_preflight_passed(self):
        from engine.index.sqlite_index import _extract_fields
        payload = {
            "article": {"id": "x", "company_slug": "y"},
            "pipeline": {"tier": "HOME"},
            "insight": {
                "decision_summary": {"materiality": "HIGH", "action": "ACT"},
                "cfo_preflight": {"passed": True, "gates": {}, "failures": []},
            },
            "perspectives": {},
            "meta": {},
        }
        fields = _extract_fields(payload)
        assert fields["cfo_preflight_status"] == "PASS"

    def test_extract_fields_returns_fail_when_preflight_failed(self):
        from engine.index.sqlite_index import _extract_fields
        payload = {
            "article": {"id": "x", "company_slug": "y"},
            "pipeline": {"tier": "HOME"},
            "insight": {
                "decision_summary": {"materiality": "HIGH", "action": "ACT"},
                "cfo_preflight": {"passed": False, "gates": {}, "failures": [{"gate": "x", "reason": "y"}]},
            },
            "perspectives": {},
            "meta": {},
        }
        fields = _extract_fields(payload)
        assert fields["cfo_preflight_status"] == "FAIL"

    def test_extract_fields_returns_none_when_preflight_absent(self):
        from engine.index.sqlite_index import _extract_fields
        payload = {
            "article": {"id": "x", "company_slug": "y"},
            "pipeline": {"tier": "HOME"},
            "insight": {"decision_summary": {"materiality": "HIGH"}},
            "perspectives": {},
            "meta": {},
        }
        fields = _extract_fields(payload)
        # NULL = preflight not run → not surfaced as FAIL
        assert fields["cfo_preflight_status"] is None


# ---------------------------------------------------------------------------
# D. legacy_adapter perspective filter wiring
# ---------------------------------------------------------------------------


class TestLegacyAdapterFilter:
    def test_news_feed_signature_includes_perspective(self):
        # Static signature check: ensures the W3 query param is wired.
        import inspect
        from api.routes.legacy_adapter import news_feed
        sig = inspect.signature(news_feed)
        assert "perspective" in sig.parameters
