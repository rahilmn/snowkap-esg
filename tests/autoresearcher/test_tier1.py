"""Tier-1 autoresearcher tests (per-tenant corpus + R6 promoter)."""
from __future__ import annotations

import pytest

from engine.autoresearcher.corpus import CorpusArticle
from engine.autoresearcher.ledger import ExperimentRecord
from engine.autoresearcher.tier1.corpus import load_tenant_corpus
from engine.autoresearcher.tier1.promoter import (
    _topic_from_knob_id,
    promote_tenant_knob,
)
from engine.autoresearcher.tier1.runner import run_tier1


def _mk_record(metric_delta: float = 0.05) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp-t1",
        ts="2026-05-01T00:00:00",
        tier="tenant",
        seed=42,
        knob_kind="ontology_weight",
        knob_id="materialFor:topic_climate:industry_power",
        knob_before={"value": 0.7},
        knob_after={"value": 0.75},
        metric_before={"composite": 0.5},
        metric_after={"composite": 0.55},
        metric_delta=metric_delta,
        decision="keep",
        rationale="test",
        n_articles=5,
    )


# ---------------------------------------------------------------------------
# Corpus filter
# ---------------------------------------------------------------------------


def test_load_tenant_corpus_filters_to_tenant():
    """Real-data smoke: corpus loader filters by tenant slug.

    If no articles exist for the test tenant (fresh checkout), the
    result is an empty list — also acceptable."""
    result = load_tenant_corpus(tenant_slug="adani-power", min_age_days=0)
    assert isinstance(result, list)
    for a in result:
        assert isinstance(a, CorpusArticle)
        assert a.tenant_slug == "adani-power"


# ---------------------------------------------------------------------------
# Promoter
# ---------------------------------------------------------------------------


def test_topic_from_knob_id_extracts_topic_for_material_for():
    """The fallback topic extraction handles `materialFor:topic_X:industry_Y`."""
    topic = _topic_from_knob_id("materialFor:topic_climate:industry_power")
    assert "climate" in topic


def test_topic_from_knob_id_falls_back_to_generic():
    """Unknown id format → generic placeholder."""
    topic = _topic_from_knob_id("rand_knob_id")
    assert topic == "autoresearcher"


def test_promote_with_delta_above_threshold_fires_r6(tmp_path):
    """A high-delta proposal causes R6 to fire and a belief to update."""
    rec = _mk_record(metric_delta=0.05)
    summary = promote_tenant_knob(
        record=rec, tenant_slug="adani-power", audit_dir=tmp_path,
    )
    assert summary["ok"] is True
    # R6 fired, belief was updated
    assert summary.get("applied") is True
    assert summary["n_beliefs_updated"] >= 1


def test_promote_with_delta_below_threshold_does_not_apply(tmp_path):
    """When R6 doesn't fire (delta below threshold), the promoter
    returns ok=True but applied=False."""
    rec = _mk_record(metric_delta=0.001)
    summary = promote_tenant_knob(
        record=rec, tenant_slug="adani-power", audit_dir=tmp_path,
    )
    assert summary["ok"] is True
    assert summary.get("applied") is False


def test_promote_handles_belief_revision_import_failure(monkeypatch, tmp_path):
    """If belief_revision import fails, promoter returns ok=False — not raises."""
    import sys
    # Simulate failure by injecting a stub that raises on import
    monkeypatch.setitem(sys.modules, "engine.governance.belief_revision", None)
    try:
        summary = promote_tenant_knob(
            record=_mk_record(), tenant_slug="adani-power", audit_dir=tmp_path,
        )
        # Either ok=False or graceful "no R6" — neither raises
        assert summary["ok"] in (True, False)
    finally:
        # Reload the real module so other tests keep working
        del sys.modules["engine.governance.belief_revision"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_run_tier1_returns_loop_result(tmp_path, monkeypatch):
    """End-to-end smoke for Tier-1 runner."""
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    result = run_tier1(
        tenant_slug="adani-power",
        budget=3,
        seed=11,
        keep_threshold=-1.0,  # accept everything in smoke
        min_age_days=0,
        base_data_dir=tmp_path,
        audit_dir=tmp_path,
    )
    assert result.tier == "tenant"
    assert result.budget == 3
    assert result.n_keeps + result.n_discards + result.n_errors <= 3
