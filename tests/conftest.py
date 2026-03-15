"""Pytest configuration and fixtures.

Per CLAUDE.md: Tests use pytest + pytest-asyncio + httpx (AsyncClient).
Provides reusable fixtures for JWT tokens, test clients, and mock data.
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.permissions import ROLE_PERMISSIONS, Role
from backend.core.security import create_jwt_token
from backend.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    """Async test client for FastAPI."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def make_token(
    tenant_id: str = "test-tenant",
    user_id: str = "test-user",
    company_id: str = "test-company",
    designation: str = "Analyst",
    permissions: list[str] | None = None,
    domain: str = "test.com",
) -> str:
    """Create a JWT token for testing."""
    if permissions is None:
        permissions = ROLE_PERMISSIONS[Role.ANALYST]
    return create_jwt_token(
        tenant_id=tenant_id,
        user_id=user_id,
        company_id=company_id,
        designation=designation,
        permissions=permissions,
        domain=domain,
    )


def auth_headers(
    tenant_id: str = "test-tenant",
    user_id: str = "test-user",
    permissions: list[str] | None = None,
    designation: str = "Analyst",
    domain: str = "test.com",
) -> dict[str, str]:
    """Create Authorization headers for testing."""
    token = make_token(
        tenant_id=tenant_id,
        user_id=user_id,
        permissions=permissions,
        designation=designation,
        domain=domain,
    )
    return {"Authorization": f"Bearer {token}"}


# Pre-built token fixtures for common roles

@pytest.fixture
def analyst_headers() -> dict[str, str]:
    """JWT headers for an Analyst role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.ANALYST])


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """JWT headers for a Tenant Admin role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.TENANT_ADMIN])


@pytest.fixture
def platform_admin_headers() -> dict[str, str]:
    """JWT headers for a Platform Admin role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.PLATFORM_ADMIN])


@pytest.fixture
def member_headers() -> dict[str, str]:
    """JWT headers for a basic Member role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.MEMBER])


@pytest.fixture
def executive_headers() -> dict[str, str]:
    """JWT headers for an Executive role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.EXECUTIVE])


@pytest.fixture
def sustainability_manager_headers() -> dict[str, str]:
    """JWT headers for a Sustainability Manager role user."""
    return auth_headers(permissions=ROLE_PERMISSIONS[Role.SUSTAINABILITY_MANAGER])
