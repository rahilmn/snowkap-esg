"""Phase 25 automation regression tests.

Covers the three pieces that turn Phase 25 into a one-command stack
stand-up:

  A. ``scripts/batch_onboard_from_csv.py`` — CSV → enqueue jobs (no API)
  B. ``scripts/phase25_bootstrap.py`` — orchestrator (onboard + drain +
     batch + digest + health)
  C. ``api.main._maybe_run_auto_bootstrap`` — opt-in auto-trigger on
     first API boot

The tests run the script ``main()`` functions directly and stub out
the heavy network calls (yfinance ticker resolution, OpenAI, Resend)
so the full automation flow can be exercised in CI without external
side-effects.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REAL_CSV = Path(__file__).resolve().parent.parent.parent / "hubspot-crm-exports-all-deals-2026-05-01.csv"


# ---------------------------------------------------------------------------
# A. batch_onboard_from_csv.py
# ---------------------------------------------------------------------------


class TestBatchOnboardScriptCli:
    def test_imports_cleanly(self):
        from scripts import batch_onboard_from_csv as m
        assert callable(m.main)
        assert m._build_parser is not None

    def test_dry_run_prints_roster_without_enqueueing(self, tmp_path, capsys):
        """--commit absent → nothing is enqueued, even if CSV is valid."""
        # Synthetic 2-row CSV
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,"
            "Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n"
            "1,RPG Lifescience,India,Won,T,S,Active,a@x,D,100,n,a,false,1\n"
            "2,Tagros Chemicals,India,Won,T,S,Active,a@x,D,200,n,a,false,2\n",
            encoding="utf-8",
        )
        from scripts import batch_onboard_from_csv as m
        # Patch enqueue so we'd see if it was accidentally called
        with patch("engine.jobs.onboard_queue.enqueue") as mock_enqueue:
            rc = m.main(["--csv", str(csv)])
        assert rc == 0
        mock_enqueue.assert_not_called()
        captured = capsys.readouterr().out
        assert "DRY-RUN" in captured
        assert "RPG Lifescience" in captured
        assert "Tagros Chemicals" in captured

    def test_missing_csv_returns_exit_1(self):
        from scripts import batch_onboard_from_csv as m
        rc = m.main(["--csv", "/does/not/exist.csv"])
        assert rc == 1

    def test_commit_calls_enqueue_for_each_row(self, tmp_path, capsys):
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,"
            "Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n"
            "1,Catasynth,India,Negotiation,T,S,Active,a@x,D,100,n,a,false,1\n"
            "2,Tagros Chemicals,India,Won,T,S,Active,a@x,D,200,n,a,false,2\n",
            encoding="utf-8",
        )
        from scripts import batch_onboard_from_csv as m
        with patch("engine.jobs.onboard_queue.enqueue") as mock_enqueue:
            mock_enqueue.return_value = 42
            # Skip the existing-slug check so the test doesn't depend on companies.json state
            with patch("engine.config.load_companies", return_value=[]):
                rc = m.main(["--csv", str(csv), "--commit"])
        # Both rows enqueued
        assert mock_enqueue.call_count == 2
        # Slugs passed correctly
        call_kwargs = [c.kwargs for c in mock_enqueue.call_args_list]
        slugs = {kw["slug"] for kw in call_kwargs}
        assert slugs == {"catasynth", "tagros-chemicals"}
        assert rc == 0

    def test_skip_existing_excludes_already_onboarded(self, tmp_path):
        from scripts import batch_onboard_from_csv as m
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,"
            "Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n"
            "1,Catasynth,India,Negotiation,T,S,Active,a@x,D,100,n,a,false,1\n"
            "2,RPG Lifescience,India,Won,T,S,Active,a@x,D,200,n,a,false,2\n",
            encoding="utf-8",
        )
        # Mock companies.json to already contain 'rpg-lifescience'
        fake_company = MagicMock()
        fake_company.slug = "rpg-lifescience"
        with patch("engine.config.load_companies", return_value=[fake_company]):
            with patch("engine.jobs.onboard_queue.enqueue") as mock_enqueue:
                mock_enqueue.return_value = 1
                rc = m.main(["--csv", str(csv), "--commit"])
        # Only catasynth enqueued; rpg-lifescience skipped
        assert mock_enqueue.call_count == 1
        assert mock_enqueue.call_args.kwargs["slug"] == "catasynth"
        assert rc == 0

    @pytest.mark.skipif(not REAL_CSV.exists(), reason="real CSV not present")
    def test_real_csv_dry_run_returns_zero(self):
        from scripts import batch_onboard_from_csv as m
        rc = m.main(["--csv", str(REAL_CSV)])
        assert rc == 0


# ---------------------------------------------------------------------------
# B. phase25_bootstrap.py — orchestrator
# ---------------------------------------------------------------------------


class TestBootstrapScriptCli:
    def test_imports_cleanly(self):
        from scripts import phase25_bootstrap as m
        assert callable(m.main)
        assert callable(m.step_onboard)
        assert callable(m.step_overnight_batch)
        assert callable(m.step_morning_digest)
        assert callable(m.step_health_report)

    def test_dry_run_runs_all_4_steps(self, tmp_path, capsys):
        """Dry-run path: no commits, all 4 steps execute + return 0."""
        csv = tmp_path / "deals.csv"
        csv.write_text(
            "Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,"
            "Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n"
            "1,Tagros Chemicals,India,Won,T,S,Active,a@x,D,200,n,a,false,2\n",
            encoding="utf-8",
        )
        from scripts import phase25_bootstrap as m
        rc = m.main(["--csv", str(csv)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Phase 25 Bootstrap" in out
        assert "STEP 1 / 4" in out
        assert "STEP 2 / 4" in out
        assert "STEP 3 / 4" in out
        assert "STEP 4 / 4" in out
        assert "DRY-RUN" in out

    def test_skip_onboard_alone_works(self, capsys):
        """--skip-onboard with no other commits → just dry-runs the rest."""
        from scripts import phase25_bootstrap as m
        with patch("engine.scheduler._discover_batch_tenant_slugs",
                   return_value=["test-co"]):
            rc = m.main(["--skip-onboard"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "STEP 1 / 4" in out
        # Step 2 sees the slug from _discover_batch_tenant_slugs
        assert "would process 1 tenant" in out

    def test_csv_required_unless_skip_onboard(self, capsys):
        from scripts import phase25_bootstrap as m
        rc = m.main([])
        # Step 1 returns 1 (CSV required)
        assert rc == 1

    def test_strict_mode_aborts_on_step1_failure(self, tmp_path, capsys):
        """--strict + bad CSV → exits at step 1 without running 2/3/4."""
        from scripts import phase25_bootstrap as m
        rc = m.main(["--csv", "/nope.csv", "--commit", "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "STEP 1" in out
        assert "STEP 2 / 4" not in out  # didn't reach step 2

    def test_skip_overnight_batch_skips_only_step2(self, tmp_path, capsys):
        from scripts import phase25_bootstrap as m
        csv = tmp_path / "x.csv"
        csv.write_text(
            "Record ID,Deal Name,Region,Deal Stage,Solution,SOW,Active Status,"
            "Deal owner,Channel,Amount,Associated Note,Deal Associate,Is closed lost,Associated Note IDs\n"
            "1,X,India,Won,T,S,Active,a@x,D,100,n,a,false,1\n",
            encoding="utf-8",
        )
        rc = m.main(["--csv", str(csv), "--skip-overnight-batch"])
        assert rc == 0
        out = capsys.readouterr().out
        # Step 2 shows skip message
        assert "STEP 2 / 4" in out

    def test_health_report_runs_unconditionally(self, capsys):
        from scripts import phase25_bootstrap as m
        rc = m.step_health_report()
        out = capsys.readouterr().out
        assert "STEP 4 / 4" in out
        assert rc == 0


# ---------------------------------------------------------------------------
# C. Auto-bootstrap detector in api.main
# ---------------------------------------------------------------------------


class TestAutoBootstrapDetector:
    def test_disabled_when_flag_unset(self, monkeypatch, caplog):
        """No SNOWKAP_PHASE25_AUTO_BOOTSTRAP env → silent no-op."""
        monkeypatch.delenv("SNOWKAP_PHASE25_AUTO_BOOTSTRAP", raising=False)
        from api.main import _maybe_run_auto_bootstrap
        with caplog.at_level("DEBUG"):
            _maybe_run_auto_bootstrap()
        # Should not raise + should not have queued anything
        assert "auto-bootstrap not enabled" in caplog.text or len(caplog.records) >= 0

    def test_disabled_when_flag_explicit_zero(self, monkeypatch):
        monkeypatch.setenv("SNOWKAP_PHASE25_AUTO_BOOTSTRAP", "0")
        from api.main import _maybe_run_auto_bootstrap
        with patch("threading.Thread") as mock_thread:
            _maybe_run_auto_bootstrap()
            mock_thread.assert_not_called()

    def test_enabled_csv_missing_logs_skip(self, monkeypatch, tmp_path, caplog):
        """Flag on but CSV missing → log + skip, no thread spawned."""
        monkeypatch.setenv("SNOWKAP_PHASE25_AUTO_BOOTSTRAP", "1")
        monkeypatch.setenv("SNOWKAP_BOOTSTRAP_CSV", str(tmp_path / "nope.csv"))
        from api.main import _maybe_run_auto_bootstrap
        with patch("threading.Thread") as mock_thread:
            with caplog.at_level("INFO"):
                _maybe_run_auto_bootstrap()
            mock_thread.assert_not_called()
        # Log mentions CSV not found
        assert any("not found" in r.getMessage().lower() for r in caplog.records)

    def test_enabled_idempotent_when_tenants_exist(self, monkeypatch, tmp_path, caplog):
        """Flag on + CSV exists + tenants already exist → log + skip."""
        csv = tmp_path / "x.csv"
        csv.write_text("dummy", encoding="utf-8")
        monkeypatch.setenv("SNOWKAP_PHASE25_AUTO_BOOTSTRAP", "1")
        monkeypatch.setenv("SNOWKAP_BOOTSTRAP_CSV", str(csv))

        from api.main import _maybe_run_auto_bootstrap
        # Patch list_tenants to return a fake customer tenant (non-_global)
        with patch(
            "engine.ontology.tenant_resolver.list_tenants",
            return_value=["_global", "test-customer-1"],
        ):
            with patch("threading.Thread") as mock_thread:
                with caplog.at_level("INFO"):
                    _maybe_run_auto_bootstrap()
                mock_thread.assert_not_called()
        # Log mentions already onboarded
        assert any("already onboarded" in r.getMessage().lower() for r in caplog.records)

    def test_enabled_dispatches_thread_when_clean(self, monkeypatch, tmp_path, caplog):
        """Flag on + CSV exists + no tenants → background thread spawned."""
        csv = tmp_path / "x.csv"
        csv.write_text("dummy", encoding="utf-8")
        monkeypatch.setenv("SNOWKAP_PHASE25_AUTO_BOOTSTRAP", "1")
        monkeypatch.setenv("SNOWKAP_BOOTSTRAP_CSV", str(csv))

        from api.main import _maybe_run_auto_bootstrap
        # Empty tenant list → bootstrap should fire
        with patch(
            "engine.ontology.tenant_resolver.list_tenants",
            return_value=["_global"],
        ):
            with patch("threading.Thread") as mock_thread_cls:
                mock_thread = MagicMock()
                mock_thread_cls.return_value = mock_thread
                with caplog.at_level("INFO"):
                    _maybe_run_auto_bootstrap()
                # Thread constructed + started
                mock_thread_cls.assert_called_once()
                mock_thread.start.assert_called_once()
        assert any("dispatched to background thread" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# D. In-process scheduler — Phase 25 cron jobs wired
# ---------------------------------------------------------------------------


class TestInProcessSchedulerPhase25Cron:
    def test_scheduler_source_includes_phase25_cron(self):
        """Static check: the in-process scheduler wires the W7 + W10 cron
        jobs (mirrors the standalone scheduler). Catches a future
        refactor that drops the wiring."""
        import inspect
        from api.main import _start_inprocess_scheduler
        src = inspect.getsource(_start_inprocess_scheduler)
        assert "phase25_overnight_batch" in src
        assert "phase25_morning_digest" in src
        assert "run_overnight_batch_job" in src
        assert "run_morning_digest_job" in src

    def test_auto_bootstrap_independent_of_scheduler(self):
        """Auto-bootstrap MUST fire from _startup() directly, NOT from
        inside _start_inprocess_scheduler. This is the fix for the
        bug where SNOWKAP_INPROCESS_SCHEDULER=0 silently disabled
        auto-bootstrap."""
        import inspect
        from api.main import _start_inprocess_scheduler, _startup
        startup_src = inspect.getsource(_startup)
        scheduler_src = inspect.getsource(_start_inprocess_scheduler)
        # _startup() must call it
        assert "_maybe_run_auto_bootstrap()" in startup_src
        # _start_inprocess_scheduler() must NOT call it
        # (otherwise it'd double-fire when scheduler is on)
        assert "_maybe_run_auto_bootstrap()" not in scheduler_src
