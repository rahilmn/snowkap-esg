"""Phase 6 §8.3 — feed endpoint wiring tests.

Validates that ``GET /api/news/feed?personalise=true`` actually applies
the persona re-ranker against real (mocked) feed rows + payloads, and
that the un-personalised path is byte-identical to the legacy behaviour.

Mocks ``sqlite_index.query_feed`` and ``_load_payload`` so we don't
require a populated DB. The persona itself is loaded via the real
``persona_store`` against an isolated SQLite file (autouse fixture).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import mint_bearer


@pytest.fixture(autouse=True)
def _jwt_env(tmp_path, monkeypatch):
    """Per-test isolation:
      - JWT secret so mint_bearer + require_auth agree
      - tmp SQLite file so persona writes don't persist across tests
      - reset news router singleton (touched indirectly via /metrics)
    """
    from pathlib import Path as _Path
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")
    monkeypatch.setenv("SNOWKAP_INTERNAL_EMAILS", "sales@snowkap.co.in")
    monkeypatch.setenv("SNOWKAP_DB_BACKEND", "sqlite")

    # Redirect ONLY the DB file to tmp; ontology + outputs go through real
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

    # Reset persona_store schema-ready latch so the fresh DB gets the table
    import importlib
    from engine.persona import persona_store
    importlib.reload(persona_store)

    # Clear the rate limiter between tests (defensive — login isn't used here
    # but importing api.main loads it)
    try:
        from api.rate_limit import LOGIN_LIMITER
        LOGIN_LIMITER.reset()
    except Exception:
        pass

    yield

    importlib.reload(persona_store)
    # Drop ontology cache loaded during tests against tmp data dir
    try:
        from engine.ontology.graph import reset_graph
        reset_graph()
    except Exception:
        pass


def _client_token(company_id: str = "test-co", email: str | None = None) -> str:
    return mint_bearer({
        "sub": email or f"user@{company_id}.test",
        "permissions": ["read", "view_news"],
        "company_id": company_id,
    })


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


def _stub_rows() -> list[dict]:
    """Two feed rows: a higher-criticality governance article + a lower
    climate article. A climate-focused persona should re-rank the climate
    one above governance even though governance has the higher base score."""
    return [
        {
            "id": "gov-1",
            "company_slug": "test-co",
            "title": "Governance shake-up",
            "json_path": "data/outputs/test-co/insights/gov-1.json",
            "criticality_score": 0.55,
            "criticality_band": "MEDIUM",
            "url": "http://example.com/gov-1",
            "published_at": "2026-05-01T00:00:00Z",
            "tier": "HOME",
            "esg_pillar": "G",
            "primary_theme": "governance",
        },
        {
            "id": "climate-1",
            "company_slug": "test-co",
            "title": "Climate exposure",
            "json_path": "data/outputs/test-co/insights/climate-1.json",
            "criticality_score": 0.50,
            "criticality_band": "MEDIUM",
            "url": "http://example.com/climate-1",
            "published_at": "2026-05-01T00:00:00Z",
            "tier": "HOME",
            "esg_pillar": "E",
            "primary_theme": "climate",
        },
    ]


def _stub_payloads() -> dict[str, dict]:
    return {
        "data/outputs/test-co/insights/gov-1.json": {
            "pipeline": {"themes": {"theme_tags": ["governance"]}},
        },
        "data/outputs/test-co/insights/climate-1.json": {
            "pipeline": {"themes": {"theme_tags": ["climate"]}},
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_feed_without_personalise_param_preserves_original_order():
    """When `personalise` is not passed, the response must NOT carry
    persona fields and the order must match the underlying query order."""
    from api.main import app
    client = TestClient(app)
    rows = _stub_rows()
    payloads = _stub_payloads()

    with patch(
        "api.routes.legacy_adapter.sqlite_index.query_feed",
        return_value=rows,
    ), patch(
        "api.routes.legacy_adapter._load_payload",
        side_effect=lambda p: payloads.get(p),
    ), patch(
        "api.routes.legacy_adapter.sqlite_index.count",
        return_value=2,
    ):
        r = client.get(
            "/api/news/feed?company_id=test-co",
            headers={"Authorization": f"Bearer {_client_token()}"},
        )

    assert r.status_code == 200
    body = r.json()
    arts = body["articles"]
    assert len(arts) == 2
    assert arts[0]["id"] == "gov-1"
    assert arts[1]["id"] == "climate-1"
    # Without personalise, no persona fields leak in
    assert "personalised_score" not in arts[0]
    assert "outside_focus" not in arts[0]


def test_feed_with_personalise_climate_focus_promotes_climate_above_gov():
    """A climate-focused persona reorders so climate-1 appears first."""
    from api.main import app
    from engine.persona.persona_store import upsert_persona
    from engine.persona.persona_model import default_persona_for_role

    # Seed a climate-focused persona for the requesting user
    p = default_persona_for_role("climate.cfo@test-co.test", "cfo")
    p.esg_focus = ["climate"]
    upsert_persona(p)

    client = TestClient(app)
    rows = _stub_rows()
    payloads = _stub_payloads()

    with patch(
        "api.routes.legacy_adapter.sqlite_index.query_feed",
        return_value=rows,
    ), patch(
        "api.routes.legacy_adapter._load_payload",
        side_effect=lambda p: payloads.get(p),
    ), patch(
        "api.routes.legacy_adapter.sqlite_index.count",
        return_value=2,
    ):
        r = client.get(
            "/api/news/feed?company_id=test-co&personalise=true",
            headers={
                "Authorization": f"Bearer {_client_token(email='climate.cfo@test-co.test')}",
            },
        )

    assert r.status_code == 200
    arts = r.json()["articles"]
    assert len(arts) == 2
    # Climate boost (0.50 × 1.4 = 0.70) > Gov no-boost (0.55) → climate first
    assert arts[0]["id"] == "climate-1"
    assert arts[1]["id"] == "gov-1"
    # Persona signals surface for the UI badge
    assert "personalised_score" in arts[0]
    assert arts[0]["personalised_score"] > arts[0].get(
        "criticality_score", 0,
    )
    # Climate article overlaps focus → not outside_focus
    assert arts[0]["outside_focus"] is False
    # Governance article has zero focus overlap → outside_focus tag
    assert arts[1]["outside_focus"] is True


def test_feed_with_personalise_falls_back_to_default_persona_when_user_has_none():
    """A user who never completed the MCQ still gets the personalise=true
    response (fallback persona is 'other' role with empty esg_focus → no
    boosts → behaviour identical to un-personalised)."""
    from api.main import app
    client = TestClient(app)
    rows = _stub_rows()
    payloads = _stub_payloads()

    # No persona stored for this user
    with patch(
        "api.routes.legacy_adapter.sqlite_index.query_feed",
        return_value=rows,
    ), patch(
        "api.routes.legacy_adapter._load_payload",
        side_effect=lambda p: payloads.get(p),
    ), patch(
        "api.routes.legacy_adapter.sqlite_index.count",
        return_value=2,
    ):
        r = client.get(
            "/api/news/feed?company_id=test-co&personalise=true",
            headers={
                "Authorization": f"Bearer {_client_token(email='nobody@test-co.test')}",
            },
        )

    assert r.status_code == 200
    arts = r.json()["articles"]
    assert len(arts) == 2
    # No esg_focus → boost = 1.0 → personalised_score == base
    assert arts[0]["personalised_score"] == 0.55  # gov (highest base)
    assert arts[1]["personalised_score"] == 0.50  # climate
    # Empty focus → outside_focus is False (vacuous, not a positive miss)
    assert arts[0]["outside_focus"] is False
    assert arts[1]["outside_focus"] is False


def test_feed_personalise_path_does_not_drop_any_rows():
    """Discoverability invariant — personalise can REORDER but never DROPs."""
    from api.main import app
    from engine.persona.persona_store import upsert_persona
    from engine.persona.persona_model import default_persona_for_role

    p = default_persona_for_role("strict@test-co.test", "cfo")
    p.esg_focus = ["water"]  # zero overlap with any test row
    upsert_persona(p)

    client = TestClient(app)
    rows = _stub_rows()
    payloads = _stub_payloads()

    with patch(
        "api.routes.legacy_adapter.sqlite_index.query_feed",
        return_value=rows,
    ), patch(
        "api.routes.legacy_adapter._load_payload",
        side_effect=lambda p: payloads.get(p),
    ), patch(
        "api.routes.legacy_adapter.sqlite_index.count",
        return_value=2,
    ):
        r = client.get(
            "/api/news/feed?company_id=test-co&personalise=true",
            headers={
                "Authorization": f"Bearer {_client_token(email='strict@test-co.test')}",
            },
        )

    assert r.status_code == 200
    arts = r.json()["articles"]
    assert {a["id"] for a in arts} == {"gov-1", "climate-1"}
    # All rows tagged outside_focus given zero water overlap
    assert all(a["outside_focus"] for a in arts)
