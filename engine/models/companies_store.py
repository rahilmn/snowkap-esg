"""Phase 28 — Persistent companies store.

Before Phase 28, companies lived only in ``config/companies.json`` +
ephemeral SQLite caches. Switching ``SNOWKAP_DB_BACKEND=postgres``
silently lost newly-onboarded tenants because there was no companies
table in Supabase.

This module owns the read/write surface for the ``companies`` table
(migration 003). The onboarding worker dual-writes here on
``mark_ready``; ``engine.config.load_companies()`` reads from this table
first and falls back to ``companies.json`` for back-compat with the 7
baseline tenants and any tests that bypass the table.

Schema mirrored from ``engine/db/migrations/003_companies.sql``.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    slug                       TEXT PRIMARY KEY,
    name                       TEXT NOT NULL,
    domain                     TEXT,
    industry                   TEXT,
    market_cap_tier            TEXT,
    yfinance_ticker            TEXT,
    eodhd_ticker               TEXT,
    framework_region           TEXT,
    revenue_cr                 REAL,
    primitive_calibration_json TEXT,
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL,
    created_by_user            TEXT,
    status                     TEXT DEFAULT 'active',
    -- Phase 31 — LLM-crafted live-fetch queries
    sustainability_query       TEXT,
    general_query              TEXT,
    -- Phase 32 — SASB sector classification for materiality lookup.
    -- NULL triggers the DECISION 3.2 fallback ("sasb_unmapped" warning
    -- + neutral 0.5 weight). Back-fill via scripts/backfill_sasb_category.py.
    sasb_category              TEXT
);
CREATE INDEX IF NOT EXISTS idx_companies_domain
    ON companies(domain);
CREATE INDEX IF NOT EXISTS idx_companies_status_updated
    ON companies(status, updated_at DESC);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (Phase 24 — SQLite or Postgres)."""
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    """Idempotent — picks up the table created by migration 003 OR
    creates it directly when the migration runner hasn't run (tests,
    fresh dev clones).
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


@dataclass
class CompanyRecord:
    """Row shape returned by the store. Mirrors the SQL columns 1:1."""
    slug: str
    name: str
    domain: str | None = None
    industry: str | None = None
    market_cap_tier: str | None = None
    yfinance_ticker: str | None = None
    eodhd_ticker: str | None = None
    framework_region: str | None = None
    revenue_cr: float | None = None
    primitive_calibration_json: str | None = None
    created_at: str = ""
    updated_at: str = ""
    created_by_user: str | None = None
    status: str = "active"
    # Phase 31 — live-fetch queries stamped at onboard time by
    # ``engine.ingestion.llm_query_generator``. Consumed by
    # ``engine.ingestion.live_fetcher`` on every /api/news/live call.
    sustainability_query: str | None = None
    general_query: str | None = None
    # Phase 32 — SASB sector for materiality lookup. NULL = no mapping,
    # triggers the neutral-0.5 + sasb_unmapped fallback in the
    # materiality query.
    sasb_category: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def primitive_calibration(self) -> dict[str, Any]:
        """Decode the JSON-blob column. Returns {} on missing or invalid."""
        if not self.primitive_calibration_json:
            return {}
        try:
            return json.loads(self.primitive_calibration_json)
        except (TypeError, ValueError):
            return {}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert(
    slug: str,
    *,
    name: str,
    domain: str | None = None,
    industry: str | None = None,
    market_cap_tier: str | None = None,
    yfinance_ticker: str | None = None,
    eodhd_ticker: str | None = None,
    framework_region: str | None = None,
    revenue_cr: float | None = None,
    primitive_calibration: dict[str, Any] | None = None,
    created_by_user: str | None = None,
    status: str = "active",
    sustainability_query: str | None = None,
    general_query: str | None = None,
) -> CompanyRecord:
    """Idempotent upsert. Caller passes domain values directly; the
    table is the new source of truth for everything except the 7
    baseline companies still seeded from ``companies.json``.

    On INSERT we stamp ``created_at`` and ``updated_at``. On UPDATE we
    refresh ``updated_at`` only; existing ``created_at`` is preserved.

    ``primitive_calibration`` (dict) is JSON-encoded into the
    ``primitive_calibration_json`` column. Passing None leaves the
    column untouched on UPDATE (so callers updating just the domain
    don't accidentally wipe a previously-calibrated β profile).
    """
    ensure_schema()
    if not slug or not name:
        raise ValueError("companies_store.upsert: slug and name are required")

    calib_json = (
        json.dumps(primitive_calibration, sort_keys=True)
        if primitive_calibration is not None
        else None
    )

    now = _now()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT slug, created_at FROM companies WHERE slug = ?", (slug,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO companies (
                    slug, name, domain, industry, market_cap_tier,
                    yfinance_ticker, eodhd_ticker, framework_region,
                    revenue_cr, primitive_calibration_json,
                    created_at, updated_at, created_by_user, status,
                    sustainability_query, general_query
                )
                VALUES (
                    :slug, :name, :domain, :industry, :market_cap_tier,
                    :yfinance_ticker, :eodhd_ticker, :framework_region,
                    :revenue_cr, :primitive_calibration_json,
                    :created_at, :updated_at, :created_by_user, :status,
                    :sustainability_query, :general_query
                )
                """,
                {
                    "slug": slug,
                    "name": name,
                    "domain": domain,
                    "industry": industry,
                    "market_cap_tier": market_cap_tier,
                    "yfinance_ticker": yfinance_ticker,
                    "eodhd_ticker": eodhd_ticker,
                    "framework_region": framework_region,
                    "revenue_cr": revenue_cr,
                    "primitive_calibration_json": calib_json,
                    "created_at": now,
                    "updated_at": now,
                    "created_by_user": created_by_user,
                    "status": status,
                    "sustainability_query": sustainability_query,
                    "general_query": general_query,
                },
            )
        else:
            # UPDATE — refresh updated_at, keep created_at. Skip the JSON
            # column when caller didn't supply it so we don't blow away
            # previously-stored calibration. Same partial-update pattern
            # applies to the Phase 31 query columns.
            assignments = [
                "name = :name",
                "domain = :domain",
                "industry = :industry",
                "market_cap_tier = :market_cap_tier",
                "yfinance_ticker = :yfinance_ticker",
                "eodhd_ticker = :eodhd_ticker",
                "framework_region = :framework_region",
                "revenue_cr = :revenue_cr",
                "updated_at = :updated_at",
                "status = :status",
            ]
            params: dict[str, Any] = {
                "slug": slug,
                "name": name,
                "domain": domain,
                "industry": industry,
                "market_cap_tier": market_cap_tier,
                "yfinance_ticker": yfinance_ticker,
                "eodhd_ticker": eodhd_ticker,
                "framework_region": framework_region,
                "revenue_cr": revenue_cr,
                "updated_at": now,
                "status": status,
            }
            if calib_json is not None:
                assignments.append("primitive_calibration_json = :primitive_calibration_json")
                params["primitive_calibration_json"] = calib_json
            if created_by_user is not None:
                # Allow back-filling the created_by_user on upgrade only.
                # Existing rows whose creator is already set should not be
                # silently re-attributed.
                assignments.append(
                    "created_by_user = COALESCE(created_by_user, :created_by_user)"
                )
                params["created_by_user"] = created_by_user
            if sustainability_query is not None:
                assignments.append("sustainability_query = :sustainability_query")
                params["sustainability_query"] = sustainability_query
            if general_query is not None:
                assignments.append("general_query = :general_query")
                params["general_query"] = general_query
            conn.execute(
                f"UPDATE companies SET {', '.join(assignments)} WHERE slug = :slug",
                params,
            )

    record = get(slug)
    if record is None:  # defensive — should be unreachable
        raise RuntimeError(f"companies_store.upsert: failed to read back slug={slug!r}")
    return record


def get(slug: str) -> CompanyRecord | None:
    """Fetch one company by slug. Returns None when absent."""
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM companies WHERE slug = ?", (slug,)
        ).fetchone()
    if row is None:
        return None
    return CompanyRecord(**dict(row))


def get_by_domain(domain: str) -> CompanyRecord | None:
    """Lookup by domain (used by self-service onboarding to dedupe)."""
    ensure_schema()
    if not domain:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM companies WHERE domain = ? AND status = 'active' "
            "ORDER BY updated_at DESC LIMIT 1",
            (domain.strip().lower(),),
        ).fetchone()
    if row is None:
        return None
    return CompanyRecord(**dict(row))


def list_all(*, status: str = "active") -> list[CompanyRecord]:
    """Return every company with the given status, newest first."""
    ensure_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM companies WHERE status = ? "
            "ORDER BY updated_at DESC",
            (status,),
        ).fetchall()
    return [CompanyRecord(**dict(r)) for r in rows]


def archive(slug: str) -> None:
    """Mark a company as archived (soft-delete). Idempotent."""
    ensure_schema()
    with _connect() as conn:
        conn.execute(
            "UPDATE companies SET status = 'archived', updated_at = ? WHERE slug = ?",
            (_now(), slug),
        )


def _truncate_all() -> None:
    """Test/wipe helper — used by scripts/wipe_clean_slate.py."""
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM companies")
