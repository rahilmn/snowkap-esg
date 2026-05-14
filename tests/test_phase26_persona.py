"""Phase 6 — persona model + storage + scorer tests.

Validates:
  - Persona model serialisation round-trip (deserialise tolerates missing
    / unknown / invalid fields)
  - Default persona for each role (cfo / ceo / analyst / other)
  - SQLite persistence (upsert, get, delete, click-affinity drift)
  - score_with_persona boost rules (esg / fw / geo / horizon / risk / click)
  - CRITICAL band floor at 0.65 (never falls below regardless of mismatch)
  - Outside-focus tag fires on zero-overlap topics
  - Cap at 1.0 — no superhuman scores
"""
from __future__ import annotations

import pytest

from engine.persona.persona_model import (
    PERSONA_QUESTIONS,
    Persona,
    default_persona_for_role,
    deserialise_persona,
)
from engine.persona.persona_scorer import (
    HOME_FLOOR,
    MAX_FINAL_SCORE,
    PersonaScoredResult,
    compute_persona_boost,
    score_with_persona,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file. Mirror the outbound_touches
    fixture pattern — monkeypatch get_data_path to point at tmp."""
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SNOWKAP_DB_BACKEND", "sqlite")

    from pathlib import Path as _Path
    import engine.config as _cfg

    _real = _cfg.get_data_path

    def _fake_get_data_path(*parts: str) -> _Path:
        # Only the DB file is redirected to tmp; ontology + outputs paths
        # go through the real resolver so transitive imports (FastAPI app
        # bootstrap loads the ontology) still find their .ttl files.
        if parts and parts[0] in ("snowkap.db",):
            return db_dir / "snowkap.db"
        if not parts:
            return db_dir
        return _real(*parts)

    monkeypatch.setattr(_cfg, "get_data_path", _fake_get_data_path)
    import engine.db.connection as _conn
    if hasattr(_conn, "get_data_path"):
        monkeypatch.setattr(
            _conn, "get_data_path", _fake_get_data_path, raising=False,
        )

    import importlib
    from engine.persona import persona_store
    importlib.reload(persona_store)
    yield
    importlib.reload(persona_store)
    # Defensive — reset ontology cache in case any transitive import loaded
    # the graph against tmp_path while our get_data_path patch was active.
    try:
        from engine.ontology.graph import reset_graph
        reset_graph()
    except Exception:
        pass


class _FakeBaseResult:
    """Duck-typed stand-in for criticality_scorer.CriticalityResult."""

    def __init__(self, score: float, band: str = "MEDIUM"):
        self.score = score
        self.band = band
        self.components = {"materiality": 0.5}
        self.role_scores = {"cfo": 0.5, "ceo": 0.5, "analyst": 0.5}


# ---------------------------------------------------------------------------
# Persona model
# ---------------------------------------------------------------------------


def test_default_persona_for_cfo_has_cfo_defaults():
    p = default_persona_for_role("u1", "cfo")
    assert p.role == "cfo"
    assert "climate" in p.esg_focus
    assert p.decision_style == "data_first"
    assert p.horizon == "annual"


def test_default_persona_for_ceo_has_3yr_horizon_and_competitive_style():
    p = default_persona_for_role("u1", "ceo")
    assert p.role == "ceo"
    assert p.horizon == "3yr"
    assert p.decision_style == "competitive_first"


def test_default_persona_for_analyst_is_regulatory_first():
    p = default_persona_for_role("u1", "analyst")
    assert p.role == "analyst"
    assert p.decision_style == "regulatory_first"


def test_default_persona_for_unknown_role_is_other():
    p = default_persona_for_role("u1", "marketing")
    assert p.role == "other"
    # Empty defaults are fine for "other"
    assert p.esg_focus == []


def test_deserialise_tolerates_missing_fields():
    p = deserialise_persona({"user_id": "u1"})
    assert p.user_id == "u1"
    assert p.role == "other"
    assert p.esg_focus == []
    assert p.horizon == "annual"


def test_deserialise_filters_invalid_enum_values():
    p = deserialise_persona({
        "user_id": "u1",
        "role": "cfo",
        "horizon": "decade",          # invalid
        "decision_style": "vibes",    # invalid
        "risk_appetite": "yolo",      # invalid
        "esg_focus": ["climate", "not_a_real_topic"],
        "frameworks": ["BRSR", "FAKE_FW"],
    })
    assert p.horizon == "annual"               # falls back to default
    assert p.decision_style == "narrative_first"
    assert p.risk_appetite == "balanced"
    assert "climate" in p.esg_focus
    assert "not_a_real_topic" not in p.esg_focus
    assert "BRSR" in p.frameworks
    assert "FAKE_FW" not in p.frameworks


def test_deserialise_clamps_affinity_to_unit_interval():
    p = deserialise_persona({
        "user_id": "u1",
        "click_affinity": {"climate": 1.5, "water": -0.3, "labour": 0.7},
    })
    assert p.click_affinity["climate"] == 1.0   # clamped down
    assert p.click_affinity["water"] == 0.0    # clamped up
    assert p.click_affinity["labour"] == 0.7


def test_persona_questions_exactly_six():
    """Plan §8.2 locks the MCQ at 6 questions (90s budget)."""
    assert len(PERSONA_QUESTIONS) == 6
    ids = [q["id"] for q in PERSONA_QUESTIONS]
    assert ids == [
        "esg_focus", "frameworks", "geographies",
        "horizon", "decision_style", "risk_appetite",
    ]


def test_persona_questions_have_required_fields():
    for q in PERSONA_QUESTIONS:
        assert "id" in q and "question" in q and "type" in q
        assert q["type"] in ("multi_select", "single_select")
        assert isinstance(q["options"], list) and len(q["options"]) >= 3
        for opt in q["options"]:
            assert "value" in opt and "label" in opt


# ---------------------------------------------------------------------------
# Persistence (SQLite store)
# ---------------------------------------------------------------------------


def test_upsert_then_get_roundtrips():
    from engine.persona.persona_store import get_persona, upsert_persona

    p = default_persona_for_role("u1", "cfo")
    p.esg_focus = ["climate", "supply_chain"]
    upsert_persona(p)

    fetched = get_persona("u1")
    assert fetched is not None
    assert fetched.user_id == "u1"
    assert fetched.role == "cfo"
    assert set(fetched.esg_focus) == {"climate", "supply_chain"}


def test_get_returns_none_for_unknown_user():
    from engine.persona.persona_store import get_persona
    assert get_persona("nobody") is None


def test_upsert_updates_existing():
    from engine.persona.persona_store import get_persona, upsert_persona

    p = default_persona_for_role("u1", "cfo")
    upsert_persona(p)
    p.horizon = "5yr_plus"
    upsert_persona(p)
    fetched = get_persona("u1")
    assert fetched.horizon == "5yr_plus"


def test_upsert_bumps_last_edited_at():
    from engine.persona.persona_store import get_persona, upsert_persona

    p = default_persona_for_role("u1", "cfo")
    p.last_edited_at = None
    upsert_persona(p)
    fetched = get_persona("u1")
    assert fetched.last_edited_at is not None


def test_delete_persona_removes_row():
    from engine.persona.persona_store import (
        delete_persona,
        get_persona,
        upsert_persona,
    )
    upsert_persona(default_persona_for_role("u1", "cfo"))
    assert get_persona("u1") is not None
    assert delete_persona("u1") is True
    assert get_persona("u1") is None
    assert delete_persona("u1") is False  # second delete is no-op


def test_record_click_affinity_increments_topic_score():
    from engine.persona.persona_store import (
        get_persona,
        record_click_affinity,
        upsert_persona,
    )
    upsert_persona(default_persona_for_role("u1", "cfo"))
    updated = record_click_affinity("u1", "climate", delta=0.3)
    assert updated is not None
    assert updated.click_affinity["climate"] == 0.3
    # Idempotent accumulation
    record_click_affinity("u1", "climate", delta=0.3)
    fetched = get_persona("u1")
    assert abs(fetched.click_affinity["climate"] - 0.6) < 1e-9


def test_record_click_affinity_clamps_at_one():
    from engine.persona.persona_store import (
        record_click_affinity,
        upsert_persona,
    )
    upsert_persona(default_persona_for_role("u1", "cfo"))
    for _ in range(20):
        record_click_affinity("u1", "climate", delta=0.3)
    from engine.persona.persona_store import get_persona
    fetched = get_persona("u1")
    assert fetched.click_affinity["climate"] == 1.0


# ---------------------------------------------------------------------------
# Persona × Criticality scoring
# ---------------------------------------------------------------------------


def _persona_focused_on_climate() -> Persona:
    return Persona(
        user_id="u1", role="cfo",
        esg_focus=["climate"], frameworks=["BRSR"], geographies=["india"],
        horizon="annual", decision_style="data_first", risk_appetite="balanced",
    )


def test_persona_boost_increases_on_full_focus_overlap():
    p = _persona_focused_on_climate()
    boost, outside = compute_persona_boost(p, article_topics=["climate"])
    # +40% from focus overlap; no other dimensions set → no additional lift
    assert abs(boost - 1.4) < 1e-6
    assert outside is False


def test_persona_outside_focus_when_no_topic_overlap():
    p = _persona_focused_on_climate()
    boost, outside = compute_persona_boost(p, article_topics=["governance"])
    # No focus overlap → boost is 1.0 (no multiplier from focus)
    assert abs(boost - 1.0) < 1e-6
    assert outside is True


def test_persona_framework_overlap_adds_30_percent():
    p = _persona_focused_on_climate()
    boost, _ = compute_persona_boost(
        p,
        article_topics=["governance"],   # no focus match
        article_frameworks=["BRSR"],     # full framework match
    )
    assert abs(boost - 1.3) < 1e-6


def test_persona_geography_overlap_adds_25_percent():
    p = _persona_focused_on_climate()
    boost, _ = compute_persona_boost(
        p,
        article_topics=["governance"],
        article_regions=["india"],
    )
    assert abs(boost - 1.25) < 1e-6


def test_persona_horizon_quarterly_penalises_long_lag():
    p = _persona_focused_on_climate()
    p.horizon = "quarterly"
    boost, _ = compute_persona_boost(
        p,
        article_topics=["governance"],     # no other lift
        cascade_dominant_lag_months=24,
    )
    assert abs(boost - 0.7) < 1e-6


def test_persona_horizon_5yr_penalises_earnings_blip():
    p = _persona_focused_on_climate()
    p.horizon = "5yr_plus"
    boost, _ = compute_persona_boost(
        p, article_topics=["governance"], event_type="earnings_blip",
    )
    assert abs(boost - 0.6) < 1e-6


def test_persona_opportunistic_boosts_positive():
    p = _persona_focused_on_climate()
    p.risk_appetite = "opportunistic"
    boost, _ = compute_persona_boost(
        p, article_topics=["governance"], polarity="positive",
    )
    assert abs(boost - 1.15) < 1e-6


def test_persona_defensive_boosts_negative():
    p = _persona_focused_on_climate()
    p.risk_appetite = "defensive"
    boost, _ = compute_persona_boost(
        p, article_topics=["governance"], polarity="negative",
    )
    assert abs(boost - 1.15) < 1e-6


def test_persona_click_affinity_boosts_top_topic():
    p = _persona_focused_on_climate()
    p.click_affinity = {"governance": 1.0}
    boost, _ = compute_persona_boost(
        p, article_topics=["governance", "supply_chain"],
    )
    # Climate (focus): no overlap → 1.0; click_affinity full → ×1.20
    assert abs(boost - 1.20) < 1e-6


def test_score_with_persona_caps_at_one():
    """Even with full overlap on every dimension, score never exceeds 1.0."""
    p = Persona(
        user_id="u1", role="cfo",
        esg_focus=["climate"], frameworks=["BRSR"], geographies=["india"],
        horizon="annual", decision_style="data_first", risk_appetite="opportunistic",
    )
    p.click_affinity = {"climate": 1.0}
    base = _FakeBaseResult(score=0.9, band="HIGH")
    out = score_with_persona(
        base, p,
        article_topics=["climate"], article_frameworks=["BRSR"],
        article_regions=["india"], polarity="positive",
    )
    assert out.score <= MAX_FINAL_SCORE
    assert out.score == 1.0  # would be ~2.5 unclamped
    assert out.persona_boost > 1.0
    assert out.base_score == 0.9


def test_score_with_persona_floors_critical_at_home_floor():
    """A CRITICAL article must never fall below 0.65 even on full mismatch."""
    p = Persona(
        user_id="u1", role="cfo",
        esg_focus=["climate"], frameworks=["BRSR"], geographies=["india"],
        horizon="quarterly",
    )
    base = _FakeBaseResult(score=0.5, band="CRITICAL")
    # Mismatch every dimension
    out = score_with_persona(
        base, p,
        article_topics=["governance"],     # no focus match
        article_frameworks=["GRI"],        # no fw match
        article_regions=["us"],            # no geo match
        cascade_dominant_lag_months=24,    # horizon penalty
    )
    # Penalty would drag score to 0.5 × 0.7 = 0.35, but CRITICAL floor saves it
    assert out.band == "CRITICAL"
    assert out.score >= HOME_FLOOR


def test_score_with_persona_marks_outside_focus_when_zero_overlap():
    p = _persona_focused_on_climate()
    base = _FakeBaseResult(score=0.5, band="MEDIUM")
    out = score_with_persona(
        base, p, article_topics=["governance"],
    )
    assert out.outside_focus is True


def test_score_with_persona_returns_serialisable_dict():
    p = _persona_focused_on_climate()
    base = _FakeBaseResult(score=0.5, band="MEDIUM")
    out = score_with_persona(base, p, article_topics=["climate"])
    import json
    js = json.dumps(out.as_dict())
    parsed = json.loads(js)
    assert "score" in parsed and "persona_boost" in parsed
    assert parsed["base_score"] == 0.5


def test_persona_boost_handles_empty_persona_dimensions_safely():
    """A bare 'other' persona with no esg_focus / frameworks / geos must
    not divide-by-zero or crash. Boost should be 1.0 (neutral)."""
    p = Persona(user_id="u1", role="other")
    boost, outside = compute_persona_boost(
        p, article_topics=["climate"], article_frameworks=["BRSR"],
    )
    assert boost == 1.0
    assert outside is False  # empty esg_focus → not outside_focus
