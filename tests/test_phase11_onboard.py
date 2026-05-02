"""Phase 11B — Admin onboarding + empty-shell pollution fix tests.

Covers:
  * POST /api/admin/onboard requires super_admin perm (403 otherwise)
  * POST /api/admin/onboard returns 202 + seeds onboarding_status row
  * GET /api/admin/onboard/{slug}/status returns current state
  * Unknown-domain login no longer pollutes tenant_registry (Phase 11B.3)
  * Tenant is registered ONLY when it has indexed articles OR via explicit onboard
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer
from api.main import app
from engine.models import onboarding_status


def _admin_token() -> str:
    import os
    os.environ.setdefault("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")
    return mint_bearer({"sub": "sales@snowkap.com", "permissions": list(SUPER_ADMIN_PERMISSIONS)})


def _client_token() -> str:
    import os
    os.environ.setdefault("JWT_SECRET", "test-secret-xxxxxxxxxxxxxxxxxxxxxx")
    return mint_bearer({"sub": "ci@mintedit.com", "permissions": ["read", "view_news"]})


@pytest.fixture(autouse=True)
def _jwt_env():
    with patch.dict("os.environ", {"JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx"}, clear=False):
        yield


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def test_onboard_requires_super_admin():
    client = TestClient(app)
    r = client.post(
        "/api/admin/onboard",
        headers={"Authorization": f"Bearer {_client_token()}"},
        json={"name": "Hero MotoCorp"},
    )
    assert r.status_code == 403


def test_onboard_status_requires_super_admin():
    client = TestClient(app)
    r = client.get(
        "/api/admin/onboard/hero-motocorp/status",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Happy path — endpoint returns 202 and seeds status
# ---------------------------------------------------------------------------


def test_onboard_returns_202_and_seeds_status():
    client = TestClient(app)

    # Patch the queue helper so the test is fast + deterministic
    # (we're testing the HTTP boundary + status seeding, not the pipeline).
    # Phase 23 — onboarding now goes through `enqueue_onboarding` instead
    # of FastAPI BackgroundTasks; the actual pipeline runs in the
    # standalone `scripts/onboarding_worker.py` process.
    with patch("api.routes.admin_onboard.enqueue_onboarding") as mock_enqueue:
        r = client.post(
            "/api/admin/onboard",
            headers={"Authorization": f"Bearer {_admin_token()}"},
            json={"name": "Hero MotoCorp", "ticker_hint": "HEROMOTOCO.NS"},
        )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["slug"] == "hero-motocorp"
    assert body["poll_url"].endswith("/hero-motocorp/status")

    # Status row seeded with state='pending' BEFORE the task runs
    status = onboarding_status.get("hero-motocorp")
    assert status is not None
    assert status.state == "pending"

    # enqueue_onboarding was invoked with our kwargs — meaning a row
    # is now waiting in the queue for the worker to drain.
    mock_enqueue.assert_called_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["slug"] == "hero-motocorp"
    assert call_kwargs["name"] == "Hero MotoCorp"
    assert call_kwargs["ticker_hint"] == "HEROMOTOCO.NS"


def test_onboard_status_returns_current_state():
    client = TestClient(app)
    # Seed a status row directly
    onboarding_status.upsert("test-co", state="analysing", fetched=10, analysed=3, home_count=1)

    r = client.get(
        "/api/admin/onboard/test-co/status",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "test-co"
    assert body["state"] == "analysing"
    assert body["fetched"] == 10
    assert body["analysed"] == 3
    assert body["home_count"] == 1


def test_onboard_status_unknown_slug_returns_404():
    client = TestClient(app)
    r = client.get(
        "/api/admin/onboard/never-onboarded/status",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Empty-shell pollution fix
# ---------------------------------------------------------------------------


def test_unknown_domain_login_registers_tenant_immediately():
    """Phase 22 (supersedes Phase 11B.3): every corporate login lands on
    its OWN company. New prospects are registered in tenant_registry
    immediately AND scheduled for background onboarding so the dashboard
    isn't empty when the user reaches Home. The Phase 11B 'pollution'
    concern is resolved by gating the cross-tenant view in the UI and
    on the API instead of by suppressing the registry write."""
    from unittest.mock import patch as _patch
    from engine.index import tenant_registry

    client = TestClient(app)

    # Clear any prior run's row
    try:
        import sqlite3
        from engine.index.sqlite_index import DB_PATH
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM tenant_registry WHERE domain = 'random-prospect-co.test'")
            conn.commit()
    except Exception:
        pass

    # Patch the queue enqueue so we don't actually write to the
    # onboarding queue — we're testing the registry write + slug return.
    # Phase 23 — first-login onboarding kickoff routes through
    # `enqueue_onboarding`, not the in-process background task.
    with _patch("api.routes.admin_onboard.enqueue_onboarding"):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "ceo@random-prospect-co.test",
                "domain": "random-prospect-co.test",
                "designation": "ceo",
                "company_name": "Random Prospect Co",
                "name": "CEO",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()

    # Login response carries the prospect's own company_id (NOT null)
    expected_slug = tenant_registry._slug_from_domain("random-prospect-co.test")
    assert body["company_id"] == expected_slug, (
        f"login should return the prospect's own slug, got {body['company_id']!r}"
    )

    # Tenant is now registered so the super-admin's switcher shows it
    assert tenant_registry.get_tenant(expected_slug) is not None, (
        "Phase 22: prospect logins must register a tenant immediately."
    )


def test_login_from_known_target_domain_still_appears():
    """Sanity: existing target companies (with indexed articles) still
    surface in the switcher via the admin/tenants endpoint's merge of
    target-config + registry."""
    client = TestClient(app)

    r = client.get(
        "/api/admin/tenants",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200
    names = [e["slug"] for e in r.json()]
    # The 7 target companies come from config/companies.json
    for expected in ("icici-bank", "adani-power", "jsw-energy"):
        assert expected in names, f"target {expected!r} missing from switcher"
