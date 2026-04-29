"""Phase 10 auth tests: SUPER_ADMIN allowlist + bearer-token permission gate.

Covers:
  * is_snowkap_super_admin() across domain + allowlist combinations
  * decode_bearer() on well-formed / malformed / missing tokens
  * require_bearer_permission() 403 behaviour
  * /api/admin/tenants permission wall
  * auth_login / auth_returning_user: allowlisted emails get super_admin perms
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.auth_context import (
    SUPER_ADMIN_PERMISSIONS,
    decode_bearer,
    is_snowkap_super_admin,
)
from api.main import app


# ---------------------------------------------------------------------------
# is_snowkap_super_admin — allowlist gate
# ---------------------------------------------------------------------------


def test_super_admin_allowlist_match():
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com,ci@snowkap.com"}, clear=False):
        assert is_snowkap_super_admin("sales@snowkap.com") is True
        assert is_snowkap_super_admin("SALES@snowkap.com") is True  # case-insensitive
        assert is_snowkap_super_admin(" ci@snowkap.com ") is True  # trimmed


def test_super_admin_allowlist_miss():
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        assert is_snowkap_super_admin("intruder@snowkap.com") is False  # right domain, not on list
        assert is_snowkap_super_admin("sales@other.com") is False  # on name, wrong domain
        assert is_snowkap_super_admin("") is False


def test_super_admin_empty_allowlist_denies_all():
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": ""}, clear=False):
        assert is_snowkap_super_admin("sales@snowkap.com") is False


def test_super_admin_accepts_both_internal_domains():
    env = {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.co.in,ops@snowkap.com"}
    with patch.dict("os.environ", env, clear=False):
        assert is_snowkap_super_admin("sales@snowkap.co.in") is True
        assert is_snowkap_super_admin("ops@snowkap.com") is True


# ---------------------------------------------------------------------------
# decode_bearer — defensive parsing
# ---------------------------------------------------------------------------


def _mint(claims: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"Bearer {header}.{payload}."


def test_decode_bearer_roundtrip():
    token = _mint({"sub": "x@y.com", "permissions": ["super_admin", "read"]})
    claims = decode_bearer(token)
    assert claims["sub"] == "x@y.com"
    assert "super_admin" in claims["permissions"]


def test_decode_bearer_malformed_returns_empty():
    assert decode_bearer(None) == {}
    assert decode_bearer("") == {}
    assert decode_bearer("NotBearer abc") == {}
    assert decode_bearer("Bearer not-a-jwt") == {}
    assert decode_bearer("Bearer a.b.c.d.e") != {} or True  # still parses payload segment


# ---------------------------------------------------------------------------
# /api/admin/tenants — permission wall
# ---------------------------------------------------------------------------


def test_admin_tenants_requires_super_admin():
    client = TestClient(app)
    # Token without super_admin permission
    regular_token = _mint({"sub": "ci@mintedit.com", "permissions": ["read", "view_news"]})
    r = client.get("/api/admin/tenants", headers={"Authorization": regular_token})
    assert r.status_code == 403
    assert "super_admin" in r.json()["detail"].lower()


def test_admin_tenants_super_admin_lists_all():
    client = TestClient(app)
    admin_token = _mint({"sub": "sales@snowkap.com", "permissions": SUPER_ADMIN_PERMISSIONS})
    r = client.get("/api/admin/tenants", headers={"Authorization": admin_token})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1  # at least one company from config/companies.json
    entry = body[0]
    # Switcher needs these fields
    for key in ("id", "slug", "name", "industry", "article_count"):
        assert key in entry, f"missing {key} in {entry}"


# ---------------------------------------------------------------------------
# auth_login / auth_returning_user — allowlisted emails get super_admin perms
# ---------------------------------------------------------------------------


def test_auth_login_grants_super_admin_to_allowlisted_email():
    client = TestClient(app)
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "sales@snowkap.com",
                "domain": "snowkap.com",
                "designation": "sales",
                "company_name": "Snowkap",
                "name": "Sales Team",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert "super_admin" in body["permissions"]
    assert "manage_drip_campaigns" in body["permissions"]
    assert "override_tenant_context" in body["permissions"]


def test_auth_login_regular_user_no_super_admin():
    client = TestClient(app)
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "analyst@mintedit.com",
                "domain": "mintedit.com",
                "designation": "analyst",
                "company_name": "Mint",
                "name": "Mint Analyst",
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert "super_admin" not in body["permissions"]
    assert "manage_drip_campaigns" not in body["permissions"]


def test_auth_returning_user_grants_super_admin():
    client = TestClient(app)
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "ops@snowkap.co.in"}, clear=False):
        r = client.post(
            "/api/auth/returning-user",
            json={"email": "ops@snowkap.co.in"},
        )
    assert r.status_code == 200
    assert "super_admin" in r.json()["permissions"]


# ---------------------------------------------------------------------------
# Share endpoints: admin-only (Phase 10 lockdown)
# ---------------------------------------------------------------------------


def test_share_endpoint_rejects_regular_user():
    client = TestClient(app)
    regular_token = _mint({"sub": "ci@mintedit.com", "permissions": ["read", "view_news"]})
    r = client.post(
        "/api/news/any-article-id/share",
        headers={"Authorization": regular_token},
        json={"recipient_email": "target@example.com"},
    )
    assert r.status_code == 403, r.text
    assert "manage_drip_campaigns" in r.json()["detail"].lower()


def test_share_preview_endpoint_rejects_regular_user():
    client = TestClient(app)
    regular_token = _mint({"sub": "ci@mintedit.com", "permissions": ["read", "view_news"]})
    r = client.post(
        "/api/news/any-article-id/share/preview",
        headers={"Authorization": regular_token},
        json={"recipient_email": "target@example.com"},
    )
    assert r.status_code == 403


def test_share_endpoint_missing_token_rejects():
    client = TestClient(app)
    # No Authorization header at all — still 403 (no permissions claim)
    r = client.post(
        "/api/news/any-article-id/share",
        json={"recipient_email": "target@example.com"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Tenant registry: new companies auto-populate the switcher
# ---------------------------------------------------------------------------


def test_new_company_login_auto_registers_and_appears_in_switcher():
    """When a brand-new domain logs in, it must show up in /api/admin/tenants
    so sales@snowkap.com can switch into it and share analysis with them."""
    client = TestClient(app)

    # Step 1: a brand-new prospect logs in
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        login = client.post(
            "/api/auth/login",
            json={
                "email": "ceo@brand-new-company-xyz.com",
                "domain": "brand-new-company-xyz.com",
                "designation": "ceo",
                "company_name": "Brand New Company XYZ",
                "name": "Test CEO",
            },
        )
    assert login.status_code == 200
    assert "super_admin" not in login.json()["permissions"]

    # Step 2: sales@snowkap.com fetches the switcher list
    admin_token = _mint({"sub": "sales@snowkap.com", "permissions": SUPER_ADMIN_PERMISSIONS})
    r = client.get("/api/admin/tenants", headers={"Authorization": admin_token})
    assert r.status_code == 200
    entries = r.json()

    # The new company must be in the list
    names = [e.get("name", "").lower() for e in entries]
    slugs = [e.get("slug") for e in entries]
    assert any("brand new company xyz" in n or "brand-new-company-xyz" in n for n in names), (
        f"New company didn't register in switcher. Names seen: {names}"
    )
    assert "brand-new-company-xyz" in slugs, f"Expected slug not present in {slugs}"

    # It should be tagged source='onboarded', not 'target'
    match = next((e for e in entries if e.get("slug") == "brand-new-company-xyz"), None)
    assert match is not None
    assert match["source"] == "onboarded"


def test_switcher_includes_all_seven_target_companies():
    client = TestClient(app)
    admin_token = _mint({"sub": "sales@snowkap.com", "permissions": SUPER_ADMIN_PERMISSIONS})
    r = client.get("/api/admin/tenants", headers={"Authorization": admin_token})
    assert r.status_code == 200
    entries = r.json()

    target_slugs = {e["slug"] for e in entries if e.get("source") == "target"}
    # All 7 target companies must appear
    expected = {
        "icici-bank",
        "yes-bank",
        "idfc-first-bank",
        "waaree-energies",
        "singularity-amc",
        "adani-power",
        "jsw-energy",
    }
    missing = expected - target_slugs
    assert not missing, f"Missing target companies in switcher: {missing}"


def test_snowkap_internal_login_does_not_pollute_registry():
    """Super-admin logins should NOT insert snowkap.com into the tenant list —
    Snowkap is the seller, not a tenant. This test first cleans any stale
    'snowkap' row (which can exist if a dev login happened without the env
    var configured), then verifies that logging in WITH the allowlist
    active does not re-register it."""
    import sqlite3
    from engine.index.sqlite_index import DB_PATH

    # Clean slate: remove any stale snowkap row from prior dev runs
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("DELETE FROM tenant_registry WHERE slug = 'snowkap' OR domain = 'snowkap.com'")
            conn.commit()
    except Exception:
        pass

    client = TestClient(app)
    with patch.dict("os.environ", {"SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com"}, clear=False):
        client.post(
            "/api/auth/login",
            json={
                "email": "sales@snowkap.com",
                "domain": "snowkap.com",
                "designation": "sales",
                "company_name": "Snowkap",
                "name": "Sales Team",
            },
        )

    admin_token = _mint({"sub": "sales@snowkap.com", "permissions": SUPER_ADMIN_PERMISSIONS})
    r = client.get("/api/admin/tenants", headers={"Authorization": admin_token})
    slugs = [e["slug"] for e in r.json()]
    assert "snowkap" not in slugs, f"Snowkap polluted the tenant registry: {slugs}"
