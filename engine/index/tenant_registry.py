"""Onboarded-tenant registry (SQLite-backed).

When a new company/domain logs in via `/api/auth/login`, we record them here
so the sales super-admin's CompanySwitcher automatically reflects every
company the product has ever seen — including prospects that signed up
after the 7 target companies were hardcoded in `config/companies.json`.

The registry is read-and-write safe across concurrent requests (SQLite
default locking is fine for the low write volume we have — a handful of
logins per minute at most).

Schema:

    tenant_registry(
        slug         TEXT PRIMARY KEY,   -- URL-safe slug derived from domain
        domain       TEXT NOT NULL UNIQUE,
        name         TEXT,                -- display name (human-readable)
        industry     TEXT,                -- SASB / industry when known
        source       TEXT NOT NULL,       -- 'target' | 'onboarded'
        created_at   TEXT NOT NULL,       -- ISO-8601 UTC
        last_seen_at TEXT                  -- updated on every login
    )

`source='target'` → one of the 7 hardcoded companies in config/companies.json.
`source='onboarded'` → auto-registered when a new domain logged in.

This table lives alongside the article index in `data/snowkap.db`.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.index.sqlite_index import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenant_registry (
    slug         TEXT PRIMARY KEY,
    domain       TEXT NOT NULL UNIQUE,
    name         TEXT,
    industry     TEXT,
    source       TEXT NOT NULL DEFAULT 'onboarded',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tenant_source ON tenant_registry(source);
CREATE INDEX IF NOT EXISTS idx_tenant_last_seen ON tenant_registry(last_seen_at DESC);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    """Create the table on first use. Idempotent."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Phase 11A: ensure WAL mode via the shared setter in sqlite_index.
    # WAL is a per-DB setting so any module calling this path bootstraps
    # concurrency-safe writes across every table in snowkap.db.
    from engine.index.sqlite_index import _ensure_wal_mode
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


def _slug_from_domain(domain: str) -> str:
    """Convert `mintedit.com` → `mintedit`, `adani-power.in` → `adani-power`."""
    d = (domain or "").strip().lower()
    # Strip the TLD — keep everything before the final dot
    base = d.rsplit(".", 1)[0] if "." in d else d
    # Normalise non-slug chars
    slug = re.sub(r"[^a-z0-9-]+", "-", base).strip("-")
    return slug or "tenant"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def register_tenant(
    domain: str,
    name: str | None = None,
    industry: str | None = None,
    source: str = "onboarded",
) -> str | None:
    """Upsert a tenant row. Returns the slug on success, None on bad input.

    `last_seen_at` is always refreshed so the switcher can sort by recency.
    `source` is set only on first insert; subsequent upserts leave it alone
    (so a target company that was seeded as 'target' stays 'target' even if
    someone at that domain logs in and triggers this path).
    """
    ensure_schema()
    domain = (domain or "").strip().lower()
    if not domain or "." not in domain:
        return None

    slug = _slug_from_domain(domain)
    now = _now()
    with _connect() as conn:
        # Try insert — falls through to update on conflict
        conn.execute(
            """
            INSERT INTO tenant_registry(slug, domain, name, industry, source, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                name = COALESCE(excluded.name, tenant_registry.name),
                industry = COALESCE(excluded.industry, tenant_registry.industry),
                last_seen_at = excluded.last_seen_at
            """,
            (slug, domain, name, industry, source, now, now),
        )
    return slug


def list_tenants() -> list[dict[str, Any]]:
    """Return every registered tenant, newest-first by `last_seen_at`."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT slug, domain, name, industry, source, created_at, last_seen_at
            FROM tenant_registry
            ORDER BY COALESCE(last_seen_at, created_at) DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_tenant(slug: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_registry WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else None


def get_tenant_by_domain(domain: str) -> dict[str, Any] | None:
    """Look up a previously-registered tenant by its domain.

    Used by /auth/resolve-domain so a returning prospect (e.g. someone
    from idfcfirstbank.com who self-onboarded last week) is recognised
    as `is_existing=true` with their real persisted company name —
    instead of falling through to the "(Guest)" path every login.
    """
    ensure_schema()
    d = (domain or "").strip().lower()
    if not d:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_registry WHERE domain = ?", (d,)
        ).fetchone()
        return dict(row) if row else None


def seed_target_companies(companies: list[Any]) -> int:
    """Seed the 7 target companies as source='target'. Returns rows touched.

    Safe to call repeatedly — existing rows are upserted, source stays 'target'.
    """
    ensure_schema()
    touched = 0
    now = _now()
    with _connect() as conn:
        for c in companies:
            domain = getattr(c, "domain", None)
            if not domain:
                continue
            slug = getattr(c, "slug", None) or _slug_from_domain(domain)
            name = getattr(c, "name", None)
            industry = getattr(c, "industry", None)
            conn.execute(
                """
                INSERT INTO tenant_registry(slug, domain, name, industry, source, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, 'target', ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    slug = excluded.slug,
                    name = excluded.name,
                    industry = excluded.industry,
                    source = 'target'
                """,
                (slug, domain.lower(), name, industry, now, now),
            )
            touched += 1
    return touched
