"""Phase 6 — persona MCQ + GET / PUT /api/me/persona endpoint tests.

Validates the live HTTP surface behind the persona MCQ flow:

  - GET  /api/me/persona/questions — static schema, exactly 6 questions
  - GET  /api/me/persona            — returns role-default with mcq_completed=False
                                        when nothing stored
  - PUT  /api/me/persona            — upserts from MCQ answers, caps lists at 3,
                                       validates enums, returns mcq_completed=True
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import mint_bearer


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Same fixture pattern as other Phase 26 tests — only redirect the DB
    file so the ontology + outputs paths still resolve correctly."""
    from pathlib import Path as _Path
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setenv("SNOWKAP_INTERNAL_EMAILS", "sales@snowkap.co.in")
    monkeypatch.setenv("SNOWKAP_DB_BACKEND", "sqlite")

    import engine.config as _cfg
    _real = _cfg.get_data_path

    def _fake(*parts: str) -> _Path:
        if parts and parts[0] in ("snowkap.db",):
            return db_dir / "snowkap.db"
        if not parts:
            return db_dir
        return _real(*parts)

    monkeypatch.setattr(_cfg, "get_data_path", _fake)
    import engine.db.connection as _conn
    if hasattr(_conn, "get_data_path"):
        monkeypatch.setattr(_conn, "get_data_path", _fake, raising=False)

    import importlib
    from engine.persona import persona_store
    importlib.reload(persona_store)

    try:
        from api.rate_limit import LOGIN_LIMITER
        LOGIN_LIMITER.reset()
    except Exception:
        pass

    yield

    importlib.reload(persona_store)
    try:
        from engine.ontology.graph import reset_graph
        reset_graph()
    except Exception:
        pass


def _token(email: str = "user@test.test") -> str:
    return mint_bearer({
        "sub": email,
        "permissions": ["read", "view_news"],
        "company_id": "test-co",
    })


# ---------------------------------------------------------------------------
# /api/me/persona/questions
# ---------------------------------------------------------------------------


def test_questions_endpoint_returns_six_mcq_items():
    from api.main import app
    client = TestClient(app)
    r = client.get("/api/me/persona/questions")
    assert r.status_code == 200
    body = r.json()
    assert "questions" in body
    qs = body["questions"]
    assert len(qs) == 6
    # Round-trip-able with PUT body
    field_ids = {q["id"] for q in qs}
    assert field_ids == {
        "esg_focus", "frameworks", "geographies",
        "horizon", "decision_style", "risk_appetite",
    }


# ---------------------------------------------------------------------------
# GET /api/me/persona
# ---------------------------------------------------------------------------


def test_get_persona_returns_role_default_when_unsaved():
    from api.main import app
    client = TestClient(app)
    r = client.get(
        "/api/me/persona",
        headers={"Authorization": f"Bearer {_token('new@test.test')}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mcq_completed"] is False
    assert body["persona"]["user_id"] == "new@test.test"
    # Default 'other' role yields empty esg_focus
    assert body["persona"]["role"] == "other"
    assert body["persona"]["esg_focus"] == []


def test_get_persona_requires_auth():
    from api.main import app
    client = TestClient(app)
    r = client.get("/api/me/persona")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PUT /api/me/persona
# ---------------------------------------------------------------------------


def test_put_persona_upserts_from_mcq_answers():
    from api.main import app
    client = TestClient(app)
    r = client.put(
        "/api/me/persona",
        json={
            "role": "cfo",
            "esg_focus": ["climate", "supply_chain"],
            "frameworks": ["BRSR", "TCFD"],
            "geographies": ["india"],
            "horizon": "annual",
            "decision_style": "data_first",
            "risk_appetite": "balanced",
        },
        headers={"Authorization": f"Bearer {_token('cfo@test.test')}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mcq_completed"] is True
    p = body["persona"]
    assert p["role"] == "cfo"
    assert set(p["esg_focus"]) == {"climate", "supply_chain"}
    assert set(p["frameworks"]) == {"BRSR", "TCFD"}
    assert p["horizon"] == "annual"

    # GET right after PUT confirms persistence + mcq_completed=true
    r2 = client.get(
        "/api/me/persona",
        headers={"Authorization": f"Bearer {_token('cfo@test.test')}"},
    )
    assert r2.status_code == 200
    assert r2.json()["mcq_completed"] is True


def test_put_persona_caps_multi_select_at_three():
    """Plan §8.2 — multi-select questions cap at 3. The endpoint truncates
    silently (better UX than 422-ing on a 4th selection)."""
    from api.main import app
    client = TestClient(app)
    r = client.put(
        "/api/me/persona",
        json={
            "role": "cfo",
            "esg_focus": ["climate", "water", "biodiversity", "labour"],  # 4
            "frameworks": ["BRSR", "GRI", "TCFD", "CSRD"],                  # 4
        },
        headers={"Authorization": f"Bearer {_token('greedy@test.test')}"},
    )
    assert r.status_code == 200
    p = r.json()["persona"]
    assert len(p["esg_focus"]) == 3
    assert len(p["frameworks"]) == 3
    # First-3 retention (the order the user picked)
    assert p["esg_focus"] == ["climate", "water", "biodiversity"]


def test_put_persona_filters_invalid_enum_values():
    """A garbage option value is dropped from the list, not 422-ing the
    whole submission. Lets old clients send slightly-stale option keys
    without breaking the save."""
    from api.main import app
    client = TestClient(app)
    r = client.put(
        "/api/me/persona",
        json={
            "role": "cfo",
            "esg_focus": ["climate", "yolo_topic"],
            "horizon": "decade",  # invalid → falls back to default 'annual'
        },
        headers={"Authorization": f"Bearer {_token('strict@test.test')}"},
    )
    assert r.status_code == 200
    p = r.json()["persona"]
    assert p["esg_focus"] == ["climate"]
    assert p["horizon"] == "annual"  # fell back to default


def test_put_persona_partial_update_preserves_existing_fields():
    """Sending only `risk_appetite` keeps every other field as previously
    saved. Lets the UI implement single-field edits without re-sending
    the whole MCQ."""
    from api.main import app
    client = TestClient(app)
    # Initial save
    client.put(
        "/api/me/persona",
        json={
            "role": "cfo",
            "esg_focus": ["climate"],
            "frameworks": ["BRSR"],
            "horizon": "3yr",
            "risk_appetite": "balanced",
        },
        headers={"Authorization": f"Bearer {_token('partial@test.test')}"},
    )
    # Single-field update
    r = client.put(
        "/api/me/persona",
        json={"risk_appetite": "opportunistic"},
        headers={"Authorization": f"Bearer {_token('partial@test.test')}"},
    )
    assert r.status_code == 200
    p = r.json()["persona"]
    assert p["risk_appetite"] == "opportunistic"
    # Untouched fields preserved
    assert p["esg_focus"] == ["climate"]
    assert p["frameworks"] == ["BRSR"]
    assert p["horizon"] == "3yr"
    assert p["role"] == "cfo"


def test_put_persona_requires_auth():
    from api.main import app
    client = TestClient(app)
    r = client.put("/api/me/persona", json={"role": "cfo"})
    assert r.status_code in (401, 403)
