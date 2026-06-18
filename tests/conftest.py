"""Pytest configuration and fixtures.

Per CLAUDE.md: Tests use pytest + pytest-asyncio + httpx (AsyncClient).
Provides reusable fixtures for JWT tokens, test clients, and mock data.
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")
# `mint_bearer` (api.auth_context) refuses to sign without JWT_SECRET. Set a
# deterministic test secret BEFORE anything imports the auth stack so token
# fixtures work in a fresh checkout / CI.
os.environ.setdefault("JWT_SECRET", "test-secret-pytest-0123456789abcdefghij")

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

from api.main import app
from api.auth_context import SUPER_ADMIN_PERMISSIONS, mint_bearer

# ---------------------------------------------------------------------------
# Legacy-compat shim. The `backend.*` package (backend.core.permissions /
# .security / .main) was removed in the Phase 46 rebuild, but several legacy
# test modules still `from backend.core... import ...`. Recreate the handful
# of symbols they need on top of the current `api/` auth stack, and register
# synthetic `backend.*` modules in sys.modules so those imports resolve at
# collection time (conftest is imported before the test modules).
# ---------------------------------------------------------------------------
import sys
import types


class Role:
    ANALYST = "Analyst"
    TENANT_ADMIN = "Tenant Admin"
    PLATFORM_ADMIN = "Platform Admin"
    MEMBER = "Member"
    EXECUTIVE = "Executive"
    SUSTAINABILITY_MANAGER = "Sustainability Manager"


_READ = ["read", "chat", "view_dashboard", "view_news", "view_analysis"]
ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.ANALYST: _READ + ["view_predictions", "view_reports", "export_data"],
    Role.MEMBER: list(_READ),
    Role.EXECUTIVE: _READ + ["view_predictions", "view_reports"],
    Role.SUSTAINABILITY_MANAGER: _READ + ["view_predictions", "view_reports", "manage_assertions"],
    Role.TENANT_ADMIN: _READ + [
        "manage_users", "manage_tenant", "manage_roles",
        "view_predictions", "view_reports", "generate_reports", "export_data",
    ],
    Role.PLATFORM_ADMIN: list(SUPER_ADMIN_PERMISSIONS),
}


def create_jwt_token(
    tenant_id: str = "test-tenant",
    user_id: str = "test-user",
    company_id: str = "test-company",
    designation: str = "Analyst",
    permissions: list[str] | None = None,
    domain: str = "test.com",
    **extra,
) -> str:
    """Compat wrapper around ``api.auth_context.mint_bearer`` for legacy fixtures."""
    claims = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "designation": designation,
        "permissions": permissions if permissions is not None else ROLE_PERMISSIONS[Role.ANALYST],
        "domain": domain,
    }
    claims.update(extra)
    return mint_bearer(claims)


def _install_backend_shim() -> None:
    if "backend" in sys.modules:
        return
    backend = types.ModuleType("backend")
    core = types.ModuleType("backend.core")
    perms = types.ModuleType("backend.core.permissions")
    perms.Role = Role
    perms.ROLE_PERMISSIONS = ROLE_PERMISSIONS
    security = types.ModuleType("backend.core.security")
    security.create_jwt_token = create_jwt_token
    main = types.ModuleType("backend.main")
    main.app = app
    backend.core = core
    core.permissions = perms
    core.security = security
    sys.modules.update({
        "backend": backend,
        "backend.core": core,
        "backend.core.permissions": perms,
        "backend.core.security": security,
        "backend.main": main,
    })


_install_backend_shim()


# `test_security.py` exercises the removed `backend.core` stack
# (config.settings, get_permissions_for_role, map_designation_to_role,
# hash_magic_link_token, backend.services.ontology_service) — none of which
# exist in the api/ stack. Exclude it from collection until it is rewritten
# against api/auth_context; shimming those internals would assert against
# fakes rather than real behaviour. Tracked as legacy-test debt.
collect_ignore = ["test_security.py"]


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


# Phase 51 — modules guard their `CREATE TABLE` DDL via the central
# DB-identity schema guard (engine.db.schema_guard) instead of a per-module
# `_SCHEMA_READY` boolean. Tests point `connect()` at different SQLite files
# (tmp dirs via `isolated_db`, the dev `data/snowkap.db`, etc.). Clear the
# guard before every test so each test's first `ensure_schema()` always
# re-runs its DDL against whatever database that test actually uses — a stale
# "already created" memo from one test must never silently skip table creation
# in another test's DB (the test_phase51j regression). Production never calls
# this, so the per-process fast path is preserved there.
@pytest.fixture(autouse=True)
def _reset_schema_guard():
    try:
        from engine.db import reset_schema_guard
        reset_schema_guard()
    except Exception:
        pass  # guard unavailable in some legacy-only test runs
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
