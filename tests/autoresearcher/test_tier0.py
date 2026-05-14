"""Tier-0 specifics tests: promoter routes to advisor queue; runner
composes the full lifecycle."""
from __future__ import annotations

from engine.autoresearcher.ledger import ExperimentRecord
from engine.autoresearcher.tier0.promoter import queue_for_advisor_review
from engine.autoresearcher.tier0.runner import run_tier0


def _mk_kept_record() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-test",
        ts="2026-05-01T00:00:00",
        tier="system",
        seed=42,
        knob_kind="ordinal_mapping",
        knob_id="confidence_band:high",
        knob_before={"value": 0.85},
        knob_after={"value": 0.80},
        metric_before={"composite": 0.5},
        metric_after={"composite": 0.55},
        metric_delta=0.05,
        decision="keep",
        rationale="test",
        n_articles=10,
    )


def test_promoter_writes_to_advisor_queue(tmp_path):
    """Accepted knob → advisor_queue.jsonl entry with the right shape."""
    ok = queue_for_advisor_review(_mk_kept_record(), base_data_dir=tmp_path)
    assert ok is True

    # Verify the queue file got an entry
    from engine.audit import read_advisor_queue
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert len(events) >= 1
    last = events[-1]
    assert last["event_type"] == "unverified_candidate"
    assert last["category"] == "autoresearcher_knob_change"
    assert "confidence_band:high" in last.get("candidate_id", "")


def test_promoter_is_best_effort_on_failure(tmp_path, monkeypatch):
    """Force a failure and confirm promoter returns False, not raises."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(
        "engine.audit.route_unverified_to_advisor", boom,
    )
    ok = queue_for_advisor_review(_mk_kept_record(), base_data_dir=tmp_path)
    assert ok is False


def test_run_tier0_returns_loop_result(tmp_path, monkeypatch):
    """End-to-end: runner discovers knobs, loads corpus, runs loop."""
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    result = run_tier0(
        budget=3,
        seed=7,
        keep_threshold=-1.0,  # accept any change for the smoke test
        min_age_days=0,
        base_data_dir=tmp_path,
    )
    # We don't assert specific keeps because the corpus may be empty
    # in a fresh checkout. Either the loop ran or short-circuited; both
    # are acceptable behaviour.
    assert result.tier == "system"
    assert result.budget == 3
    # n_keeps + n_discards + n_errors ≤ budget
    assert result.n_keeps + result.n_discards + result.n_errors <= 3
