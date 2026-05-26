"""Phase 11B — Track the async onboarding pipeline for any new tenant.

When an admin onboards a new company via POST /api/admin/onboard, three
things happen in sequence in a background task:

  1. `fetch_for_company(slug, limit=10)` — pull ~10 ESG-filtered articles
     via NewsAPI.ai / Google News for the target company.
  2. For each fetched article → `process_article()` → 12-stage pipeline.
  3. Ready — dashboard returns real analysed insights at `/home?company=<slug>`.

This table records progress so the frontend modal can poll and show
("Fetching 10 articles…" → "Analysing 3/10…" → "Ready"). Errors bubble up
into `error` so the admin knows what broke without reading logs.

Schema:
    onboarding_status(
      slug         TEXT PRIMARY KEY,
      state        TEXT NOT NULL,    -- 'pending'|'fetching'|'analysing'|'ready'|'failed'
      fetched      INTEGER DEFAULT 0,
      analysed     INTEGER DEFAULT 0,
      home_count   INTEGER DEFAULT 0,
      started_at   TEXT NOT NULL,
      finished_at  TEXT,
      error        TEXT
    )
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from engine.db import connect as _db_connect
from engine.index.sqlite_index import DB_PATH, _ensure_wal_mode  # noqa: F401

logger = logging.getLogger(__name__)

OnboardState = Literal["pending", "fetching", "analysing", "ready", "failed"]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS onboarding_status (
    slug        TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    fetched     INTEGER DEFAULT 0,
    analysed    INTEGER DEFAULT 0,
    home_count  INTEGER DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    error       TEXT
);
"""

_SCHEMA_READY = False


@contextmanager
def _connect() -> Iterator[Any]:
    """Backend-aware connection (Phase 24)."""
    with _db_connect() as conn:
        yield conn


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ensure_wal_mode()
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
    _SCHEMA_READY = True


@dataclass
class OnboardingStatus:
    slug: str
    state: str
    fetched: int
    analysed: int
    home_count: int
    started_at: str
    finished_at: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "state": self.state,
            "fetched": self.fetched,
            "analysed": self.analysed,
            "home_count": self.home_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert(slug: str, **fields: Any) -> OnboardingStatus:
    """Create or update the row. Always updates whatever fields are passed.
    State transitions: pending → fetching → analysing → ready | failed.

    Phase 21 — when transitioning to a non-failed state without an explicit
    `error=` argument, clear any stale error from a previous attempt. Bug
    surfaced 2026-04-29: an alias slug retained the failed-attempt error
    after a successful retry, making the onboarding modal show "Failed"
    when the canonical pipeline had succeeded.
    """
    ensure_schema()
    allowed = {"state", "fetched", "analysed", "home_count", "finished_at", "error"}
    payload = {k: v for k, v in fields.items() if k in allowed}
    # Clear stale error when transitioning to a non-failed state and the
    # caller hasn't explicitly set the error field.
    new_state = payload.get("state")
    if new_state and new_state != "failed" and "error" not in payload:
        payload["error"] = None

    with _connect() as conn:
        existing = conn.execute(
            "SELECT slug FROM onboarding_status WHERE slug = ?", (slug,)
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO onboarding_status (slug, state, fetched, analysed, home_count, started_at, finished_at, error)
                VALUES (:slug, :state, :fetched, :analysed, :home_count, :started_at, :finished_at, :error)
                """,
                {
                    "slug": slug,
                    "state": payload.get("state", "pending"),
                    "fetched": payload.get("fetched", 0),
                    "analysed": payload.get("analysed", 0),
                    "home_count": payload.get("home_count", 0),
                    "started_at": _now(),
                    "finished_at": payload.get("finished_at"),
                    "error": payload.get("error"),
                },
            )
        else:
            if not payload:
                return get(slug)  # type: ignore[return-value]
            assignments = ", ".join(f"{k} = :{k}" for k in payload.keys())
            conn.execute(
                f"UPDATE onboarding_status SET {assignments} WHERE slug = :slug",
                {**payload, "slug": slug},
            )

    return get(slug)  # type: ignore[return-value]


def claim_pending(slug: str) -> bool:
    """Atomically reserve the right to schedule onboarding for `slug`.

    Returns True iff this caller inserted a fresh `state='pending'` row.
    Returns False if a row already exists (another login already kicked
    off the pipeline). Used by `_ensure_tenant_for_login` so two
    simultaneous first-time logins for the same prospect don't both
    enqueue `_background_onboard` and double-charge the news API.

    Implementation note: `INSERT OR IGNORE` against the PRIMARY KEY
    (`slug`) is atomic in SQLite — `cursor.rowcount == 1` only when
    the insert actually happened.
    """
    ensure_schema()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO onboarding_status
                (slug, state, fetched, analysed, home_count, started_at, finished_at, error)
            VALUES
                (?, 'pending', 0, 0, 0, ?, NULL, NULL)
            """,
            (slug, _now()),
        )
        return cur.rowcount == 1


def get(slug: str) -> OnboardingStatus | None:
    ensure_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM onboarding_status WHERE slug = ?", (slug,)
        ).fetchone()
        return OnboardingStatus(**dict(row)) if row else None


def mark_failed(slug: str, error: str) -> None:
    upsert(slug, state="failed", error=error[:500], finished_at=_now())


def mark_ready(slug: str, *, fetched: int | None = None, analysed: int | None = None,
               home_count: int | None = None, created_by_user: str | None = None) -> None:
    """Mark a slug as ready. Phase 21 — accept stats so an alias slug can
    mirror the canonical row's progress when the onboarder adjusts slugs
    (e.g. "tatachemicals" → "tata-chemicals-limited").

    Phase 28 — also dual-write the company row into the ``companies``
    table from the just-written ``config/companies.json`` entry. This is
    the source-of-truth move: Supabase becomes authoritative for newly-
    onboarded tenants while the 7 baseline companies still hydrate from
    JSON for back-compat. ``created_by_user`` (optional) records the
    auth-context email that triggered the onboard.
    """
    extras: dict[str, Any] = {}
    if fetched is not None:
        extras["fetched"] = fetched
    if analysed is not None:
        extras["analysed"] = analysed
    if home_count is not None:
        extras["home_count"] = home_count
    upsert(slug, state="ready", finished_at=_now(), **extras)

    # Phase 28 — dual-write to the companies table. Failures here must
    # NOT mark the onboarding as failed (the worker already finished
    # the article pipeline); we log and continue.
    try:
        _dual_write_company(slug, created_by_user=created_by_user)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mark_ready: companies-table dual-write failed for slug=%s: %s",
            slug, exc,
        )


def _dual_write_company(slug: str, *, created_by_user: str | None) -> None:
    """Phase 28 — persist the just-onboarded Company into the
    ``companies`` SQLite/Postgres table.

    Reads the freshly-written ``config/companies.json`` entry produced
    by the onboarder, then upserts into the persistent table. Drops the
    ``load_companies`` lru_cache so the live process surfaces the new
    tenant on its next read.
    """
    from engine.config import get_company, invalidate_companies_cache
    from engine.models.companies_store import upsert as company_upsert

    # Refresh cache so we read the line the onboarder just wrote.
    invalidate_companies_cache()
    try:
        company = get_company(slug)
    except KeyError:
        logger.warning("mark_ready: no companies.json entry for slug=%s", slug)
        return

    company_upsert(
        slug=company.slug,
        name=company.name,
        domain=company.domain or None,
        industry=company.industry,
        market_cap_tier=company.market_cap,
        yfinance_ticker=company.yfinance_ticker,
        eodhd_ticker=company.eodhd_ticker,
        framework_region=company.framework_region,
        revenue_cr=(
            float(company.primitive_calibration.get("revenue_cr"))
            if company.primitive_calibration and "revenue_cr" in company.primitive_calibration
            else None
        ),
        primitive_calibration=company.primitive_calibration,
        created_by_user=created_by_user,
        status="active",
        # Phase 31 — persist the LLM-crafted live-fetch queries so
        # /api/news/live can read them without going to companies.json.
        sustainability_query=getattr(company, "sustainability_query", None),
        general_query=getattr(company, "general_query", None),
    )
    # Invalidate again so subsequent reads see the DB row (not the
    # JSON-only snapshot we just hydrated from).
    invalidate_companies_cache()


def reset(slug: str) -> None:
    """Phase 22.3 — wipe the onboarding row for `slug` so a subsequent
    `claim_pending(slug)` succeeds.

    Used by the self-service `/news/onboarding-retry` endpoint: a
    prospect whose first run failed (or finished ready-but-empty) can
    request a fresh attempt from the UI without an admin in the loop.
    Idempotent — no-op if the row doesn't exist.
    """
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))


def force_claim_pending(slug: str) -> bool:
    """Phase 22.3 (race-fix) — atomically reset+re-claim in ONE
    transaction so two parallel `/news/onboarding-retry` calls for the
    same slug can't BOTH end up scheduling `_background_onboard`.

    Pre-fix the retry endpoint did `reset(); claim_pending()` as two
    separate transactions: under load, both callers could observe an
    empty row after each other's DELETE and both INSERTs would
    succeed (the second under `INSERT OR IGNORE` because the row was
    just gone), spawning duplicate background pipelines and
    double-charging NewsAPI.

    Returns True iff this caller inserted a fresh `state='pending'`
    row. Returns False if another caller raced ahead — the loser
    should surface 409 to the user.

    Atomicity: SQLite serialises writers per-database; we wrap
    DELETE + INSERT in a single connection so the writer lock isn't
    released between them. The IMMEDIATE transaction upgrade ensures
    we hold the write lock across both statements.
    """
    ensure_schema()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM onboarding_status WHERE slug = ?", (slug,))
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO onboarding_status
                    (slug, state, fetched, analysed, home_count, started_at, finished_at, error)
                VALUES
                    (?, 'pending', 0, 0, 0, ?, NULL, NULL)
                """,
                (slug, _now()),
            )
            inserted = cur.rowcount == 1
            conn.execute("COMMIT")
            return inserted
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _truncate_all() -> None:
    ensure_schema()
    with _connect() as conn:
        conn.execute("DELETE FROM onboarding_status")
