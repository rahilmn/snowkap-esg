"""Phase 25 W7 — overnight batch + article selector regression tests.

Three layers:

  A. ``engine.analysis.article_selector`` — scoring + top-N selection.
  B. ``engine.audit.append_overnight_run`` / ``read_overnight_runs`` —
     audit log writer + reader for the nightly batch.
  C. ``engine.scheduler.run_overnight_batch_job`` — orchestrator (with
     the per-tenant pipeline call mocked so we don't actually run LLM).

Critical invariant: the selector NEVER calls Stage 10 LLM (only Stage 4
relevance scoring, which is free). This is the cost lever that keeps
the overnight batch under $5/night.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stand-in for IngestedArticle so tests don't need to import the full
# news_fetcher module (which has heavy side-effects)
# ---------------------------------------------------------------------------


@dataclass
class _FakeArticle:
    id: str
    title: str
    content: str = ""
    summary: str = ""
    source: str = "google_news"
    url: str = ""
    published_at: str = "2026-05-01T00:00:00+00:00"
    company_slug: str = "test-co"


# ---------------------------------------------------------------------------
# A. article_selector — selection + scoring
# ---------------------------------------------------------------------------


class TestArticleSelector:
    def test_returns_at_most_n(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        articles = [_FakeArticle(id=str(i), title=f"Article {i} climate water carbon SEBI") for i in range(10)]
        result = select_top_n_for_pipeline(articles, n=3)
        assert len(result) == 3

    def test_returns_all_when_fewer_than_n(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        articles = [_FakeArticle(id=str(i), title=f"Article {i}") for i in range(2)]
        result = select_top_n_for_pipeline(articles, n=5)
        assert len(result) == 2  # not padded

    def test_returns_empty_for_empty_input(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        assert select_top_n_for_pipeline([], n=3) == []

    def test_high_keyword_density_ranks_higher(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        # Article with many ESG keywords should outrank a generic one
        rich = _FakeArticle(
            id="rich",
            title="SEBI fines ICICI on climate carbon GHG scope 3",
            summary="Water pollution audit BRSR disclosure compliance",
            content="emission carbon footprint scope 1 scope 2 scope 3 climate change",
        )
        sparse = _FakeArticle(id="sparse", title="Quarterly earnings call", content="")
        result = select_top_n_for_pipeline([sparse, rich], n=1)
        assert len(result) == 1
        assert result[0].id == "rich"

    def test_freshness_tiebreaker(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        # Same content + score, different dates → newer wins
        old = _FakeArticle(id="old", title="climate water", published_at="2025-01-01T00:00:00+00:00")
        new = _FakeArticle(id="new", title="climate water", published_at="2026-05-04T00:00:00+00:00")
        result = select_top_n_for_pipeline([old, new], n=1)
        assert result[0].id == "new"

    def test_handles_unparseable_published_at(self):
        from engine.analysis.article_selector import select_top_n_for_pipeline
        bad = _FakeArticle(id="bad", title="climate", published_at="not-a-date")
        good = _FakeArticle(id="good", title="climate")
        result = select_top_n_for_pipeline([good, bad], n=2)
        assert len(result) == 2  # both survive

    def test_no_llm_calls_during_selection(self):
        """Critical cost invariant: scoring uses only the heuristic
        keyword density — never calls OpenAI."""
        from engine.analysis import article_selector
        # If any LLM client got imported during selection, that's a regression
        with patch("openai.OpenAI") as mock_openai:
            articles = [_FakeArticle(id=str(i), title="climate water") for i in range(5)]
            article_selector.select_top_n_for_pipeline(articles, n=3)
            mock_openai.assert_not_called()


# ---------------------------------------------------------------------------
# B. engine.audit overnight-run writer + reader
# ---------------------------------------------------------------------------


class TestOvernightRunAudit:
    @pytest.fixture
    def patched_audit_dir(self, monkeypatch, tmp_path):
        from engine import audit
        audit_dir = tmp_path / "audit_root"

        def _resolve(_base=None):
            d = audit_dir / "audit"
            d.mkdir(parents=True, exist_ok=True)
            return d

        monkeypatch.setattr(audit, "_resolve_audit_dir", _resolve)
        return audit_dir

    def test_append_and_read_back(self, patched_audit_dir):
        from engine import audit
        audit.append_overnight_run(
            started_at="2026-05-04T01:00:00+00:00",
            completed_at="2026-05-04T03:30:00+00:00",
            tenants_attempted=17,
            tenants_succeeded=16,
            articles_fetched=340,
            articles_selected=51,
            articles_passed_preflight=38,
            total_cost_usd=3.05,
            errors=[{"tenant_slug": "jsl", "error_class": "TickerNotFound", "message": "..."}],
            extra={"workers": 4, "fetch_per_tenant": 20, "select_per_tenant": 3},
        )
        entries = list(audit.read_overnight_runs())
        assert len(entries) == 1
        e = entries[0]
        assert e["tenants_attempted"] == 17
        assert e["tenants_succeeded"] == 16
        assert e["articles_passed_preflight"] == 38
        assert e["total_cost_usd"] == 3.05
        assert e["errors"][0]["tenant_slug"] == "jsl"
        assert e["extra"]["workers"] == 4

    def test_minimal_entry_no_optional_fields(self, patched_audit_dir):
        from engine import audit
        audit.append_overnight_run(
            started_at="2026-05-04T01:00:00+00:00",
            completed_at="2026-05-04T01:05:00+00:00",
            tenants_attempted=0,
            tenants_succeeded=0,
            articles_fetched=0,
            articles_selected=0,
            articles_passed_preflight=0,
        )
        e = next(audit.read_overnight_runs())
        # Optional fields not stamped when omitted
        assert "total_cost_usd" not in e
        assert "errors" not in e
        assert "extra" not in e

    def test_missing_log_file_returns_empty(self, patched_audit_dir):
        from engine import audit
        # No write yet → no file
        assert list(audit.read_overnight_runs()) == []


# ---------------------------------------------------------------------------
# C. scheduler — overnight batch orchestration (mocked)
# ---------------------------------------------------------------------------


class TestOvernightBatchOrchestrator:
    def test_discover_batch_tenant_slugs_excludes_globals(self):
        from engine.scheduler import _discover_batch_tenant_slugs
        slugs = _discover_batch_tenant_slugs()
        # _global excluded
        assert "_global" not in slugs
        # Original 7 excluded (defensive — they may not be in tenants/ at all)
        for original in ("icici-bank", "yes-bank", "adani-power", "jsw-energy"):
            assert original not in slugs

    def test_run_overnight_batch_job_with_no_tenants(self, monkeypatch):
        """No tenant slugs → no work, but audit log still written."""
        from engine import scheduler
        from engine import audit

        # Patch audit dir to a tmp location so we don't pollute real audit log
        with patch.object(audit, "_resolve_audit_dir") as mock_resolve:
            tmp = Path("/tmp/scheduler_test_audit")
            tmp.mkdir(parents=True, exist_ok=True)
            mock_resolve.return_value = tmp

            counts = scheduler.run_overnight_batch_job(tenant_slugs=[])
            assert counts["tenants_attempted"] == 0
            assert counts["tenants_succeeded"] == 0
            assert counts["errors"] == 0

    def test_run_overnight_batch_job_with_failing_tenant_logs_error(self, monkeypatch):
        """When _process_one_tenant_overnight returns ok=False, the
        batch run should record it in errors[] without crashing."""
        from engine import scheduler

        def _fake_failing_tenant(*, slug, fetch_per_tenant, select_per_tenant):
            return {
                "ok": False, "error_class": "FakeError",
                "error_message": "simulated failure", "fetched": 0,
                "selected": 0, "passed": 0,
            }

        monkeypatch.setattr(
            scheduler, "_process_one_tenant_overnight", _fake_failing_tenant,
        )
        counts = scheduler.run_overnight_batch_job(
            tenant_slugs=["fake-slug-1", "fake-slug-2"],
            workers=2,
        )
        assert counts["tenants_attempted"] == 2
        assert counts["tenants_succeeded"] == 0
        assert counts["errors"] == 2

    def test_run_overnight_batch_job_with_succeeding_tenants(self, monkeypatch):
        from engine import scheduler

        def _fake_success_tenant(*, slug, fetch_per_tenant, select_per_tenant):
            return {
                "ok": True, "fetched": 20, "selected": 3, "passed": 2,
            }

        monkeypatch.setattr(
            scheduler, "_process_one_tenant_overnight", _fake_success_tenant,
        )
        counts = scheduler.run_overnight_batch_job(
            tenant_slugs=["a", "b", "c"], workers=3,
        )
        assert counts["tenants_attempted"] == 3
        assert counts["tenants_succeeded"] == 3
        assert counts["articles_fetched"] == 60
        assert counts["articles_selected"] == 9
        assert counts["articles_passed_preflight"] == 6
        assert counts["errors"] == 0

    def test_cli_flag_overnight_batch_recognised(self):
        """The --overnight-batch flag triggers the W7 path. Static check
        against the argparse signature so a future refactor doesn't
        silently drop the CLI."""
        import inspect
        from engine.scheduler import main
        src = inspect.getsource(main)
        assert "--overnight-batch" in src
        assert "run_overnight_batch_job" in src
        assert "fetch_per_tenant" in src
        assert "select_per_tenant" in src
        assert "workers" in src
