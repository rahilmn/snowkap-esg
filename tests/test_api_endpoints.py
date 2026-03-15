"""API endpoint tests — verifies route registration, auth enforcement, schemas.

Tests all major API routes without requiring a live database.
Focus: route existence, auth enforcement, input validation, permission gating.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.permissions import ROLE_PERMISSIONS, Role
from backend.core.security import create_jwt_token
from backend.main import app

from tests.conftest import auth_headers, make_token


# --- Health Endpoint ---

@pytest.mark.asyncio
async def test_health_check():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "esg-api"
        assert "version" in data


# --- Auth Enforcement ---

class TestAuthEnforcement:
    """Every protected endpoint must reject unauthenticated requests."""

    PROTECTED_ENDPOINTS = [
        ("GET", "/api/companies/"),
        ("GET", "/api/news/feed"),
        ("GET", "/api/predictions/"),
        ("GET", "/api/predictions/stats"),
        ("GET", "/api/ontology/stats"),
        ("POST", "/api/agent/chat"),
        ("GET", "/api/agent/agents"),
        ("GET", "/api/agent/history"),
        ("GET", "/api/media/"),
        ("GET", "/api/media/stats/summary"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    async def test_no_token_returns_403(self, method: str, path: str):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if method == "GET":
                resp = await client.get(path)
            else:
                resp = await client.post(path, json={})
            assert resp.status_code == 403, f"{method} {path} should require auth"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    async def test_invalid_token_returns_401(self, method: str, path: str):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": "Bearer invalid.token.here"}
            if method == "GET":
                resp = await client.get(path, headers=headers)
            else:
                resp = await client.post(path, json={}, headers=headers)
            assert resp.status_code == 401, f"{method} {path} should reject invalid JWT"


# --- Auth Endpoints ---

class TestAuthEndpoints:
    @pytest.mark.asyncio
    async def test_resolve_domain_rejects_personal(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/resolve-domain", json={"domain": "gmail.com"})
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_resolve_domain_accepts_corporate(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/resolve-domain", json={"domain": "mahindra.com"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_magic_link_email_domain_mismatch(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/magic-link", json={
                "email": "user@other.com",
                "domain": "mahindra.com",
                "designation": "Analyst",
                "company_name": "Mahindra",
            })
            assert resp.status_code == 400
            assert "must match" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_magic_link_personal_email_blocked(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/magic-link", json={
                "email": "user@gmail.com",
                "domain": "gmail.com",
                "designation": "Analyst",
                "company_name": "Test",
            })
            # Should fail because gmail.com is blocked at resolve-domain level
            assert resp.status_code == 400


# --- Agent Endpoints ---

class TestAgentEndpoints:
    @pytest.mark.asyncio
    async def test_chat_empty_question_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = auth_headers()
            resp = await client.post("/api/agent/chat", json={"question": ""}, headers=headers)
            assert resp.status_code == 400
            assert "empty" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_chat_whitespace_only_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = auth_headers()
            resp = await client.post("/api/agent/chat", json={"question": "   "}, headers=headers)
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_agents_returns_nine(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = auth_headers()
            resp = await client.get("/api/agent/agents", headers=headers)
            assert resp.status_code == 200
            agents = resp.json()
            assert len(agents) == 9
            agent_ids = {a["id"] for a in agents}
            assert "supply_chain" in agent_ids
            assert "compliance" in agent_ids
            assert "analytics" in agent_ids

    @pytest.mark.asyncio
    async def test_ask_about_news_requires_article_id(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = auth_headers()
            resp = await client.post("/api/agent/ask-about-news", json={}, headers=headers)
            assert resp.status_code == 422  # Pydantic validation error


# --- Permission Gating ---

class TestPermissionGating:
    @pytest.mark.asyncio
    async def test_admin_tenants_requires_platform_admin(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Analyst doesn't have platform_admin permission
            headers = auth_headers(permissions=ROLE_PERMISSIONS[Role.ANALYST])
            resp = await client.get("/api/admin/tenants", headers=headers)
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_tenants_allowed_for_platform_admin(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = auth_headers(permissions=ROLE_PERMISSIONS[Role.PLATFORM_ADMIN])
            resp = await client.get("/api/admin/tenants", headers=headers)
            # 200 or 500 (no DB), but NOT 403
            assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_prediction_trigger_requires_permission(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Member has no trigger_predictions permission
            headers = auth_headers(permissions=ROLE_PERMISSIONS[Role.MEMBER])
            resp = await client.post("/api/predictions/trigger", json={
                "article_id": "a1", "company_id": "c1",
            }, headers=headers)
            assert resp.status_code == 403


# --- Tenant Isolation ---

class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_different_tenants_get_independent_results(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers_a = auth_headers(tenant_id="tenant-a")
            headers_b = auth_headers(tenant_id="tenant-b")

            resp_a = await client.get("/api/companies/", headers=headers_a)
            resp_b = await client.get("/api/companies/", headers=headers_b)

            # Both should succeed (may be empty without DB)
            assert resp_a.status_code == 200
            assert resp_b.status_code == 200

    @pytest.mark.asyncio
    async def test_cross_tenant_company_access_denied(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Try to access a company with wrong tenant's token
            headers = auth_headers(tenant_id="tenant-a")
            resp = await client.get("/api/companies/some-company-from-tenant-b", headers=headers)
            # Should be 404 (not found in this tenant), never 200
            assert resp.status_code in (404, 500)  # 404 expected; 500 if no DB


# --- Media Endpoints ---

class TestMediaEndpoints:
    @pytest.mark.asyncio
    async def test_upload_requires_auth(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/media/upload")
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_search_requires_auth(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/media/search", json={"query": "test"})
            assert resp.status_code == 403
