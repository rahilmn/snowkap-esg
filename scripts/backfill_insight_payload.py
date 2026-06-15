"""Phase 51.B backfill — mirror on-disk insight payloads into Postgres.

Walks ``data/outputs/{slug}/insights/*.json`` and upserts each into the
``insight_payload`` table (``engine.models.insight_payload``) so the durable
DB mirror is populated for insights written before the writer's dual-write
existed. Once this has run cleanly against prod, ``data/outputs`` can be
dropped from git + the image — ``insight_detail`` falls back to the mirror
when the on-disk file is gone.

Idempotent + resumable: ``upsert`` is INSERT OR REPLACE / ON CONFLICT, so a
re-run simply re-mirrors. Use ``--skip-existing`` to skip rows already in the
table (cheap re-runs) and ``--dry-run`` to preview without writing.

Usage:
    # Preview what would be mirrored (no writes):
    python scripts/backfill_insight_payload.py --dry-run

    # Mirror everything (prod = Postgres via SUPABASE_DATABASE_URL):
    python scripts/backfill_insight_payload.py

    # Resume / cheap re-run, skipping already-mirrored rows:
    python scripts/backfill_insight_payload.py --skip-existing

Exit code 0 on a clean run, 1 if any file errored.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the project root importable when run as a standalone script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import get_data_path  # noqa: E402
from engine.db.connection import is_postgres  # noqa: E402
from engine.models import insight_payload as insight_payload_store  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_insight_payload")


def _iter_insight_files(outputs_root: Path):
    """Yield (company_slug, path) for every data/outputs/{slug}/insights/*.json."""
    if not outputs_root.exists():
        return
    for slug_dir in sorted(p for p in outputs_root.iterdir() if p.is_dir()):
        insights_dir = slug_dir / "insights"
        if not insights_dir.is_dir():
            continue
        for f in sorted(insights_dir.glob("*.json")):
            yield slug_dir.name, f


def run(*, dry_run: bool, skip_existing: bool, limit: int) -> dict[str, int]:
    outputs_root = Path(get_data_path("outputs"))
    backend = "postgres" if is_postgres() else "sqlite"
    logger.info(
        "backfill starting: outputs_root=%s backend=%s dry_run=%s skip_existing=%s",
        outputs_root, backend, dry_run, skip_existing,
    )
    if not dry_run:
        insight_payload_store.ensure_schema()

    stats = {"scanned": 0, "upserted": 0, "skipped": 0, "errors": 0}
    for slug, path in _iter_insight_files(outputs_root):
        stats["scanned"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip unreadable %s: %s", path.name, exc)
            stats["errors"] += 1
            continue

        article = payload.get("article") or {}
        # Authoritative id from the payload; fall back to the filename suffix
        # (data/outputs/{slug}/insights/{YYYY-MM-DD}_{article_id}.json).
        article_id = article.get("id") or path.stem.split("_", 1)[-1]
        company_slug = article.get("company_slug") or slug
        if not article_id:
            logger.warning("skip %s: no article id", path.name)
            stats["errors"] += 1
            continue

        if skip_existing and not dry_run:
            try:
                if insight_payload_store.get(article_id) is not None:
                    stats["skipped"] += 1
                    continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("existence check failed for %s: %s", article_id, exc)

        if dry_run:
            logger.info("[dry-run] would mirror %s (%s)", article_id, company_slug)
            stats["upserted"] += 1
        else:
            try:
                insight_payload_store.upsert(article_id, company_slug, payload)
                stats["upserted"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("upsert failed for %s: %s", article_id, exc)
                stats["errors"] += 1
                continue

        if limit and stats["upserted"] >= limit:
            logger.info("hit --limit %d, stopping early", limit)
            break

    logger.info(
        "backfill DONE: scanned=%(scanned)d upserted=%(upserted)d "
        "skipped=%(skipped)d errors=%(errors)d", stats,
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror on-disk insight payloads into Postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; no DB writes.")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip articles already present in insight_payload.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N upserts (0 = all).")
    args = parser.parse_args(argv)
    stats = run(dry_run=args.dry_run, skip_existing=args.skip_existing, limit=args.limit)
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
