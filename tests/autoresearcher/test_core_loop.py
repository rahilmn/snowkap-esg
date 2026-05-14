"""Integration tests for the core autoresearcher loop:
  corpus → metrics → ledger → experimenter → evaluator → loop
"""
from __future__ import annotations

from pathlib import Path

from engine.autoresearcher.corpus import CorpusArticle, load_held_out_corpus
from engine.autoresearcher.experimenter import Experimenter
from engine.autoresearcher.evaluator import evaluate
from engine.autoresearcher.knob_kinds.ordinal_mapping import (
    OrdinalMappingKnob,
    OrdinalMappingState,
)
from engine.autoresearcher.ledger import (
    ExperimentRecord,
    leaderboard,
    make_experiment_id,
    read_ledger,
    record_experiment,
)
from engine.autoresearcher.loop import run_loop
from engine.autoresearcher.metrics import calibration_score
from engine.autoresearcher.ontology_introspector import KnobRegistry


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------


def _make_corpus(n: int = 5) -> list[CorpusArticle]:
    """Synthetic corpus for deterministic testing."""
    return [
        CorpusArticle(
            article_id=f"a{i}",
            tenant_slug="test-tenant",
            url=f"https://x.com/{i}",
            title=f"Article {i}",
            published_at=f"2026-04-{i+1:02d}T00:00:00+00:00",
            predicted_tier="HOME",
            predicted_band="HIGH",
            themes=["water"],
            gold_tier_band="CONFIRMED",
            gold_advisor_verdict="approve" if i % 2 == 0 else "none",
            gold_audit_clean=True,
            raw_insight={},
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_calibration_score_on_perfect_corpus_is_high():
    """All HIGH predictions, all CONFIRMED gold, all approved → near 1.0."""
    corpus = _make_corpus(5)
    score = calibration_score(corpus)
    assert score.composite > 0.7  # f1=1.0, ndcg≈1.0, audit=1.0, advisor=1.0
    assert score.f1 == 1.0


def test_calibration_score_on_empty_corpus_is_zero():
    score = calibration_score([])
    assert score.composite == 0.0


def test_calibration_score_breakdown_is_json_serialisable():
    import json
    corpus = _make_corpus(3)
    score = calibration_score(corpus)
    json.dumps(score.to_dict())


def test_calibration_score_on_mispredicted_corpus_is_lower():
    """LOW predictions on CONFIRMED gold → F1 should be 0."""
    corpus = _make_corpus(5)
    for a in corpus:
        a.predicted_band = "LOW"
    score = calibration_score(corpus)
    assert score.f1 == 0.0


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def test_ledger_round_trip(tmp_path):
    rec = ExperimentRecord(
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
    path = record_experiment(rec, base_data_dir=tmp_path, emit_audit=False)
    assert path.exists()
    entries = list(read_ledger("system", base_data_dir=tmp_path))
    assert len(entries) == 1
    assert entries[0]["experiment_id"] == "exp-test"
    assert entries[0]["decision"] == "keep"


def test_leaderboard_returns_top_keeps_sorted(tmp_path):
    """Leaderboard sorts kept experiments by metric_delta descending."""
    for i, delta in enumerate([0.01, 0.05, 0.03]):
        record_experiment(ExperimentRecord(
            experiment_id=f"exp-{i}",
            ts="2026-05-01T00:00:00", tier="system", seed=42,
            knob_kind="x", knob_id=f"k{i}",
            knob_before={}, knob_after={}, metric_before={}, metric_after={},
            metric_delta=delta, decision="keep", rationale="t", n_articles=1,
        ), base_data_dir=tmp_path, emit_audit=False)
    top = leaderboard("system", base_data_dir=tmp_path)
    assert [r["metric_delta"] for r in top] == [0.05, 0.03, 0.01]


def test_make_experiment_id_is_stable():
    """Same seed + same n → same id."""
    assert make_experiment_id(42, 0) == make_experiment_id(42, 0)
    assert make_experiment_id(42, 0) != make_experiment_id(42, 1)


# ---------------------------------------------------------------------------
# Experimenter
# ---------------------------------------------------------------------------


def test_experimenter_proposes_within_bound():
    state = OrdinalMappingState(values={
        ("confidence_band", "low"): 0.30,
        ("confidence_band", "moderate"): 0.60,
    })
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [
        OrdinalMappingKnob(category="confidence_band", label="low", state=state),
        OrdinalMappingKnob(category="confidence_band", label="moderate", state=state),
    ]
    exp = Experimenter(reg, seed=42)
    p = exp.propose()
    assert p is not None
    assert p.knob in reg.knobs
    # New value must be within the magnitude bound
    delta = abs(p.new_value - p.knob.baseline_value())
    assert delta <= p.knob.magnitude_bound()


def test_experimenter_seed_stable():
    """Same seed → same first proposal."""
    state = OrdinalMappingState(values={
        ("confidence_band", "low"): 0.30,
        ("confidence_band", "moderate"): 0.60,
    })
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [
        OrdinalMappingKnob(category="confidence_band", label="low", state=state),
        OrdinalMappingKnob(category="confidence_band", label="moderate", state=state),
    ]
    p1 = Experimenter(reg, seed=7).propose()
    p2 = Experimenter(reg, seed=7).propose()
    assert p1.knob.knob_id == p2.knob.knob_id
    assert p1.new_value == p2.new_value


def test_experimenter_returns_none_on_empty_registry():
    exp = Experimenter(KnobRegistry(), seed=42)
    assert exp.propose() is None


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def test_evaluator_applies_and_reverts():
    """After evaluate(), the knob's value is back to baseline."""
    state = OrdinalMappingState(values={("confidence_band", "low"): 0.30})
    k = OrdinalMappingKnob(category="confidence_band", label="low", state=state)
    baseline = k.current_value()
    from engine.autoresearcher.experimenter import Proposal
    p = Proposal(knob=k, new_value=0.35, rationale="t")
    corpus = _make_corpus(3)
    _result = evaluate(p, corpus)
    assert k.current_value() == baseline


def test_evaluator_returns_error_on_out_of_bounds():
    state = OrdinalMappingState(values={("confidence_band", "low"): 0.30})
    k = OrdinalMappingKnob(category="confidence_band", label="low", state=state, magnitude=0.05)
    from engine.autoresearcher.experimenter import Proposal
    p = Proposal(knob=k, new_value=0.90, rationale="too big")
    result = evaluate(p, _make_corpus(3))
    assert result.error is not None
    assert result.delta == 0.0


# ---------------------------------------------------------------------------
# Loop (integration)
# ---------------------------------------------------------------------------


def test_run_loop_with_empty_registry_short_circuits(tmp_path):
    result = run_loop(
        tier="system", registry=KnobRegistry(),
        corpus=_make_corpus(3), budget=10, seed=42,
        base_data_dir=tmp_path,
    )
    assert result.n_keeps == 0
    assert result.n_discards == 0


def test_run_loop_with_empty_corpus_short_circuits(tmp_path):
    state = OrdinalMappingState(values={("confidence_band", "low"): 0.30})
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [OrdinalMappingKnob(category="confidence_band", label="low", state=state)]
    result = run_loop(
        tier="system", registry=reg, corpus=[], budget=10, seed=42,
        base_data_dir=tmp_path,
    )
    assert result.n_keeps == 0


def test_run_loop_records_experiments_to_ledger(tmp_path, monkeypatch):
    """End-to-end: each experiment is recorded; ledger is readable."""
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    state = OrdinalMappingState(values={
        ("confidence_band", "low"): 0.30,
        ("confidence_band", "moderate"): 0.60,
        ("confidence_band", "high"): 0.85,
    })
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [
        OrdinalMappingKnob(category="confidence_band", label=label, state=state)
        for label in ("low", "moderate", "high")
    ]
    result = run_loop(
        tier="system", registry=reg, corpus=_make_corpus(5),
        budget=5, seed=42, base_data_dir=tmp_path,
    )
    assert result.n_keeps + result.n_discards + result.n_errors == 5
    entries = list(read_ledger("system", base_data_dir=tmp_path))
    assert len(entries) == 5
    # Every entry has the required schema
    for e in entries:
        assert "experiment_id" in e
        assert "knob_kind" in e
        assert "metric_delta" in e
        assert "decision" in e


def test_run_loop_on_keep_callback_fires(tmp_path, monkeypatch):
    """When a `keep` happens, the on_keep callback gets the record.

    Synthetic: we lower the keep_threshold to -infinity so every
    experiment is a keep, ensuring the callback fires at least once.
    """
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    state = OrdinalMappingState(values={("confidence_band", "low"): 0.30})
    reg = KnobRegistry()
    reg.ordinal_state = state
    reg.knobs = [OrdinalMappingKnob(category="confidence_band", label="low", state=state)]
    keeps: list = []
    run_loop(
        tier="system", registry=reg, corpus=_make_corpus(3),
        budget=3, seed=42, keep_threshold=-1.0,
        base_data_dir=tmp_path,
        on_keep=keeps.append,
    )
    assert len(keeps) > 0
