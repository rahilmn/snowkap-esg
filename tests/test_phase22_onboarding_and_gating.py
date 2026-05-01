"""Phase 22 — Login auto-onboarding + super-admin-only cross-tenant view.

Covers:
  * Any corporate login returns the prospect's own company_id (not null)
  * Snowkap-internal logins (super-admins) get company_id=null
  * Snowkap-internal logins do NOT pollute tenant_registry
  * /api/news/feed with company_id omitted → 403 for non-admins
  * /api/news/stats with company_id omitted → 403 for non-admins
  * Both endpoints with explicit company_id → 200 for everyone
  * Both endpoints with company_id omitted → 200 for super-admins
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer
from api.main import app
from engine.index import tenant_registry
from engine.index.sqlite_index import DB_PATH


@pytest.fixture(autouse=True)
def _jwt_env():
    with patch.dict(
        "os.environ",
        {
            "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
            "SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.co.in",
        },
        clear=False,
    ):
        yield


def _admin_token() -> str:
    return mint_bearer({
        "sub": "sales@snowkap.co.in",
        "permissions": list(SUPER_ADMIN_PERMISSIONS),
        "company_id": None,
    })


def _client_token(company_id: str = "icici-bank") -> str:
    """Mint a regular-user token bound to the given tenant."""
    return mint_bearer({
        "sub": f"user@{company_id}.test",
        "permissions": ["read", "view_news"],
        "company_id": company_id,
    })


def _purge(domain: str) -> None:
    try:
        slug = tenant_registry._slug_from_domain(domain)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM tenant_registry WHERE domain = ?", (domain,))
            try:
                conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
            except sqlite3.OperationalError:
                pass  # table not yet created
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /auth/login — every corporate login lands on its own company
# ---------------------------------------------------------------------------


def test_corporate_login_assigns_own_company_id():
    """A brand-new prospect logging in must get back their own company_id
    so the dashboard auto-scopes to their company on Home."""
    domain = "phase22-prospect-a.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/login",
            json={
                "email": f"ceo@{domain}",
                "domain": domain,
                "designation": "ceo",
                "company_name": "Phase 22 Prospect A",
                "name": "Test CEO",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()

    expected_slug = tenant_registry._slug_from_domain(domain)
    assert body["company_id"] == expected_slug
    assert "super_admin" not in body["permissions"]
    assert tenant_registry.get_tenant(expected_slug) is not None


def test_returning_user_login_also_assigns_own_company_id():
    """Returning-user flow must also populate company_id from the email
    domain — otherwise sign-in via the 'Already have an account' path
    drops the user back into the empty cross-tenant view."""
    domain = "phase22-prospect-b.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/returning-user",
            json={"email": f"user@{domain}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()

    expected_slug = tenant_registry._slug_from_domain(domain)
    assert body["company_id"] == expected_slug


def test_target_company_login_uses_curated_slug():
    """If the prospect happens to be one of the 7 hardcoded targets, the
    target slug wins (no need to register, no need to onboard)."""
    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={
            "email": "analyst@icicibank.com",
            "domain": "icicibank.com",
            "designation": "analyst",
            "company_name": "ICICI Bank",
            "name": "Analyst",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_id"] == "icici-bank"


def test_super_admin_login_returns_null_company_id():
    """sales@snowkap.co.in is the only allowlisted super-admin; their
    login returns company_id=None so the dashboard defaults to the
    cross-tenant view."""
    client = TestClient(app)
    r = client.post(
        "/api/auth/login",
        json={
            "email": "sales@snowkap.co.in",
            "domain": "snowkap.co.in",
            "designation": "sales",
            "company_name": "Snowkap",
            "name": "Sales Team",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] is None
    assert "super_admin" in body["permissions"]


def test_non_allowlisted_snowkap_email_lands_on_own_tenant():
    """Architect-flagged regression: a Snowkap-domain login that is NOT
    on SNOWKAP_INTERNAL_EMAILS must land on its own concrete company —
    NOT the cross-tenant view. Otherwise any @snowkap.co.in employee
    bypasses the super_admin gate just by sharing the company domain."""
    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard"):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "engineer@snowkap.co.in",  # NOT on the allowlist
                "domain": "snowkap.co.in",
                "designation": "engineer",
                "company_name": "Snowkap",
                "name": "Random Engineer",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] is not None, (
        "Non-allowlisted Snowkap user must NOT get the cross-tenant view"
    )
    assert "super_admin" not in body["permissions"]


def test_login_kicks_off_background_onboarding_for_new_prospects():
    """New-prospect login must enqueue the onboarding pipeline so the
    dashboard isn't empty by the time the user reaches Home."""
    domain = "phase22-prospect-c.test"
    _purge(domain)

    client = TestClient(app)
    with patch("api.routes.admin_onboard._background_onboard") as mock_bg:
        r = client.post(
            "/api/auth/login",
            json={
                "email": f"ceo@{domain}",
                "domain": domain,
                "designation": "ceo",
                "company_name": "Phase 22 Prospect C",
                "name": "Test CEO",
            },
        )
    assert r.status_code == 200

    # FastAPI runs BackgroundTasks AFTER the response is sent. With
    # TestClient that happens in the same thread before .post() returns —
    # so by here the patched function has been invoked exactly once.
    assert mock_bg.called, "Background onboarding task was not scheduled"
    call_kwargs = mock_bg.call_args.kwargs
    assert call_kwargs["slug"] == tenant_registry._slug_from_domain(domain)
    assert call_kwargs["domain"] == domain


# ---------------------------------------------------------------------------
# /news/feed + /news/stats — super-admin gate on cross-tenant view
# ---------------------------------------------------------------------------


def test_news_feed_without_company_id_rejects_regular_user():
    """A non-admin token MUST NOT be able to fetch the cross-tenant feed
    by simply omitting company_id. This is the gate that keeps client A
    from seeing client B's analysis."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 403, r.text
    assert "super_admin" in r.json()["detail"].lower()


def test_news_stats_without_company_id_rejects_regular_user():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 403, r.text


def test_news_feed_with_company_id_allows_regular_user():
    """Regular users must still be able to see their own company's feed."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "articles" in body
    assert "total" in body


def test_news_stats_with_company_id_allows_regular_user():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_client_token()}"},
    )
    assert r.status_code == 200, r.text


def test_news_feed_without_company_id_allows_super_admin():
    """Super-admins ARE allowed to see the cross-tenant feed."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200, r.text


def test_news_stats_without_company_id_allows_super_admin():
    client = TestClient(app)
    r = client.get(
        "/api/news/stats",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Slug-enumeration gate — non-admin can only see their own tenant
# ---------------------------------------------------------------------------


def test_news_feed_rejects_other_tenants_slug_enumeration():
    """Architect-flagged hole: a non-admin must NOT be able to read another
    tenant's feed by simply passing `company_id=icici-bank` when their
    own JWT is bound to e.g. `yes-bank`. This is the broken access
    control fix — the JWT carries the user's own slug and the API
    rejects mismatches."""
    client = TestClient(app)
    yes_bank_token = _client_token("yes-bank")
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {yes_bank_token}"},
    )
    assert r.status_code == 403, r.text
    assert "cross-tenant" in r.json()["detail"].lower()


def test_news_stats_rejects_other_tenants_slug_enumeration():
    client = TestClient(app)
    yes_bank_token = _client_token("yes-bank")
    r = client.get(
        "/api/news/stats?company_id=icici-bank",
        headers={"Authorization": f"Bearer {yes_bank_token}"},
    )
    assert r.status_code == 403, r.text


def test_news_feed_allows_user_to_see_own_tenant():
    """Sanity: the slug-enumeration gate must not block users from
    reading their OWN tenant's data."""
    client = TestClient(app)
    icici_token = _client_token("icici-bank")
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {icici_token}"},
    )
    assert r.status_code == 200, r.text


def test_forged_unsigned_token_cannot_bypass_tenant_scope():
    """Architect-flagged hole: when REQUIRE_SIGNED_JWT=1, an attacker
    cannot forge an unsigned token claiming super_admin or another
    tenant's company_id. The decoder rejects the unsigned token, the
    request reaches the gate with empty claims, and the gate rejects.

    Without strict mode the unsigned-decode fallback in
    `api/auth_context.decode_bearer` would happily accept the forged
    token — production must run with `REQUIRE_SIGNED_JWT=1`."""
    import base64 as _b64
    import json as _json

    with patch.dict("os.environ", {"REQUIRE_SIGNED_JWT": "1"}, clear=False):
        # Build an `alg:none` JWT claiming super_admin + cross-tenant scope
        header = _b64.urlsafe_b64encode(_json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload = _b64.urlsafe_b64encode(_json.dumps({
            "sub": "attacker@evil.test",
            "permissions": ["super_admin"],
            "company_id": None,
            "exp": 9_999_999_999,
        }).encode()).rstrip(b"=").decode()
        forged = f"{header}.{payload}."

        client = TestClient(app)
        # Cross-tenant: must be rejected (unsigned token → empty claims → no super_admin)
        r = client.get(
            "/api/news/feed",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r.status_code in (401, 403), r.text
        # Slug enumeration with forged token: also rejected
        r2 = client.get(
            "/api/news/feed?company_id=icici-bank",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert r2.status_code in (401, 403), r2.text


def test_super_admin_can_query_any_tenant():
    """Super-admins are exempt from the slug-binding check — they can
    scope to any tenant."""
    client = TestClient(app)
    r = client.get(
        "/api/news/feed?company_id=icici-bank",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r.status_code == 200
    r2 = client.get(
        "/api/news/feed?company_id=yes-bank",
        headers={"Authorization": f"Bearer {_admin_token()}"},
    )
    assert r2.status_code == 200
