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

    # Patch the background task to a no-op so the test is fast + deterministic
    # (we're testing the HTTP boundary + status seeding, not the pipeline).
    with patch("api.routes.admin_onboard._background_onboard") as mock_bg:
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

    # BackgroundTasks.add_task was invoked with our callable + kwargs
    mock_bg.assert_called_once()
    call_kwargs = mock_bg.call_args.kwargs
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


def test_unknown_domain_login_does_not_pollute_tenant_registry():
    """Phase 11B.3: a regular client logging in from a domain with no
    indexed articles should NOT appear in the super-admin's switcher."""
    from engine.index import tenant_registry

    client = TestClient(app)

    # Make sure this domain isn't already registered
    try:
        import sqlite3
        from engine.index.sqlite_index import DB_PATH
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM tenant_registry WHERE domain = 'random-prospect-co.test'")
            conn.commit()
    except Exception:
        pass

    # Ensure zero articles indexed for this slug (it's a never-seen domain)
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
    assert r.status_code == 200

    # Confirm NOT registered (no indexed articles → no registry entry)
    slug = tenant_registry._slug_from_domain("random-prospect-co.test")
    assert tenant_registry.get_tenant(slug) is None, (
        "Login should NOT auto-register a domain with zero indexed articles. "
        "This is the Phase 11B.3 fix."
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
