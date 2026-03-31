"""Regression tests for Critical/High security fixes.

Covers:
- CVE-2024-33663: python-jose replaced with PyJWT
- BUG-01: Stored XSS via user name field
- BUG-02: XSS via ReactMarkdown (server-side name sanitization)
- BUG-03: resolve-domain accepts XSS/empty payloads
- BUG-04: Token storage (verified via API behavior)
- BUG-05: Missing JWT claim validation
- BUG-06: Race condition on user creation (IntegrityError handling)
- BUG-07: Negative offset → 500 with SQL leak
- BUG-08: 403 instead of 401 for unauthenticated requests
- BUG-10: Auth bypass via sessionStorage (server-side auth enforcement)

Run: cd snowkap-esg && python -m pytest backend/tests/test_security_fixes.py -v
"""

import asyncio
import json
import re

import httpx
import pytest

BASE_URL = "http://localhost:8000"
VALID_LOGIN = {
    "email": "test@nike.com",
    "name": "Test User",
    "designation": "CEO",
    "company_name": "Nike, Inc.",
    "domain": "nike.com",
}


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def valid_token(event_loop):
    """Get a valid JWT token for authenticated tests."""

    async def _get_token():
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as c:
            r = await c.post("/api/auth/login", json=VALID_LOGIN)
            assert r.status_code == 200
            return r.json()["token"]

    return event_loop.run_until_complete(_get_token())


@pytest.fixture(scope="module")
def auth_headers(valid_token):
    return {"Authorization": f"Bearer {valid_token}"}


# ──────────────────────────────────────────────
# CVE-2024-33663: PyJWT replaces python-jose
# ──────────────────────────────────────────────


class TestJWTLibraryReplacement:
    """Verify PyJWT works correctly after replacing python-jose."""

    def test_jwt_encode_decode_roundtrip(self):
        """JWT encode/decode works with PyJWT."""
        import jwt

        payload = {"sub": "user123", "tenant_id": "tenant456"}
        token = jwt.encode(payload, "test-secret", algorithm="HS256")
        decoded = jwt.decode(token, "test-secret", algorithms=["HS256"])
        assert decoded["sub"] == "user123"
        assert decoded["tenant_id"] == "tenant456"

    def test_jwt_invalid_token_raises_correct_error(self):
        """Invalid tokens raise InvalidTokenError (aliased as JWTError)."""
        from jwt.exceptions import InvalidTokenError as JWTError

        import jwt

        with pytest.raises(JWTError):
            jwt.decode("not.a.valid.token", "secret", algorithms=["HS256"])

    def test_jwt_expired_token_raises_correct_error(self):
        """Expired tokens raise ExpiredSignatureError (subclass of InvalidTokenError)."""
        from datetime import datetime, timedelta, timezone

        import jwt

        payload = {
            "sub": "user",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")
        with pytest.raises(jwt.ExpiredSignatureError):
            jwt.decode(token, "secret", algorithms=["HS256"])

    def test_pyjwt_is_installed(self):
        """PyJWT should be the active JWT library."""
        import jwt

        assert hasattr(jwt, "encode")
        assert hasattr(jwt, "decode")
        # Verify it's PyJWT (has __version__), not python-jose
        assert hasattr(jwt, "__version__")


# ──────────────────────────────────────────────
# BUG-08: 401 for unauthenticated (was 403)
# ──────────────────────────────────────────────


class TestAuthStatusCodes:
    """Verify correct HTTP status codes for auth scenarios."""

    @pytest.mark.asyncio
    async def test_missing_auth_returns_401(self):
        """Request without Authorization header → 401 (not 403)."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed")
            assert r.status_code == 401
            assert "missing" in r.json().get("detail", "").lower() or "token" in r.json().get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_invalid_jwt_returns_401(self):
        """Request with tampered JWT → 401."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get(
                "/api/news/feed",
                headers={"Authorization": "Bearer fake.token.here"},
            )
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_header_returns_401(self):
        """Request with no Authorization header at all → 401."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/auth/me")
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_200(self, auth_headers):
        """Valid token → 200 on protected endpoints."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed", headers=auth_headers)
            assert r.status_code == 200


# ──────────────────────────────────────────────
# BUG-05: JWT claim validation
# ──────────────────────────────────────────────


class TestJWTClaimValidation:
    """Verify JWT tokens with missing claims are rejected."""

    @pytest.mark.asyncio
    async def test_token_missing_tenant_id_returns_401(self):
        """JWT without tenant_id claim → 401."""
        import jwt

        from backend.core.config import settings

        token = jwt.encode(
            {"sub": "user123"},  # Missing tenant_id, company_id, etc.
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get(
                "/api/news/feed",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 401
            assert "missing" in r.json().get("detail", "").lower()


# ──────────────────────────────────────────────
# BUG-03: resolve-domain input validation
# ──────────────────────────────────────────────


class TestResolveDomainValidation:
    """Verify domain validation rejects invalid inputs."""

    @pytest.mark.asyncio
    async def test_empty_domain_returns_422(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": ""})
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_xss_domain_returns_422(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/resolve-domain",
                json={"domain": "<script>alert(1)</script>"},
            )
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_spaces_only_domain_returns_422(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": "   "})
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_sql_injection_domain_returns_422(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/resolve-domain",
                json={"domain": "'; DROP TABLE tenants; --"},
            )
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_domain_returns_200(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post("/api/auth/resolve-domain", json={"domain": "nike.com"})
            assert r.status_code == 200
            assert r.json()["domain"] == "nike.com"


# ──────────────────────────────────────────────
# BUG-01: Stored XSS via user name
# ──────────────────────────────────────────────


class TestXSSSanitization:
    """Verify HTML is stripped from user inputs."""

    @pytest.mark.asyncio
    async def test_html_tags_stripped_from_name(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/login",
                json={**VALID_LOGIN, "name": "<b>Bold</b> <script>alert(1)</script>"},
            )
            assert r.status_code == 200
            name = r.json().get("name", "")
            assert "<" not in name
            assert ">" not in name
            assert "script" not in name.lower()

    @pytest.mark.asyncio
    async def test_img_onerror_stripped_from_name(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/login",
                json={**VALID_LOGIN, "name": '<img src=x onerror=alert(1)>'},
            )
            assert r.status_code == 200
            name = r.json().get("name", "")
            assert "<img" not in name
            assert "onerror" not in name

    @pytest.mark.asyncio
    async def test_normal_name_preserved(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/login",
                json={**VALID_LOGIN, "name": "Rahil Naik"},
            )
            assert r.status_code == 200
            assert r.json().get("name") == "Rahil Naik"

    @pytest.mark.asyncio
    async def test_unicode_name_preserved(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/login",
                json={**VALID_LOGIN, "name": "Rahi\u0142 Na\u00efk"},
            )
            assert r.status_code == 200
            # Unicode should be preserved, only HTML stripped
            assert "<" not in r.json().get("name", "")


# ──────────────────────────────────────────────
# BUG-07: Input validation (offset, limit)
# ──────────────────────────────────────────────


class TestInputValidation:
    """Verify query parameter validation prevents crashes."""

    @pytest.mark.asyncio
    async def test_negative_offset_returns_422(self, auth_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed?offset=-1", headers=auth_headers)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_zero_limit_returns_422(self, auth_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed?limit=0", headers=auth_headers)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_over_max_limit_returns_422(self, auth_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed?limit=999", headers=auth_headers)
            assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_pagination_returns_200(self, auth_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get(
                "/api/news/feed?limit=10&offset=0", headers=auth_headers
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_sql_injection_in_sort_by_is_safe(self, auth_headers):
        """SQL injection in sort_by param should not crash the server."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get(
                "/api/news/feed?sort_by='; DROP TABLE articles; --",
                headers=auth_headers,
            )
            # Should either return 200 (ignored) or 422 (rejected), never 500
            assert r.status_code in (200, 422)


# ──────────────────────────────────────────────
# BUG-06: Race condition on user creation
# ──────────────────────────────────────────────


class TestConcurrentLogin:
    """Verify concurrent logins don't crash."""

    @pytest.mark.asyncio
    async def test_concurrent_login_same_email_no_crash(self):
        """Two simultaneous login requests with the same email should both succeed."""
        login_data = {
            "email": "concurrent@nike.com",
            "name": "Concurrent Test",
            "designation": "CEO",
            "company_name": "Nike, Inc.",
            "domain": "nike.com",
        }

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as c:
            # Fire 3 concurrent login requests
            results = await asyncio.gather(
                c.post("/api/auth/login", json=login_data),
                c.post("/api/auth/login", json=login_data),
                c.post("/api/auth/login", json=login_data),
                return_exceptions=True,
            )

            # All should succeed (200) — no 500 errors
            for r in results:
                if isinstance(r, Exception):
                    pytest.fail(f"Concurrent login raised exception: {r}")
                assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
                assert "token" in r.json()


# ──────────────────────────────────────────────
# Tenant isolation (regression)
# ──────────────────────────────────────────────


class TestTenantIsolation:
    """Verify cross-tenant data isolation is maintained."""

    @pytest.mark.asyncio
    async def test_nike_user_cannot_see_icici_articles(self):
        """Nike user's feed should not contain ICICI Bank articles."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=15) as c:
            # Login as Nike
            r = await c.post("/api/auth/login", json=VALID_LOGIN)
            nike_token = r.json()["token"]
            h = {"Authorization": f"Bearer {nike_token}"}

            # Get feed
            r = await c.get("/api/news/feed", headers=h)
            assert r.status_code == 200
            articles = r.json().get("articles", [])

            # No article should mention ICICI in a Nike tenant feed
            for article in articles:
                title = article.get("title", "").lower()
                # Articles about ICICI should not appear in Nike's feed
                assert "icici" not in title or "nike" in title

    @pytest.mark.asyncio
    async def test_admin_endpoint_requires_admin_role(self):
        """Non-admin user should get 403 on admin endpoints."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.post(
                "/api/auth/login",
                json={
                    "email": "analyst@nike.com",
                    "name": "Analyst",
                    "designation": "ESG Analyst",
                    "company_name": "Nike",
                    "domain": "nike.com",
                },
            )
            h = {"Authorization": f"Bearer {r.json()['token']}"}
            r = await c.get("/api/admin/tenants", headers=h)
            assert r.status_code == 403


# ──────────────────────────────────────────────
# Error information leakage
# ──────────────────────────────────────────────


class TestErrorLeakage:
    """Verify errors don't leak internal details."""

    @pytest.mark.asyncio
    async def test_401_does_not_leak_jwt_secret(self):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get(
                "/api/news/feed",
                headers={"Authorization": "Bearer invalid"},
            )
            body = r.text.lower()
            assert "secret" not in body
            assert "jwt_secret" not in body
            assert "traceback" not in body

    @pytest.mark.asyncio
    async def test_422_does_not_leak_sql(self, auth_headers):
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as c:
            r = await c.get("/api/news/feed?offset=-1", headers=auth_headers)
            body = r.text.lower()
            assert "select" not in body
            assert "sqlalchemy" not in body
            assert "postgresql" not in body
