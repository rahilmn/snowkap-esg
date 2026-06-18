"""Phase 6 §8.1 — persona persistence.

Stores Personas keyed by user_id in SQLite. Mirrors the existing
``onboarding_status`` pattern (lazy schema, backend-aware connection).

The shape favours a single-row-per-user denormalised JSON blob for
simplicity. Lists / dicts (esg_focus, click_affinity, ...) live as
JSON strings; the model layer (de)serialises on read/write.

Schema:
    persona(
      user_id              TEXT PRIMARY KEY,
      role                 TEXT NOT NULL,
      esg_focus_json       TEXT,
      frameworks_json      TEXT,
      geographies_json     TEXT,
      horizon              TEXT,
      decision_style       TEXT,
      risk_appetite        TEXT,
      click_affinity_json  TEXT,
      skip_affinity_json   TEXT,
      last_active          TEXT,
      onboarded_at         TEXT NOT NULL,
      last_edited_at       TEXT,
      last_drift_update_at TEXT,
      version              INTEGER DEFAULT 1
    )
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from engine.db import connect as _db_connect, schema_ready, mark_schema_ready
from engine.persona.persona_model import (
    Persona,
    deserialise_persona,
)

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS persona (
    user_id              TEXT PRIMARY KEY,
    role                 TEXT NOT NULL,
    esg_focus_json       TEXT,
    frameworks_json      TEXT,
    geographies_json     TEXT,
    horizon              TEXT,
    decision_style       TEXT,
    risk_appetite        TEXT,
    click_affinity_json  TEXT,
    skip_affinity_json   TEXT,
    last_active          TEXT,
    onboarded_at         TEXT NOT NULL,
    last_edited_at       TEXT,
    last_drift_update_at TEXT,
    version              INTEGER DEFAULT 1
);
"""


@contextmanager
def _connect() -> Iterator[Any]:
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    if schema_ready("persona"):
        return
    try:
        from engine.index.sqlite_index import _ensure_wal_mode
        _ensure_wal_mode()
    except Exception:  # noqa: BLE001 — non-fatal in test contexts
        pass
    with _connect() as conn:
        if hasattr(conn, "executescript"):
            conn.executescript(SCHEMA_SQL)
        else:
            for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
                conn.execute(stmt)
    mark_schema_ready("persona")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dump(obj: Any) -> str:
    return json.dumps(obj or [], ensure_ascii=False)


def _load_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def upsert_persona(persona: Persona) -> Persona:
    """Insert-or-update. Bumps `last_edited_at` on every call.

    Returns the persona as-stored (with timestamps refreshed).
    """
    if not persona.user_id:
        raise ValueError("persona.user_id is required")
    ensure_schema()
    persona.last_edited_at = _now()

    row = (
        persona.user_id,
        persona.role,
        _dump(persona.esg_focus),
        _dump(persona.frameworks),
        _dump(persona.geographies),
        persona.horizon,
        persona.decision_style,
        persona.risk_appetite,
        _dump(persona.click_affinity),
        _dump(persona.skip_affinity),
        persona.last_active,
        persona.onboarded_at,
        persona.last_edited_at,
        persona.last_drift_update_at,
        persona.version,
    )

    with _connect() as conn:
        existing = conn.execute(
            "SELECT user_id FROM persona WHERE user_id = ?",
            (persona.user_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO persona ("
                "user_id, role, esg_focus_json, frameworks_json, "
                "geographies_json, horizon, decision_style, risk_appetite, "
                "click_affinity_json, skip_affinity_json, last_active, "
                "onboarded_at, last_edited_at, last_drift_update_at, version"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        else:
            conn.execute(
                "UPDATE persona SET "
                "role = ?, esg_focus_json = ?, frameworks_json = ?, "
                "geographies_json = ?, horizon = ?, decision_style = ?, "
                "risk_appetite = ?, click_affinity_json = ?, "
                "skip_affinity_json = ?, last_active = ?, "
                "onboarded_at = ?, last_edited_at = ?, "
                "last_drift_update_at = ?, version = ? "
                "WHERE user_id = ?",
                row[1:] + (persona.user_id,),
            )
    return persona


def get_persona(user_id: str) -> Persona | None:
    """Read one persona by user_id. Returns None if not found."""
    if not user_id:
        return None
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, role, esg_focus_json, frameworks_json, "
            "geographies_json, horizon, decision_style, risk_appetite, "
            "click_affinity_json, skip_affinity_json, last_active, "
            "onboarded_at, last_edited_at, last_drift_update_at, version "
            "FROM persona WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    # Backend-aware row access (sqlite3.Row supports both index and dict)
    def _at(key: str, idx: int) -> Any:
        try:
            return row[key]
        except (TypeError, KeyError, IndexError):
            return row[idx]

    return deserialise_persona({
        "user_id": _at("user_id", 0),
        "role": _at("role", 1),
        "esg_focus": _load_json(_at("esg_focus_json", 2), []),
        "frameworks": _load_json(_at("frameworks_json", 3), []),
        "geographies": _load_json(_at("geographies_json", 4), []),
        "horizon": _at("horizon", 5),
        "decision_style": _at("decision_style", 6),
        "risk_appetite": _at("risk_appetite", 7),
        "click_affinity": _load_json(_at("click_affinity_json", 8), {}),
        "skip_affinity": _load_json(_at("skip_affinity_json", 9), {}),
        "last_active": _at("last_active", 10),
        "onboarded_at": _at("onboarded_at", 11),
        "last_edited_at": _at("last_edited_at", 12),
        "last_drift_update_at": _at("last_drift_update_at", 13),
        "version": _at("version", 14),
    })


def delete_persona(user_id: str) -> bool:
    """Remove a persona. Returns True if a row was deleted."""
    if not user_id:
        return False
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM persona WHERE user_id = ?", (user_id,),
        )
        return bool(cur.rowcount and cur.rowcount > 0)


def total_count() -> int:
    """How many personas are stored. Used by /metrics for adoption tracking."""
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM persona")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("persona total_count failed: %s", exc)
        return 0


def count_by_role() -> dict[str, int]:
    """Persona count bucketed by role. Helps confirm the MCQ flow is
    surfacing a healthy mix (not e.g. 100% 'other' — which would mean
    the role-selection step is broken)."""
    ensure_schema()
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT role, COUNT(*) FROM persona GROUP BY role")
            return {str(r[0] or "other"): int(r[1]) for r in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("count_by_role failed: %s", exc)
        return {}


def record_click_affinity(
    user_id: str,
    topic: str,
    delta: float = 0.1,
) -> Persona | None:
    """Bump a topic's click_affinity by `delta`, clamped to [0, 1].
    Used by the auto-drift updater (called when a user opens / saves
    an article tagged with `topic`). Returns the updated persona or
    None if the user has no persona row yet.
    """
    if not user_id or not topic:
        return None
    p = get_persona(user_id)
    if p is None:
        return None
    current = p.click_affinity.get(topic, 0.0)
    p.click_affinity[topic] = max(0.0, min(1.0, current + delta))
    p.last_active = _now()
    p.last_drift_update_at = _now()
    return upsert_persona(p)
