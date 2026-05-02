"""Phase 11A — signed JWT verification tests.

Covers:
  * mint_bearer() produces an HS256-signed token decodable by jwt.decode
  * decode_bearer() accepts valid signed tokens
  * decode_bearer() rejects tokens with tampered signatures
  * decode_bearer() rejects expired tokens
  * Unsigned tokens are always rejected (legacy compat window removed)
  * /api/auth/login now returns a signed token
"""

from __future__ import annotations

import base64
import json
import os
import time
from unittest.mock import patch

import jwt as _jwt
import pytest
from fastapi.testclient import TestClient

from api.auth_context import (
    SUPER_ADMIN_PERMISSIONS,
    decode_bearer,
    mint_bearer,
)
from api.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_with_secret(**extra):
    base = {"JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx", **extra}
    return patch.dict("os.environ", base, clear=False)


def _mint_unsigned(payload: dict) -> str:
    """Build an unsigned base64 JWT the way legacy clients produced them."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


# ---------------------------------------------------------------------------
# mint_bearer + decode_bearer roundtrip
# ---------------------------------------------------------------------------


def test_mint_bearer_returns_verifiable_token():
    with _env_with_secret():
        token = mint_bearer({"sub": "sales@snowkap.com", "permissions": ["super_admin"]})
    # 3 dot-separated segments = signed JWT (unsigned shim emits 2 + trailing dot)
    assert token.count(".") == 2
    assert all(token.split(".")), "no empty segments"

    with _env_with_secret():
        claims = decode_bearer(f"Bearer {token}")
    assert claims["sub"] == "sales@snowkap.com"
    assert "super_admin" in claims["permissions"]
    assert "iat" in claims and "exp" in claims


def test_mint_bearer_requires_secret():
    # Empty secret → refuse to sign
    with patch.dict("os.environ", {"JWT_SECRET": ""}, clear=False):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            mint_bearer({"sub": "x"})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_decode_rejects_tampered_signature():
    with _env_with_secret():
        token = mint_bearer({"sub": "x", "permissions": ["super_admin"]})
        # Flip one character in the signature segment
        header, payload, sig = token.split(".")
        bad_sig = sig[:-2] + ("xx" if not sig.endswith("xx") else "aa")
        tampered = f"Bearer {header}.{payload}.{bad_sig}"
        assert decode_bearer(tampered) == {}


def test_decode_rejects_expired_token():
    """Expired tokens are always rejected."""
    with _env_with_secret():
        now = int(time.time())
        # Manually craft an expired but otherwise valid token
        expired = _jwt.encode(
            {"sub": "x", "permissions": ["super_admin"], "iat": now - 7200, "exp": now - 3600},
            "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
            algorithm="HS256",
        )
        assert decode_bearer(f"Bearer {expired}") == {}


def test_decode_rejects_wrong_secret():
    """Token signed with different secret is rejected."""
    other = _jwt.encode({"sub": "x"}, "other-secret", algorithm="HS256")
    with _env_with_secret():
        assert decode_bearer(f"Bearer {other}") == {}


# ---------------------------------------------------------------------------
# Unsigned-token rejection (compat window removed — Task #4)
# ---------------------------------------------------------------------------


def test_unsigned_tokens_always_rejected():
    """Legacy `alg:none` base64 tokens are unconditionally rejected.

    The pre-Phase-11 compat fallback in `decode_bearer` is gone — there is
    no env flag that can re-enable it, so a future config regression cannot
    silently re-open the cross-tenant bypass.
    """
    unsigned = _mint_unsigned({"sub": "legacy@x.com", "permissions": ["read"]})
    # No env flag, with secret set
    with _env_with_secret():
        assert decode_bearer(f"Bearer {unsigned}") == {}
    # Even setting the old REQUIRE_SIGNED_JWT flag back to empty/0 must NOT
    # re-enable the unsigned path — the helper has been deleted entirely.
    with _env_with_secret(REQUIRE_SIGNED_JWT=""):
        assert decode_bearer(f"Bearer {unsigned}") == {}
    with _env_with_secret(REQUIRE_SIGNED_JWT="0"):
        assert decode_bearer(f"Bearer {unsigned}") == {}


def test_signed_tokens_still_accepted():
    with _env_with_secret():
        token = mint_bearer({"sub": "x", "permissions": ["super_admin"]})
        claims = decode_bearer(f"Bearer {token}")
    assert claims["sub"] == "x"
    assert "_unsigned" not in claims


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_decode_no_token_returns_empty():
    assert decode_bearer(None) == {}
    assert decode_bearer("") == {}
    assert decode_bearer("NotBearer abc") == {}


def test_decode_with_secret_missing_returns_empty():
    """If JWT_SECRET is missing at runtime, refuse to decode anything."""
    signed = _jwt.encode({"sub": "x"}, "any-key", algorithm="HS256")
    with patch.dict("os.environ", {"JWT_SECRET": ""}, clear=False):
        assert decode_bearer(f"Bearer {signed}") == {}


# ---------------------------------------------------------------------------
# /api/auth/login end-to-end
# ---------------------------------------------------------------------------


def test_login_endpoint_returns_signed_token():
    client = TestClient(app)
    env = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com",
    }
    with patch.dict("os.environ", env, clear=False):
        r = client.post(
            "/api/auth/login",
            json={
                "email": "sales@snowkap.com",
                "domain": "snowkap.com",
                "designation": "sales",
                "company_name": "Snowkap",
                "name": "Sales",
            },
        )
    assert r.status_code == 200
    token = r.json()["token"]
    # 3 segments = real signed JWT
    assert token.count(".") == 2, f"expected signed JWT, got {token!r}"

    # And the token is actually verifiable
    with patch.dict("os.environ", env, clear=False):
        claims = decode_bearer(f"Bearer {token}")
    assert claims["sub"] == "sales@snowkap.com"
    assert "super_admin" in claims["permissions"]


def test_login_token_usable_on_admin_tenants_endpoint():
    """End-to-end: login → use token on admin endpoint → 200."""
    client = TestClient(app)
    env = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_INTERNAL_EMAILS": "sales@snowkap.com",
    }
    with patch.dict("os.environ", env, clear=False):
        login = client.post(
            "/api/auth/login",
            json={
                "email": "sales@snowkap.com",
                "domain": "snowkap.com",
                "designation": "sales",
                "company_name": "Snowkap",
                "name": "Sales",
            },
        )
        token = login.json()["token"]
        r = client.get("/api/admin/tenants", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert len(body) >= 1


# ---------------------------------------------------------------------------
# require_auth tightening — read-only feed endpoints
# ---------------------------------------------------------------------------


def test_require_auth_rejects_garbage_bearer_in_strict_mode():
    """Phase 11A: require_auth now verifies Bearer JWTs, not just 'non-empty'.
    In strict mode (REQUIRE_SIGNED_JWT=1), garbage tokens → 401."""
    client = TestClient(app)
    env = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "REQUIRE_SIGNED_JWT": "1",
    }
    with patch.dict("os.environ", env, clear=False):
        r = client.get("/api/companies/", headers={"Authorization": "Bearer garbage-not-a-jwt"})
    assert r.status_code == 401


def test_require_auth_accepts_valid_jwt_in_strict_mode():
    client = TestClient(app)
    env = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "REQUIRE_SIGNED_JWT": "1",
    }
    with patch.dict("os.environ", env, clear=False):
        token = mint_bearer({"sub": "test@x.com", "permissions": ["read"]})
        r = client.get("/api/companies/", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


def test_require_auth_dev_mode_still_permissive():
    """Dev mode (no SNOWKAP_API_KEY, no REQUIRE_SIGNED_JWT) stays open for
    local development ergonomics."""
    client = TestClient(app)
    with patch.dict("os.environ", {"JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx"}, clear=False):
        # Explicitly unset both strict-mode signals
        for var in ("SNOWKAP_API_KEY", "REQUIRE_SIGNED_JWT"):
            os.environ.pop(var, None)
        r = client.get("/api/companies/", headers={"Authorization": "Bearer anything"})
    # In dev mode, any (or no) token is fine
    assert r.status_code == 200


def test_require_auth_api_key_path_still_works():
    client = TestClient(app)
    env = {
        "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxx",
        "SNOWKAP_API_KEY": "test-api-key-123",
    }
    with patch.dict("os.environ", env, clear=False):
        r = client.get("/api/companies/", headers={"X-API-Key": "test-api-key-123"})
    assert r.status_code == 200
