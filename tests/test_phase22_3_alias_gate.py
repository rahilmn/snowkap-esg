"""Phase 22.3 — Alias-aware tenant scope gate.

Pre-fix a JWT minted with the login-slug `basf` could not query
`?company_id=basf-se` (the canonical slug from the onboarder's yfinance
lookup) because `_require_tenant_scope` did a raw string compare. The
fix runs both sides through `sqlite_index.resolve_slug()` first so
aliased identities collapse to the same canonical.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.routes.legacy_adapter import _require_tenant_scope
from engine.index import sqlite_index


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    """Each test runs against a fresh SQLite so aliases don't leak."""
    db = tmp_path / "test_snowkap.db"
    monkeypatch.setenv("SNOWKAP_DB_PATH", str(db))
    # sqlite_index reads the env var via _db_path() each connect.
    yield


def _claims(company_id: str | None) -> dict:
    return {
        "sub": "user@example.com",
        "company_id": company_id,
        "permissions": ["read"],
    }


def test_exact_match_passes():
    _require_tenant_scope("acme", _claims("acme"))


def test_mismatch_no_alias_403():
    with pytest.raises(HTTPException) as exc:
        _require_tenant_scope("rival", _claims("acme"))
    assert exc.value.status_code == 403


def test_alias_login_slug_matches_canonical():
    """JWT carries `basf` (login slug); query uses `basf-se` (canonical)."""
    sqlite_index.register_alias("basf", "basf-se")
    _require_tenant_scope("basf-se", _claims("basf"))


def test_alias_canonical_matches_login_slug():
    """Symmetric — JWT carries canonical, query uses login slug."""
    sqlite_index.register_alias("lloyds", "lloyds-banking-group")
    _require_tenant_scope("lloyds", _claims("lloyds-banking-group"))


def test_super_admin_bypass_unchanged():
    claims = {"sub": "x", "company_id": None, "permissions": ["super_admin"]}
    _require_tenant_scope("any-tenant", claims)
