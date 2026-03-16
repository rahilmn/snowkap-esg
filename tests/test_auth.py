"""Auth flow tests — 3-way login system.

Per CLAUDE.md: Domain → Designation → Company Name → JWT
"""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.mark.asyncio
async def test_resolve_domain_blocks_personal_email():
    """Personal email domains (gmail, yahoo) should be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/auth/resolve-domain", json={"domain": "gmail.com"})
        assert response.status_code == 400
        assert "Personal email" in response.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_domain_accepts_corporate():
    """Corporate domains should be accepted."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/auth/resolve-domain", json={"domain": "mahindra.com"})
        assert response.status_code == 200
        data = response.json()
        assert data["domain"] == "mahindra.com"


@pytest.mark.asyncio
async def test_login_domain_mismatch():
    """Email domain must match company domain per CLAUDE.md Rule #8."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/auth/login", json={
            "email": "user@other.com",
            "domain": "mahindra.com",
            "designation": "Analyst",
            "company_name": "Mahindra Logistics",
            "name": "Test User",
        })
        assert response.status_code == 400
        assert "must match" in response.json()["detail"]
