"""Tests for the quantitative_mappings.ttl + SPARQL query layer.

Defaults match the hardcoded constants — no behavioural drift.
"""
from __future__ import annotations

from engine.ontology.intelligence import (
    _DEFAULT_CONFIDENCE_MAPPING,
    _DEFAULT_HEADLINE_PRIORITY_MAPPING,
    _DEFAULT_SEVERITY_MAPPING,
    _DEFAULT_STANCE_MAPPING,
    query_band_mapping,
    query_priority_weight,
    query_quantitative_mappings_all,
    query_severity_mapping,
    query_stance_magnitude,
)


def test_band_mapping_defaults_match_hardcoded_constants():
    """The hardcoded fallback values match the pre-refactor constants."""
    assert _DEFAULT_CONFIDENCE_MAPPING == {"low": 0.30, "moderate": 0.60, "high": 0.85}


def test_severity_mapping_defaults_match_hardcoded_constants():
    assert _DEFAULT_SEVERITY_MAPPING == {
        "low": 0.25, "moderate": 0.50, "high": 0.75, "critical": 1.00,
    }


def test_stance_mapping_defaults_match_hardcoded_constants():
    assert _DEFAULT_STANCE_MAPPING == {"negative": -1.0, "neutral": 0.0, "positive": 1.0}


def test_query_band_mapping_returns_default_on_unknown_key():
    """An unknown band falls through to the 0.5 sentinel — never raises."""
    val = query_band_mapping("never-a-band")
    assert val == 0.5


def test_query_band_mapping_returns_value_for_known_bands():
    """Smoke-tests against the loaded ontology — values must be in [0, 1]."""
    for band in ("low", "moderate", "high"):
        val = query_band_mapping(band)
        assert 0.0 <= val <= 1.0


def test_query_severity_mapping_returns_critical_one():
    """Severity=critical should map to 1.0 (default and as-loaded)."""
    val = query_severity_mapping("critical")
    assert val == 1.0


def test_query_stance_magnitude_negative_is_negative():
    """negative stance must map to a negative magnitude."""
    val = query_stance_magnitude("negative")
    assert val < 0


def test_query_stance_magnitude_neutral_is_zero():
    assert query_stance_magnitude("neutral") == 0.0


def test_query_priority_weight_decays_with_rank():
    """priority 1 must score higher than priority 5."""
    assert query_priority_weight(1) > query_priority_weight(5)


def test_query_priority_weight_falls_back_on_unknown_rank():
    val = query_priority_weight(99)
    assert val == 0.1  # default sentinel


def test_query_quantitative_mappings_all_includes_4_categories():
    """The snapshot includes all 4 mapping categories."""
    all_maps = query_quantitative_mappings_all()
    assert set(all_maps.keys()) == {
        "confidence_band", "severity_band", "stakeholder_stance", "headline_priority",
    }
    # Each value is a dict (may be empty if TTL not loaded; OK either way)
    for cat, mapping in all_maps.items():
        assert isinstance(mapping, dict), f"{cat} value must be dict"
