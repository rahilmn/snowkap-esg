"""Per-tenant deck FREEZE flags.

When a tenant's deck is frozen, the automated rebuild paths skip it so a
hand-curated deck survives untouched until it is un-frozen. The freeze is
checked at the single chokepoint ``deck_builder.build_company_deck`` (used by
BOTH the weekly Sunday refresh and the overnight batch) and, for observability
+ token savings, explicitly in the weekly refresh loop.

Built to protect the Maruti Tuesday demo deck from the Sunday cron.

Backend-agnostic (SQLite dev / Postgres prod via ``engine.db.connect``).
``CREATE TABLE IF NOT EXISTS`` → no migration. Public surface:
  * ensure_schema() -> None
  * set_frozen(slug, frozen, reason="") -> None
  * is_frozen(slug) -> bool
  * list_frozen() -> list[dict]
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from engine.db import connect as _db_connect, is_postgres

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_schema() -> None:
    """Create the ``deck_freeze`` table if absent. Idempotent + cheap."""
    with _db_connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS deck_freeze ("
            "  slug TEXT PRIMARY KEY,"
            "  frozen INTEGER NOT NULL DEFAULT 0,"
            "  reason TEXT,"
            "  updated_at TEXT"
            ")"
        )


def set_frozen(slug: str, frozen: bool, reason: str = "") -> None:
    """Freeze (or un-freeze) a tenant's deck."""
    if not slug:
        raise ValueError("slug is required")
    ensure_schema()
    now = _now_iso()
    with _db_connect() as conn:
        if is_postgres():
            sql = (
                "INSERT INTO deck_freeze (slug, frozen, reason, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (slug) DO UPDATE SET "
                "  frozen = EXCLUDED.frozen, "
                "  reason = EXCLUDED.reason, "
                "  updated_at = EXCLUDED.updated_at"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO deck_freeze (slug, frozen, reason, updated_at) "
                "VALUES (?, ?, ?, ?)"
            )
        conn.execute(sql, (slug, 1 if frozen else 0, reason or "", now))


def is_frozen(slug: str) -> bool:
    """True iff the tenant's deck is frozen. Fails OPEN (returns False) on any
    error so a freeze-table glitch never blocks the other tenants' refresh —
    the caller logs; the demo deck is verified frozen out-of-band."""
    if not slug:
        return False
    try:
        ensure_schema()
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT frozen FROM deck_freeze WHERE slug = ?", (slug,)
            ).fetchone()
        if not row:
            return False
        val = row["frozen"] if hasattr(row, "keys") else row[0]
        return bool(val)
    except Exception as exc:  # noqa: BLE001 — guard must never raise into a build loop
        logger.warning(
            "deck_freeze.is_frozen failed for %s (treating as NOT frozen): %s", slug, exc
        )
        return False


def list_frozen() -> list[dict[str, Any]]:
    """Return the currently-frozen tenants (for the admin response)."""
    try:
        ensure_schema()
        with _db_connect() as conn:
            rows = conn.execute(
                "SELECT slug, frozen, reason, updated_at FROM deck_freeze WHERE frozen = 1"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(dict(r) if hasattr(r, "keys")
                       else {"slug": r[0], "frozen": r[1], "reason": r[2], "updated_at": r[3]})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("deck_freeze.list_frozen failed: %s", exc)
        return []
