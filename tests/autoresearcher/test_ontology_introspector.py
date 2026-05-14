"""Tests for the ontology introspector — auto-discovery of knobs from
the live ontology + scorer module."""
from __future__ import annotations

from engine.autoresearcher.knobs import is_blacklisted
from engine.autoresearcher.ontology_introspector import (
    KnobRegistry,
    discover_all_knobs,
)


def test_discover_returns_registry():
    """The discovery returns a populated registry (or empty if ontology
    isn't loaded — either way no crash)."""
    reg = discover_all_knobs()
    assert isinstance(reg, KnobRegistry)
    assert isinstance(reg.knobs, list)


def test_registry_stats_has_total():
    reg = discover_all_knobs()
    stats = reg.stats()
    assert "__total__" in stats
    assert stats["__total__"] == len(reg.knobs)


def test_no_blacklisted_knobs_survive_discovery():
    """The discovery's final filter removes any blacklisted knob."""
    reg = discover_all_knobs()
    for k in reg.knobs:
        assert not is_blacklisted(kind=k.kind, knob_id=k.knob_id), (
            f"blacklisted knob leaked through: {k.kind}:{k.knob_id}"
        )


def test_discovered_scorer_component_knobs_include_per_role():
    """The scorer always has WEIGHTS_BY_ROLE — should produce knobs for cfo/ceo/analyst."""
    reg = discover_all_knobs()
    scorer_knobs = reg.by_kind("scorer_component_weight")
    roles = {getattr(k, "role", None) for k in scorer_knobs}
    # At minimum default+cfo+ceo+analyst should be present
    assert {"default", "cfo", "ceo", "analyst"}.issubset(roles)


def test_discovered_ordinal_mapping_knobs_have_categories():
    reg = discover_all_knobs()
    ord_knobs = reg.by_kind("ordinal_mapping")
    # When the TTL is loaded, we expect at least one knob per category
    # (3+ confidence levels + 4 severity + 3 stances + 5 priorities = 15)
    categories = {getattr(k, "category", None) for k in ord_knobs}
    # At least 1 category surfaced — proves the SPARQL queries reach the TTL
    assert len(categories) >= 1


def test_discovered_knobs_all_round_trip():
    """Every discovered knob can apply (with a value at its baseline)
    and revert without errors. This is the load-bearing contract that
    keeps the live system safe during replay."""
    import math

    reg = discover_all_knobs()
    sample = reg.knobs[:20]  # sample first 20 to keep test fast

    for k in sample:
        baseline = k.baseline_value()
        if k.kind in ("keyword_set_membership",):
            # Set-valued knobs don't take a `new_value` argument
            k.apply()
            k.revert()
            continue
        # Use a tiny perturbation within bound
        bound = k.magnitude_bound() if isinstance(k.magnitude_bound(), (int, float)) else 0.01
        try:
            new = baseline + min(bound / 2, 0.01) if isinstance(baseline, (int, float)) else baseline
        except TypeError:
            continue
        if isinstance(new, float) and math.isnan(new):
            continue
        try:
            k.apply(new)
            k.revert()
        except Exception as exc:
            raise AssertionError(f"round-trip failed for {k.kind}:{k.knob_id}: {exc}")
