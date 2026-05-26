"""Phase 32 — External benchmarks reader.

Surfaces CSV-imported third-party ratings (MSCI, SBTI, CRISIL, NSE ESG, …)
on the unified-analysis "What to watch" bullet. Live API integrations are
deferred (Phase 30 procurement effort); this module backs a read-only
scaffold so the visual surface is right today and the rows are easy to
swap when live data arrives.

Used by:
  * ``engine.analysis.unified_analysis.build_unified_analysis`` — calls
    ``get_benchmarks_for_company(slug, max=4)`` and stamps the result on
    ``analysis.what_to_watch.benchmarks``. Hidden in the UI when empty.
  * ``scripts/import_benchmarks.py`` — populates the table from a CSV.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.db import connect

logger = logging.getLogger(__name__)


def get_benchmarks_for_company(
    slug: str, max_n: int = 4,
) -> list[dict[str, Any]]:
    """Return the most-recent ``max_n`` benchmark rows for ``slug``.

    Sort: ``as_of_date`` DESC, then ``source`` ASC.

    Returns an empty list when the table is missing, the slug has no
    rows, or any DB hiccup happens — callers (the unified-analysis
    composer) treat empty as "hide the benchmarks chip".
    """
    if not slug or max_n <= 0:
        return []
    try:
        with connect() as c:
            cur = c.execute(
                """
                SELECT source, metric, value, as_of_date
                FROM company_benchmarks
                WHERE slug = ?
                ORDER BY as_of_date DESC, source ASC
                LIMIT ?
                """,
                (slug, int(max_n)),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            # Tolerant of dict-cursor + tuple shapes.
            if hasattr(r, "keys"):
                out.append({
                    "source": r["source"],
                    "metric": r["metric"],
                    "value": r["value"],
                    "as_of": r["as_of_date"],
                })
            else:
                out.append({
                    "source": r[0], "metric": r[1], "value": r[2], "as_of": r[3],
                })
        return out
    except Exception as exc:  # noqa: BLE001 — table-missing on first run is normal
        logger.debug("benchmarks: query failed for %s (%s)", slug, exc)
        return []


def upsert_benchmark(
    slug: str, source: str, metric: str, value: str, as_of_date: str,
) -> None:
    """Insert a single benchmark row (idempotent via composite PK)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with connect() as c:
        c.execute(
            """
            INSERT INTO company_benchmarks (slug, source, metric, value, as_of_date, imported_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (slug, source, metric, as_of_date) DO UPDATE
                SET value = excluded.value,
                    imported_at = excluded.imported_at
            """,
            (slug, source, metric, value, as_of_date, now),
        )


__all__ = ["get_benchmarks_for_company", "upsert_benchmark"]
