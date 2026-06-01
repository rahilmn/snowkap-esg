"""Pytest configuration and fixtures.

Per CLAUDE.md: Tests use pytest + pytest-asyncio + httpx (AsyncClient).
Provides reusable fixtures for JWT tokens, test clients, and mock data.
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")

# Phase 24 — `engine.config` loads `.env` at import time, which on dev
# machines configured for Supabase Postgres sets
# ``SNOWKAP_DB_BACKEND=postgres``. Tests that seed via raw
# ``sqlite3.connect(DB_PATH)`` and read back via ``engine.db.connect()``
# would then see two completely different databases (the file at
# ``data/snowkap.db`` vs Supabase). Force the backend to SQLite for
# every test session so the seed/read paths reconcile. Setting the env
# var BEFORE the .env file gets loaded means ``load_dotenv`` (whose
# default is non-override) leaves our value alone. Individual tests
# that explicitly want to exercise the postgres adapter can override.
# Escape hatch — when SNOWKAP_TEST_ALLOW_POSTGRES=1, honour whatever
# SNOWKAP_DB_BACKEND the env has set (typically `postgres` against a
# staging Supabase). Used once per Supabase-cutover validation pass;
# the default behaviour (force sqlite) is restored for normal runs.
if os.environ.get("SNOWKAP_TEST_ALLOW_POSTGRES") != "1":
    os.environ["SNOWKAP_DB_BACKEND"] = "sqlite"
    # Phase 48.0 — SQLite is hard-disabled in engine.db.connect unless this
    # escape flag is set. The test suite legitimately uses SQLite fixtures,
    # so opt in here. Production never sets this.
    os.environ["SNOWKAP_ALLOW_SQLITE"] = "1"

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


# Phase 22.3 — `/api/auth/login` uses an in-process sliding-window
# rate limiter (5/min, 20/hour per email). Across a 1,400+ test sweep
# the same test email (e.g. ``test@example.com`` or one of the seeded
# admin/sales addresses) hits the quota and subsequent tests get 429s
# unrelated to what they're actually testing. Reset the bucket before
# every test so each test starts from a clean slate.
@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    try:
        from api.rate_limit import LOGIN_LIMITER
        LOGIN_LIMITER.reset()
    except Exception:
        pass  # Module unavailable in some legacy-only test runs
    yield


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
