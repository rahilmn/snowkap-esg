"""Health check tests — verify FastAPI is running."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.mark.asyncio
async def test_health_check():
    """Per MASTER_BUILD_PLAN Phase 1: FastAPI /health → 200.

    The legacy backend.main.health_check pings PostgreSQL + Redis. The
    current Snowkap stack removed both (filesystem + SQLite only), so
    those deps will always report "error" in this environment and the
    overall status flips to "degraded". The endpoint contract — 200 +
    correct service identifier + a `status` field that is one of the
    expected values — is what we assert here.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok", data
        assert data["service"] == "snowkap-esg-api"
