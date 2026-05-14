"""Per-kind Knob tests — apply/revert round-trips + bound enforcement."""
from __future__ import annotations

import pytest

from engine.autoresearcher.knobs import KnobError
from engine.autoresearcher.knob_kinds.keyword_set import (
    KeywordSetKnob,
    KeywordSetState,
)
from engine.autoresearcher.knob_kinds.ontology_weight import (
    OntologyWeightKnob,
    OntologyWeightState,
)
from engine.autoresearcher.knob_kinds.ordinal_mapping import (
    OrdinalMappingKnob,
    OrdinalMappingState,
)
from engine.autoresearcher.knob_kinds.primitive_beta import (
    PrimitiveBetaKnob,
    PrimitiveBetaState,
)
from engine.autoresearcher.knob_kinds.scorer_component import (
    ScorerComponentKnob,
    ScorerWeightState,
)


# ---------------------------------------------------------------------------
# OrdinalMappingKnob
# ---------------------------------------------------------------------------


def test_ordinal_mapping_round_trip():
    state = OrdinalMappingState(values={("confidence_band", "low"): 0.30})
    k = OrdinalMappingKnob(category="confidence_band", label="low", state=state)
    assert k.current_value() == 0.30
    k.apply(0.40)
    assert k.current_value() == 0.40
    k.revert()
    assert k.current_value() == 0.30


def test_ordinal_mapping_rejects_unknown_category():
    state = OrdinalMappingState(values={})
    with pytest.raises(KnobError, match="category"):
        OrdinalMappingKnob(category="not_a_category", label="x", state=state)


def test_ordinal_mapping_enforces_magnitude_bound():
    state = OrdinalMappingState(values={("confidence_band", "high"): 0.85})
    k = OrdinalMappingKnob(category="confidence_band", label="high", state=state, magnitude=0.1)
    with pytest.raises(KnobError, match="magnitude"):
        k.apply(0.50)  # |Δ| = 0.35 > 0.10


def test_ordinal_mapping_from_query_all_loads_from_ontology():
    state = OrdinalMappingState.from_query_all()
    # The TTL is loaded by the existing ontology; values should be non-empty
    # OR empty (if TTL missing). Either is OK — test just confirms no crash.
    assert isinstance(state.values, dict)


# ---------------------------------------------------------------------------
# OntologyWeightKnob
# ---------------------------------------------------------------------------


def test_ontology_weight_round_trip():
    state = OntologyWeightState(values={
        ("materialFor", "topic_water", "industry_banking"): 0.7,
    })
    k = OntologyWeightKnob(
        predicate="materialFor", subj="topic_water", obj="industry_banking",
        state=state,
    )
    assert k.current_value() == 0.7
    k.apply(0.85)
    assert k.current_value() == 0.85
    k.revert()
    assert k.current_value() == 0.7


def test_ontology_weight_rejects_negative_via_soft_bound():
    """Apply with a value that's within magnitude but violates the
    soft [0, 2] clamp must raise (here magnitude=0.2 admits -0.1)."""
    state = OntologyWeightState(values={("materialFor", "x", "y"): 0.05})
    k = OntologyWeightKnob(predicate="materialFor", subj="x", obj="y", state=state)
    # baseline=0.05; new=-0.1; |Δ|=0.15 ≤ 0.2 magnitude OK; soft bound trips
    with pytest.raises(KnobError, match="soft bounds"):
        k.apply(-0.1)


def test_ontology_weight_rejects_out_of_magnitude():
    state = OntologyWeightState(values={("materialFor", "x", "y"): 0.5})
    k = OntologyWeightKnob(predicate="materialFor", subj="x", obj="y", state=state, magnitude=0.1)
    with pytest.raises(KnobError, match="magnitude"):
        k.apply(0.9)  # Δ=0.4 > 0.1


# ---------------------------------------------------------------------------
# ScorerComponentKnob
# ---------------------------------------------------------------------------


def test_scorer_component_round_trip():
    state = ScorerWeightState(values={("cfo", "financial_magnitude"): 0.40})
    k = ScorerComponentKnob(role="cfo", component="financial_magnitude", state=state)
    k.apply(0.45)
    assert k.current_value() == 0.45
    k.revert()
    assert k.current_value() == 0.40


def test_scorer_component_rejects_invalid_role():
    state = ScorerWeightState()
    with pytest.raises(KnobError, match="role"):
        ScorerComponentKnob(role="bogus", component="materiality", state=state)


def test_scorer_component_rejects_invalid_component():
    state = ScorerWeightState()
    with pytest.raises(KnobError, match="component"):
        ScorerComponentKnob(role="cfo", component="bogus", state=state)


def test_scorer_component_state_from_module_loads_all_weights():
    state = ScorerWeightState.from_scorer_module()
    # Default weights should be present
    assert state.get("default", "materiality") > 0
    assert state.get("default", "financial_magnitude") > 0
    # Per-role weights should be present
    assert state.get("cfo", "financial_magnitude") > 0


# ---------------------------------------------------------------------------
# KeywordSetKnob
# ---------------------------------------------------------------------------


def test_keyword_set_add_round_trip():
    state = KeywordSetState(sets={"event_x": frozenset({"k1", "k2"})})
    k = KeywordSetKnob(event_type="event_x", keyword="k3", action="add", state=state)
    k.apply()
    assert "k3" in state.get("event_x")
    k.revert()
    assert "k3" not in state.get("event_x")


def test_keyword_set_remove_round_trip():
    state = KeywordSetState(sets={"event_x": frozenset({"k1", "k2"})})
    k = KeywordSetKnob(event_type="event_x", keyword="k2", action="remove", state=state)
    k.apply()
    assert "k2" not in state.get("event_x")
    k.revert()
    assert "k2" in state.get("event_x")


def test_keyword_set_rejects_invalid_action():
    state = KeywordSetState()
    with pytest.raises(KnobError, match="action"):
        KeywordSetKnob(event_type="x", keyword="k", action="rotate", state=state)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PrimitiveBetaKnob
# ---------------------------------------------------------------------------


def test_primitive_beta_round_trip():
    state = PrimitiveBetaState(values={"edge_EP_OX": 0.25})
    k = PrimitiveBetaKnob(edge_id="edge_EP_OX", state=state)
    k.apply(0.30)
    assert k.current_value() == 0.30
    k.revert()
    assert k.current_value() == 0.25


def test_primitive_beta_rejects_extreme_values():
    state = PrimitiveBetaState(values={"e": 0.25})
    k = PrimitiveBetaKnob(edge_id="e", state=state)
    with pytest.raises(KnobError, match="soft bounds"):
        k.apply(3.0)


# ---------------------------------------------------------------------------
# All-kinds describe() is JSON-serialisable
# ---------------------------------------------------------------------------


def test_all_kinds_describe_is_json_serialisable():
    import json
    knobs = [
        OrdinalMappingKnob(category="confidence_band", label="low",
                           state=OrdinalMappingState(values={("confidence_band", "low"): 0.3})),
        OntologyWeightKnob(predicate="materialFor", subj="t", obj="i",
                           state=OntologyWeightState(values={("materialFor", "t", "i"): 0.5})),
        ScorerComponentKnob(role="cfo", component="materiality",
                            state=ScorerWeightState(values={("cfo", "materiality"): 0.2})),
        KeywordSetKnob(event_type="e", keyword="k", action="add",
                       state=KeywordSetState(sets={"e": frozenset()})),
        PrimitiveBetaKnob(edge_id="e1", state=PrimitiveBetaState(values={"e1": 0.1})),
    ]
    for k in knobs:
        d = k.describe()
        json.dumps(d)  # must not raise
        assert d["kind"] == k.kind
        assert d["id"] == k.knob_id
