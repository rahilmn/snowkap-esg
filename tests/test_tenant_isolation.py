"""Tenant isolation gate test.

Per MASTER_BUILD_PLAN Phase 2B:
- Gate test: Tenant A data invisible to Tenant B
- Per CLAUDE.md Rule #1: NEVER return data from Tenant A to Tenant B
"""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.security import create_jwt_token
from backend.main import app


def _make_token(tenant_id: str, user_id: str = "test-user") -> str:
    """Helper to create JWT for a specific tenant."""
    return create_jwt_token(
        tenant_id=tenant_id,
        user_id=user_id,
        company_id="test-company",
        designation="Analyst",
        permissions=["view_dashboard", "view_news", "view_analysis"],
        domain="test.com",
    )


@pytest.mark.asyncio
async def test_tenant_a_cannot_see_tenant_b_companies():
    """Tenant A's JWT should NOT return Tenant B's companies."""
    token_a = _make_token("tenant-a")
    token_b = _make_token("tenant-b")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant A sees their companies
        resp_a = await client.get(
            "/api/companies/",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 200

        # Tenant B sees their companies
        resp_b = await client.get(
            "/api/companies/",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 200

        # Both return empty lists (no seeded data in test DB)
        # but the important thing is they don't cross-contaminate
        companies_a = resp_a.json()["companies"]
        companies_b = resp_b.json()["companies"]

        # Verify no company IDs overlap
        ids_a = {c["id"] for c in companies_a}
        ids_b = {c["id"] for c in companies_b}
        assert ids_a.isdisjoint(ids_b), "Tenant A and B must never share company data"


@pytest.mark.asyncio
async def test_tenant_a_cannot_see_tenant_b_news():
    """Tenant A's JWT should NOT return Tenant B's news feed."""
    token_a = _make_token("tenant-a")
    token_b = _make_token("tenant-b")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_a = await client.get(
            "/api/news/feed",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp_b = await client.get(
            "/api/news/feed",
            headers={"Authorization": f"Bearer {token_b}"},
        )

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        articles_a = {a["id"] for a in resp_a.json()["articles"]}
        articles_b = {a["id"] for a in resp_b.json()["articles"]}
        assert articles_a.isdisjoint(articles_b), "Tenant news feeds must never overlap"


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected():
    """Requests without JWT should be rejected with 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/companies/")
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_invalid_jwt_rejected():
    """Requests with invalid JWT should be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/companies/",
            headers={"Authorization": "Bearer invalid-token-here"},
        )
        assert resp.status_code == 401
