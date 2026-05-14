"""Phase 6 §8.3 — feed re-ranker tests.

Validates `apply_persona_to_feed` re-orders rows per persona match
without dropping any (discoverability invariant), tags outside-focus
rows for the UI badge, and floors CRITICAL articles at the home-page
floor regardless of persona mismatch.

The helper takes a `load_payload` callback so tests can stub payloads
without touching disk.
"""
from __future__ import annotations

from engine.persona.persona_model import Persona
from engine.persona.persona_rerank import (
    _extract_event_type,
    _extract_frameworks,
    _extract_polarity,
    _extract_regions,
    _extract_topics,
    apply_persona_to_feed,
)


# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------


def test_extract_topics_from_theme_tags_list_of_strings():
    payload = {"pipeline": {"themes": {"theme_tags": ["climate", "water"]}}}
    assert _extract_topics(payload) == ["climate", "water"]


def test_extract_topics_promotes_primary_theme_to_index_zero():
    payload = {
        "pipeline": {
            "themes": {
                "theme_tags": ["water", "labour"],
                "primary_theme": "climate",
            },
        },
    }
    out = _extract_topics(payload)
    assert out[0] == "climate"
    assert "water" in out and "labour" in out


def test_extract_topics_handles_dict_entries():
    """Some pipeline outputs return [{topic: ...}, ...] not [str, ...]."""
    payload = {
        "pipeline": {
            "themes": {
                "theme_tags": [
                    {"topic": "climate"},
                    {"name": "water"},
                    {"label": "labour"},
                ],
            },
        },
    }
    out = _extract_topics(payload)
    assert "climate" in out and "water" in out and "labour" in out


def test_extract_frameworks_strips_section_codes_to_family():
    """BRSR:P6:Q14 → BRSR; deduped + sorted."""
    payload = {
        "pipeline": {
            "frameworks": [
                {"code": "BRSR:P6:Q14"},
                {"code": "BRSR:P9:Q1"},  # duplicate family
                {"code": "GRI:303"},
                {"code": "TCFD-Strategy-c"},
            ],
        },
    }
    out = _extract_frameworks(payload)
    assert out == sorted(["BRSR", "GRI", "TCFD-STRATEGY-C"])


def test_extract_regions_lowercases():
    payload = {"pipeline": {"geographic": {"regions": ["India", "EU"]}}}
    assert _extract_regions(payload) == ["india", "eu"]


def test_extract_polarity_from_insight():
    assert _extract_polarity({"insight": {"event_polarity": "positive"}}) == "positive"
    assert _extract_polarity({}) is None


def test_extract_event_type_from_pipeline_event():
    payload = {"pipeline": {"event": {"event_id": "event_contract_win"}}}
    assert _extract_event_type(payload) == "event_contract_win"


# ---------------------------------------------------------------------------
# Re-ranker behaviour
# ---------------------------------------------------------------------------


def _persona_focused_on_climate() -> Persona:
    return Persona(
        user_id="u1", role="cfo",
        esg_focus=["climate"], frameworks=["BRSR"], geographies=["india"],
        horizon="annual", decision_style="data_first", risk_appetite="balanced",
    )


def test_rerank_returns_empty_for_empty_input():
    out = apply_persona_to_feed([], _persona_focused_on_climate(), lambda _: None)
    assert out == []


def test_rerank_promotes_focus_match_above_higher_baseline():
    """A climate-focused user sees a 0.50 climate article rank ABOVE a
    0.55 governance article — focus match overrides a small base gap."""
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "gov", "criticality_score": 0.55, "criticality_band": "MEDIUM",
            "json_path": "gov.json",
        },
        {
            "id": "climate", "criticality_score": 0.50, "criticality_band": "MEDIUM",
            "json_path": "climate.json",
        },
    ]
    payloads = {
        "gov.json": {"pipeline": {"themes": {"theme_tags": ["governance"]}}},
        "climate.json": {"pipeline": {"themes": {"theme_tags": ["climate"]}}},
    }
    out = apply_persona_to_feed(rows, p, lambda path: payloads.get(path))
    # Climate (0.50 × 1.4 = 0.70) > governance (0.55 × 1.0 = 0.55)
    assert out[0]["id"] == "climate"
    assert out[1]["id"] == "gov"


def test_rerank_marks_outside_focus_for_zero_overlap():
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "gov", "criticality_score": 0.5, "criticality_band": "MEDIUM",
            "json_path": "gov.json",
        },
    ]
    payloads = {
        "gov.json": {"pipeline": {"themes": {"theme_tags": ["governance"]}}},
    }
    out = apply_persona_to_feed(rows, p, lambda p: payloads.get(p))
    assert out[0]["outside_focus"] is True
    assert out[0]["personalised_score"] == 0.5  # no boost


def test_rerank_floors_critical_at_home_floor_despite_mismatch():
    """A CRITICAL article scored 0.40 stays at >=0.65 even when the
    persona is opposite-focused. The discoverability guarantee."""
    p = _persona_focused_on_climate()
    p.horizon = "quarterly"
    rows = [
        {
            "id": "crit", "criticality_score": 0.40, "criticality_band": "CRITICAL",
            "json_path": "crit.json",
        },
    ]
    payloads = {
        "crit.json": {
            "pipeline": {"themes": {"theme_tags": ["governance"]}},
            "insight": {"cascade": {"dominant_lag_months": 24}},
        },
    }
    out = apply_persona_to_feed(rows, p, lambda p: payloads.get(p))
    # Mismatch + horizon penalty would drag score; CRITICAL floor saves it
    assert out[0]["criticality_band"] == "CRITICAL"
    assert out[0]["personalised_score"] >= 0.65
    assert out[0]["outside_focus"] is True


def test_rerank_caps_at_one():
    p = Persona(
        user_id="u1", role="cfo",
        esg_focus=["climate"], frameworks=["BRSR"], geographies=["india"],
        horizon="annual", decision_style="data_first", risk_appetite="opportunistic",
    )
    rows = [
        {
            "id": "boosted", "criticality_score": 0.95, "criticality_band": "HIGH",
            "json_path": "p.json",
        },
    ]
    payloads = {
        "p.json": {
            "pipeline": {
                "themes": {"theme_tags": ["climate"]},
                "frameworks": [{"code": "BRSR:P6"}],
                "geographic": {"regions": ["India"]},
            },
            "insight": {"event_polarity": "positive"},
        },
    }
    out = apply_persona_to_feed(rows, p, lambda p: payloads.get(p))
    assert out[0]["personalised_score"] == 1.0


def test_rerank_keeps_unscored_rows_at_end():
    """Rows with NULL criticality_score (pre-Phase-1.7-backfill) are kept
    but sorted last so they don't displace scored ones."""
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "unscored", "criticality_score": None, "criticality_band": None,
            "json_path": "u.json",
        },
        {
            "id": "scored", "criticality_score": 0.5, "criticality_band": "MEDIUM",
            "json_path": "s.json",
        },
    ]
    payloads = {
        "u.json": {"pipeline": {"themes": {"theme_tags": ["climate"]}}},
        "s.json": {"pipeline": {"themes": {"theme_tags": ["governance"]}}},
    }
    out = apply_persona_to_feed(rows, p, lambda p: payloads.get(p))
    assert out[0]["id"] == "scored"
    assert out[1]["id"] == "unscored"
    assert out[1]["personalised_score"] is None


def test_rerank_is_resilient_to_payload_load_failure():
    """If load_payload raises, we fall back to no-modulation rather than
    dropping the row."""
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "bad", "criticality_score": 0.5, "criticality_band": "MEDIUM",
            "json_path": "bad.json",
        },
    ]
    def _raise(_path: str) -> dict:
        raise FileNotFoundError("boom")
    out = apply_persona_to_feed(rows, p, _raise)
    assert len(out) == 1
    assert out[0]["personalised_score"] == 0.5
    assert out[0]["persona_boost"] == 1.0


def test_rerank_does_not_mutate_input_rows():
    """Original row dicts must stay clean — we add keys to copies."""
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "x", "criticality_score": 0.5, "criticality_band": "MEDIUM",
            "json_path": "x.json",
        },
    ]
    out = apply_persona_to_feed(rows, p, lambda _: {})
    # Output row has the new fields
    assert "personalised_score" in out[0]
    # Input row does NOT
    assert "personalised_score" not in rows[0]
    assert "persona_boost" not in rows[0]
    assert "outside_focus" not in rows[0]


def test_rerank_handles_string_criticality_score():
    """Some DB backends return REAL columns as Decimal/str — defensive coercion."""
    p = _persona_focused_on_climate()
    rows = [
        {
            "id": "x", "criticality_score": "0.5", "criticality_band": "MEDIUM",
            "json_path": "x.json",
        },
    ]
    out = apply_persona_to_feed(rows, p, lambda _: {})
    assert isinstance(out[0]["personalised_score"], float)
    assert out[0]["personalised_score"] == 0.5
