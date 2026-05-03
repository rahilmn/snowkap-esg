#!/usr/bin/env python
"""One-time wipe + re-seed.

DESTRUCTIVE — backs up the current intelligence state and re-runs the
ingest pipeline cold. Use when:

* The schema changed and the existing analyses don't match the new
  contract (e.g. Phase 22.x verifier regressions, schema_version bump)
* You're cutting over from SQLite to Postgres and want a clean slate
* Article quality has drifted and you'd rather start over than try to
  invalidate piecemeal

Steps:

    1. Snapshot ``data/outputs/`` to ``data/outputs.backup-<timestamp>/``
       (move, not copy — disk-cheap, instantly reversible by renaming
       back if something goes wrong before step 4 completes).
    2. Truncate ``article_index`` and ``article_analysis_status`` rows.
       Other tables (tenant_registry, llm_calls, campaigns, …) are
       preserved.
    3. Recreate the empty ``data/outputs/`` directory tree.
    4. Run a full ``ingest --all`` to re-fetch news (NewsAPI.ai primary,
       Google News fallback) and re-run the 12-stage pipeline.

Two safety gates:

    * Requires ``--yes-i-mean-it`` flag (no interactive prompt because
      auto-mode flows can't answer it).
    * Requires ``SNOWKAP_WIPE_AUTHORIZED=1`` env var as a second
      confirmation (set by the operator running the wipe).

Backup is NOT auto-deleted — operator decides when to remove it.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.config import get_data_path  # noqa: E402
from engine.db import connect, get_backend  # noqa: E402

logging.basicConfig(
    level=os.environ.get("SNOWKAP_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wipe_and_reseed")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _outputs_dir() -> Path:
    return get_data_path("outputs")


def step_backup_outputs(timestamp: str) -> Path:
    """Move ``data/outputs/`` to ``data/outputs.backup-<timestamp>/``."""
    src = _outputs_dir()
    if not src.exists():
        logger.info("step 1: outputs dir does not exist — nothing to backup")
        return src
    dst = src.parent / f"outputs.backup-{timestamp}"
    shutil.move(str(src), str(dst))
    src.mkdir(parents=True, exist_ok=True)
    logger.info("step 1: backed up outputs/ -> %s", dst)
    return dst


def step_truncate_index(dry_run: bool = False) -> dict[str, int]:
    """Truncate the article_index + article_analysis_status tables."""
    counts: dict[str, int] = {}
    with connect() as conn:
        for table in ("article_index", "article_analysis_status"):
            try:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(row["n"]) if row else 0
            except Exception:
                counts[table] = 0
        if dry_run:
            logger.info("step 2: DRY RUN — would truncate %s", counts)
            return counts
        for table in ("article_index", "article_analysis_status"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("step 2: could not truncate %s: %s", table, exc)
        # Reset article_hashes so the dedup file doesn't keep us from
        # re-fetching identical URLs we just discarded.
        try:
            hashes_path = get_data_path("processed") / "article_hashes.json"
            if hashes_path.exists():
                hashes_path.write_text('{"hashes": []}', encoding="utf-8")
                logger.info("step 2b: reset article_hashes.json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("step 2b: could not reset article_hashes.json: %s", exc)
    logger.info("step 2: truncated %s", counts)
    return counts


def step_run_ingest(max_per_query: int, limit: int) -> None:
    """Run a fresh ingest+analyse across every tenant."""
    from engine.main import cmd_ingest

    class _IngestArgs:
        all = True
        company = None

        def __init__(self, max_: int, lim: int) -> None:
            self.max = max_
            self.limit = lim

    logger.info("step 4: running ingest --all (max_per_query=%d, limit=%d)", max_per_query, limit)
    t0 = time.perf_counter()
    cmd_ingest(_IngestArgs(max_per_query, limit))
    logger.info("step 4: ingest finished in %.1fs", time.perf_counter() - t0)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_wipe_and_reseed(
    *,
    skip_ingest: bool = False,
    max_per_query: int = 10,
    limit: int = 5,
    dry_run: bool = False,
) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    logger.info("=== WIPE + RESEED start (backend=%s, ts=%s) ===", get_backend(), timestamp)

    summary: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": get_backend(),
        "timestamp": timestamp,
        "dry_run": dry_run,
        "skip_ingest": skip_ingest,
    }

    if dry_run:
        logger.info("DRY RUN — no destructive changes will be made")

    # Step 1: backup
    if not dry_run:
        backup_path = step_backup_outputs(timestamp)
        summary["backup_path"] = str(backup_path)
    else:
        summary["backup_path"] = "(dry-run, no backup made)"

    # Step 2: truncate index
    summary["truncate_counts"] = step_truncate_index(dry_run=dry_run)

    # Step 3: ensure outputs dir exists (already done in step 1 unless dry-run)
    if not dry_run:
        _outputs_dir().mkdir(parents=True, exist_ok=True)

    # Step 4: ingest
    if dry_run:
        logger.info("step 4: DRY RUN — skipping ingest")
        summary["ingest_status"] = "skipped (dry-run)"
    elif skip_ingest:
        logger.info("step 4: --skip-ingest — leaving outputs/ empty")
        summary["ingest_status"] = "skipped (--skip-ingest)"
    else:
        try:
            step_run_ingest(max_per_query=max_per_query, limit=limit)
            summary["ingest_status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.exception("step 4: ingest failed: %s", exc)
            summary["ingest_status"] = f"failed: {exc}"

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    logger.info("=== WIPE + RESEED done ===")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes-i-mean-it",
        action="store_true",
        help="Required. Confirms you want to wipe the article index + outputs.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Truncate + backup but don't re-fetch news. Useful for clean migration test.",
    )
    parser.add_argument("--max-per-query", type=int, default=10)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen, change nothing.")
    args = parser.parse_args(argv)

    if not args.dry_run:
        if not args.yes_i_mean_it:
            print(
                "ERROR: this is a destructive operation. Re-run with "
                "`--yes-i-mean-it` to confirm.",
                file=sys.stderr,
            )
            return 2
        if os.environ.get("SNOWKAP_WIPE_AUTHORIZED", "").strip() != "1":
            print(
                "ERROR: SNOWKAP_WIPE_AUTHORIZED=1 must be set in the env "
                "as a second confirmation. Re-run with:\n"
                "  SNOWKAP_WIPE_AUTHORIZED=1 python scripts/wipe_and_reseed.py --yes-i-mean-it",
                file=sys.stderr,
            )
            return 2

    summary = run_wipe_and_reseed(
        skip_ingest=args.skip_ingest,
        max_per_query=args.max_per_query,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    print()
    print("Backend       :", summary["backend"])
    print("Backup        :", summary["backup_path"])
    print("Truncated     :", summary["truncate_counts"])
    print("Ingest        :", summary["ingest_status"])
    print("Started       :", summary["started_at"])
    print("Finished      :", summary["finished_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
