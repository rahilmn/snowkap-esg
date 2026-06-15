"""Phase 51.B — durable Postgres store for the full insight detail payload.

The insight detail JSON (the 9-section deep insight + perspectives +
recommendations + unified analysis + lede) was persisted ONLY to the
container filesystem (``data/outputs/{slug}/insights/*.json``). On Railway
that filesystem is EPHEMERAL — a restart/redeploy wiped every
runtime-generated insight, so the detail view (`/api/insights/{id}`)
returned HTTP 202 "regenerating" and kicked off a fresh, billable LLM
run. (It's also why ~457 insight files were committed to git — baking
them into the image to survive deploys.)

This table mirrors that exact payload into Supabase so the detail view
survives restarts. The writer dual-writes (disk + here); the read path
(`api/routes/insights.py::insight_detail`) reads here FIRST and falls
back to disk for insights written before this store existed. Once a
backfill populates every row, ``data/outputs`` can leave the image.

Backend-agnostic (SQLite dev / Postgres prod via ``engine.db.connect``),
mirroring the article_pool / company_article_view model conventions.

Public surface:
  * ensure_schema() -> None
  * upsert(article_id, company_slug, payload) -> None
  * get(article_id) -> dict | None
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from engine.db import connect as _db_connect, is_postgres

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _to_jsonb_param(value: Any) -> Any:
    """Postgres JSONB wants a JSON string; SQLite TEXT stores it verbatim."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _from_jsonb_value(value: Any) -> Any:
    """psycopg2 returns JSONB as parsed dict/list; SQLite returns raw TEXT."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value


def ensure_schema() -> None:
    """Create the ``insight_payload`` table if absent.

    ``CREATE TABLE IF NOT EXISTS`` is portable across Postgres and SQLite,
    so this needs no migration file and no ALTER on the hot article_pool
    table. Idempotent and cheap; called at the start of upsert/get.
    """
    payload_type = "JSONB" if is_postgres() else "TEXT"
    with _db_connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS insight_payload ("
            "  article_id TEXT PRIMARY KEY,"
            "  company_slug TEXT,"
            f"  payload {payload_type},"
            "  schema_version TEXT,"
            "  updated_at TEXT"
            ")"
        )


def upsert(article_id: str, company_slug: str, payload: dict[str, Any]) -> None:
    """Insert or replace the full insight detail payload for an article."""
    if not article_id:
        raise ValueError("article_id is required")
    ensure_schema()
    try:
        schema_version = str((payload.get("meta") or {}).get("schema_version") or "")
    except AttributeError:
        schema_version = ""
    now = _now_iso()
    with _db_connect() as conn:
        if is_postgres():
            sql = (
                "INSERT INTO insight_payload "
                "  (article_id, company_slug, payload, schema_version, updated_at) "
                "VALUES (?, ?, ?::jsonb, ?, ?) "
                "ON CONFLICT (article_id) DO UPDATE SET "
                "  company_slug = EXCLUDED.company_slug, "
                "  payload = EXCLUDED.payload, "
                "  schema_version = EXCLUDED.schema_version, "
                "  updated_at = EXCLUDED.updated_at"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO insight_payload "
                "  (article_id, company_slug, payload, schema_version, updated_at) "
                "VALUES (?, ?, ?, ?, ?)"
            )
        conn.execute(sql, (
            article_id, company_slug or "",
            _to_jsonb_param(payload or {}), schema_version, now,
        ))


def get(article_id: str) -> dict[str, Any] | None:
    """Return the stored insight payload dict, or None if absent."""
    if not article_id:
        return None
    ensure_schema()
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT payload FROM insight_payload WHERE article_id = ?",
            (article_id,),
        ).fetchone()
    if not row:
        return None
    raw = row["payload"] if hasattr(row, "keys") else row[0]
    payload = _from_jsonb_value(raw)
    return payload if isinstance(payload, dict) else None
