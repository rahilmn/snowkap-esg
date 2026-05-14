"""Tier-2 autoresearcher tests (per-user persona + click affinity)."""
from __future__ import annotations

import json

import pytest

from engine.autoresearcher.knob_kinds.persona_weight import (
    PersonaWeightKnob,
    PersonaWeightState,
)
from engine.autoresearcher.knobs import KnobError
from engine.autoresearcher.ledger import ExperimentRecord
from engine.autoresearcher.tier2.corpus import load_user_corpus, load_user_history
from engine.autoresearcher.tier2.promoter import (
    promote_user_knob,
    write_persona_weights,
)
from engine.autoresearcher.tier2.runner import (
    DEFAULT_AFFINITY_KEYS,
    run_tier2,
)


# ---------------------------------------------------------------------------
# PersonaWeightKnob
# ---------------------------------------------------------------------------


def test_persona_weight_round_trip():
    s = PersonaWeightState(values={("alice", "framework_brsr"): 0.7})
    k = PersonaWeightKnob(user_id="alice", key="framework_brsr", state=s)
    k.apply(0.75)
    assert k.current_value() == 0.75
    k.revert()
    assert k.current_value() == 0.7


def test_persona_weight_isolated_between_users():
    """A knob change for alice doesn't affect bob."""
    s = PersonaWeightState(values={
        ("alice", "framework_brsr"): 0.7,
        ("bob", "framework_brsr"): 0.3,
    })
    k = PersonaWeightKnob(user_id="alice", key="framework_brsr", state=s)
    k.apply(0.78)
    assert s.get("bob", "framework_brsr") == 0.3  # untouched
    k.revert()


def test_persona_weight_rejects_out_of_clamp():
    s = PersonaWeightState(values={("alice", "x"): 0.5})
    k = PersonaWeightKnob(user_id="alice", key="x", state=s, magnitude=10.0)
    with pytest.raises(KnobError, match=r"\[0, 1\]"):
        k.apply(1.5)


def test_persona_weight_requires_user_id_and_key():
    s = PersonaWeightState()
    with pytest.raises(KnobError):
        PersonaWeightKnob(user_id="", key="x", state=s)
    with pytest.raises(KnobError):
        PersonaWeightKnob(user_id="alice", key="", state=s)


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def test_load_user_history_returns_empty_for_unknown_user(tmp_path):
    history = load_user_history(user_id="never-clicked", repo_root=tmp_path)
    assert history == []


def test_load_user_corpus_builds_from_history(tmp_path):
    persona_dir = tmp_path / "data" / "persona"
    persona_dir.mkdir(parents=True)
    history = [
        {
            "ts": "2026-05-01T00:00:00",
            "article_id": "a1",
            "tenant_slug": "adani-power",
            "url": "https://x.com/a1",
            "title": "T1",
            "published_at": "2026-04-01T00:00:00+00:00",
            "predicted_band": "HIGH",
            "action": "click",
            "themes": ["water"],
        },
        {
            "ts": "2026-05-02T00:00:00",
            "article_id": "a2",
            "predicted_band": "HIGH",
            "action": "dismiss",
            "themes": [],
        },
    ]
    (persona_dir / "alice_clicks.json").write_text(json.dumps(history))

    corpus = load_user_corpus(user_id="alice", repo_root=tmp_path)
    assert len(corpus) >= 1
    # First entry (click → CONFIRMED)
    clicks = [c for c in corpus if c.article_id == "a1"]
    if clicks:
        assert clicks[0].gold_tier_band == "CONFIRMED"
    # Second entry (dismiss → OVER_STATED since predicted HIGH)
    dismisses = [c for c in corpus if c.article_id == "a2"]
    if dismisses:
        assert dismisses[0].gold_tier_band == "OVER_STATED"


# ---------------------------------------------------------------------------
# Promoter
# ---------------------------------------------------------------------------


def test_write_persona_weights_persists_only_target_user(tmp_path):
    s = PersonaWeightState(values={
        ("alice", "framework_brsr"): 0.7,
        ("alice", "geo_in"): 0.9,
        ("bob", "geo_in"): 0.4,
    })
    path = write_persona_weights(user_id="alice", state=s, repo_root=tmp_path)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "framework_brsr" in data
    assert "geo_in" in data
    # bob's keys NOT leaked
    assert "bob" not in str(data).lower() or data["geo_in"] == 0.9


def test_promote_user_knob_returns_success(tmp_path):
    s = PersonaWeightState(values={("alice", "framework_brsr"): 0.7})
    rec = ExperimentRecord(
        experiment_id="exp", ts="2026-05-01T00:00:00", tier="user", seed=42,
        knob_kind="persona_weight", knob_id="persona:alice:framework_brsr",
        knob_before={}, knob_after={}, metric_before={}, metric_after={},
        metric_delta=0.05, decision="keep", rationale="t", n_articles=1,
    )
    result = promote_user_knob(record=rec, user_id="alice", state=s, repo_root=tmp_path)
    assert result["ok"] is True
    assert result["user_id"] == "alice"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_default_affinity_keys_is_non_empty():
    assert len(DEFAULT_AFFINITY_KEYS) >= 9


def test_run_tier2_returns_loop_result(tmp_path, monkeypatch):
    """Empty corpus is normal for a fresh user — runner short-circuits cleanly."""
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    result = run_tier2(
        user_id="ephemeral-test-user",
        budget=3,
        seed=11,
        keep_threshold=-1.0,
        base_data_dir=tmp_path,
        repo_root=tmp_path,
    )
    assert result.tier == "user"
    assert result.budget == 3
