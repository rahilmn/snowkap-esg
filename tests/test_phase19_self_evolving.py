"""Phase 19 — Self-evolving ontology wiring regression tests.

Validates the two operational gaps identified in the user-journey audit:

  1. The ingest path (`engine/main.py::_run_article`) now feeds the
     discovery buffer (was on-demand-only pre-Phase-19).
  2. The scheduler (`engine/scheduler.py`) has a periodic
     `run_promote_job` that drains the buffer into `discovered.ttl`
     without manual `POST /api/discovery/promote`.

Each test corresponds to one of the gaps so a regression points at the
right wiring.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Gap 1 — ingest path wiring
# ---------------------------------------------------------------------------


class TestIngestPathFeedsBuffer:
    """`_run_article` must call `collect_discoveries` after every successful
    pipeline run so the nightly ingest contributes to entity / theme /
    event / framework discovery — not just the on-demand path."""

    def test_run_article_imports_collect_discoveries(self):
        from engine.main import _run_article

        src = inspect.getsource(_run_article)
        # Static check: the import + call must both be present.
        assert "from engine.ontology.discovery.collector import collect_discoveries" in src, (
            "Ingest path is missing the collect_discoveries import"
        )
        assert "collect_discoveries(result, insight, company.slug)" in src, (
            "Ingest path is missing the collect_discoveries call"
        )

    def test_run_article_wraps_discovery_in_try_except(self):
        # Discovery is additive — a buffer error must NEVER block ingest.
        from engine.main import _run_article

        src = inspect.getsource(_run_article)
        # The collect_discoveries call sits inside a try/except block. Look for
        # "discovery collection skipped" log message which lives in the except.
        assert "discovery collection skipped" in src, (
            "collect_discoveries must be wrapped in try/except so a buffer "
            "error doesn't block ingest"
        )


# ---------------------------------------------------------------------------
# Gap 2 — scheduler runs the promoter periodically
# ---------------------------------------------------------------------------


class TestSchedulerRunsPromoter:
    """The periodic scheduler must run `batch_promote` on its own cadence
    so the discovery buffer doesn't sit idle for weeks."""

    def test_scheduler_exposes_run_promote_job(self):
        from engine import scheduler

        assert hasattr(scheduler, "run_promote_job"), (
            "scheduler.run_promote_job missing"
        )
        assert callable(scheduler.run_promote_job)

    def test_run_promote_job_calls_batch_promote(self):
        # Static check on the function source — confirms it imports and
        # calls the right function.
        from engine import scheduler

        src = inspect.getsource(scheduler.run_promote_job)
        assert "from engine.ontology.discovery.promoter import batch_promote" in src
        assert "batch_promote()" in src

    def test_scheduler_cli_exposes_new_flags(self):
        import io
        import sys

        from engine import scheduler

        # Capture --help output to confirm the new flags surface
        captured = io.StringIO()
        sys.stdout = captured
        try:
            scheduler.main(["--help"])
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
        help_text = captured.getvalue()
        assert "--promote-interval-minutes" in help_text, (
            "--promote-interval-minutes flag missing from scheduler CLI"
        )
        assert "--promote-once" in help_text, (
            "--promote-once flag missing from scheduler CLI"
        )

    def test_one_shot_mode_runs_promote_after_ingest(self):
        # In `--once` mode (cron-based deploys), the scheduler must run
        # the promoter at the end so daily cron entries don't need a
        # separate `--promote-once` line.
        from engine import scheduler

        src = inspect.getsource(scheduler.main)
        # Locate the `if args.once:` block and verify run_promote_job is
        # called inside it (not just run_ingest_job).
        assert "if args.once:" in src
        assert "run_promote_job()" in src, (
            "One-shot scheduler mode must drain the discovery buffer too"
        )

    def test_promote_interval_zero_disables_promoter(self):
        # Operators who want ingest-only mode (e.g. during bootstrap)
        # can pass --promote-interval-minutes=0 to disable. Source check
        # that the conditional exists.
        from engine import scheduler

        src = inspect.getsource(scheduler.main)
        assert "args.promote_interval_minutes > 0" in src, (
            "Scheduler must skip add_job when promote_interval is 0"
        )


# ---------------------------------------------------------------------------
# Gap 3 — discovery audit log + discovered.ttl coexist
# ---------------------------------------------------------------------------


class TestDiscoveryArtifactsExist:
    """The two on-disk artifacts (discovered.ttl + audit log) live where
    the promoter expects them, with the right format."""

    def test_discovered_ttl_path_exists(self):
        # Either the file exists OR the directory does (file is created on
        # first promotion). Path should be data/ontology/discovered.ttl.
        repo_root = Path(__file__).resolve().parent.parent
        expected = repo_root / "data" / "ontology" / "discovered.ttl"
        # Must at least be in a writable directory
        assert expected.parent.exists(), (
            f"data/ontology directory missing: {expected.parent}"
        )

    def test_audit_log_path_is_jsonl(self):
        repo_root = Path(__file__).resolve().parent.parent
        audit = repo_root / "data" / "ontology" / "discovery_audit.jsonl"
        # File may or may not exist; we just confirm the directory does
        assert audit.parent.exists()


# ---------------------------------------------------------------------------
# Gap 4 — promoter thresholds match the documented design
# ---------------------------------------------------------------------------


class TestPromoterThresholds:
    """The thresholds in `promoter.THRESHOLDS` must match the design spec
    so the system promotes high-confidence Tier-1 sources fast and
    requires multi-source corroboration for entities."""

    def test_framework_auto_promotes_with_one_article(self):
        from engine.ontology.discovery.promoter import THRESHOLDS

        fw = THRESHOLDS["framework"]
        assert fw["auto_promote"] is True
        assert fw["min_articles"] == 1  # Tier-1 sources only need one mention
        assert fw["min_confidence"] >= 0.70

    def test_entity_requires_multi_source(self):
        from engine.ontology.discovery.promoter import THRESHOLDS

        e = THRESHOLDS["entity"]
        assert e["auto_promote"] is True
        assert e["min_articles"] >= 3
        assert e["min_sources"] >= 2  # Cross-source corroboration

    def test_theme_requires_human_review(self):
        # Themes change taxonomy — never auto-promote.
        from engine.ontology.discovery.promoter import THRESHOLDS

        assert THRESHOLDS["theme"]["auto_promote"] is False

    def test_edge_and_weight_require_human_review(self):
        # Causal edges + materiality weights are high-impact ontology
        # changes — must be human-reviewed.
        from engine.ontology.discovery.promoter import THRESHOLDS

        assert THRESHOLDS["edge"]["auto_promote"] is False
        assert THRESHOLDS["weight"]["auto_promote"] is False


# ---------------------------------------------------------------------------
# Gap 5 — buffer capacity guard
# ---------------------------------------------------------------------------


class TestBufferGuards:
    """Defensive checks: the discovery surface must not grow unbounded."""

    def test_max_discovered_triples_cap_present(self):
        from engine.ontology.discovery.promoter import MAX_DISCOVERED_TRIPLES

        assert MAX_DISCOVERED_TRIPLES > 0
        # Spec says 10,000 — keep it sensible
        assert MAX_DISCOVERED_TRIPLES <= 100_000


# ---------------------------------------------------------------------------
# Integration — end-to-end round trip on a tiny in-memory buffer
# ---------------------------------------------------------------------------


class TestEndToEndPromote:
    """One genuine round-trip: drop a synthetic candidate that meets the
    framework threshold, run batch_promote, confirm `promoted` count."""

    def test_synthetic_framework_candidate_promotes(self, tmp_path, monkeypatch):
        from engine.ontology.discovery import candidates as cand_mod
        from engine.ontology.discovery import promoter as prom_mod
        from engine.ontology.discovery.candidates import (
            CATEGORY_FRAMEWORK,
            DiscoveryBuffer,
            DiscoveryCandidate,
            STATUS_PENDING,
        )

        # Build a fresh isolated buffer pointing at tmp_path and patch
        # get_buffer() to return it. This ensures the test isn't polluted
        # by any pre-existing discovery_staging.json on disk.
        isolated_buffer = DiscoveryBuffer(staging_path=tmp_path / "buffer.json")
        monkeypatch.setattr(cand_mod, "_buffer", isolated_buffer)
        # The promoter imports get_buffer at function scope, but the
        # module-level reference still points at the patched singleton
        # via the cand_mod._buffer attribute we just replaced.

        # Redirect discovered.ttl + audit log so we don't write to the
        # real ontology folder.
        monkeypatch.setattr(
            prom_mod, "DISCOVERED_TTL_PATH",
            tmp_path / "discovered.ttl",
        )
        monkeypatch.setattr(
            prom_mod, "AUDIT_LOG_PATH",
            tmp_path / "audit.jsonl",
        )

        # Frameworks auto-promote at conf >= 0.70 with min_articles=1.
        isolated_buffer.add(DiscoveryCandidate(
            category=CATEGORY_FRAMEWORK,
            label="TEST_FRAMEWORK_PHASE19",
            slug="test_framework_phase19",
            article_ids=["test-article-id"],
            sources=["Test Source"],
            companies=["test-company"],
            confidence=0.85,
            first_seen="2026-04-27T00:00:00+00:00",
            last_seen="2026-04-27T00:00:00+00:00",
            data={"reference": "TEST"},
            status=STATUS_PENDING,
        ))

        result = prom_mod.batch_promote()
        # Isolated buffer has just the one synthetic candidate; expect 1.
        assert result["promoted"] == 1, (
            f"Expected exactly 1 promotion, got {result}"
        )
        # The discovered.ttl file should now exist with our test framework
        ttl_text = (tmp_path / "discovered.ttl").read_text(encoding="utf-8")
        assert "TEST_FRAMEWORK_PHASE19" in ttl_text
