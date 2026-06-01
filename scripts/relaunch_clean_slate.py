"""Phase 48.G — clean Supabase to exactly the 9 launch companies.

STRICTLY Postgres. Refuses to run on SQLite (asserts is_postgres()).

Wipes every company/article/deck footprint, trims config/companies.json
to the 7 overlapping baseline companies (MAHLE + SBI are created in the
DB by the re-onboard), wipes on-disk news + outputs, and reseeds the
forum welcome threads.

Preserves: chat_conversations, chat_messages, tenant_memory,
scheduler_state, llm_calls.

Usage:
    python scripts/relaunch_clean_slate.py --confirm
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)5s %(message)s")
logger = logging.getLogger("relaunch")

# The 7 baseline companies (of the 9) that already live in config/companies.json.
# MAHLE + SBI are NOT in JSON — they get created in the DB by reonboard_nine.py.
KEEP_SLUGS = {
    "icici-bank", "yes-bank", "idfc-first-bank", "waaree-energies",
    "singularity-amc", "adani-power", "jsw-energy",
}

# FK-safe wipe order. Each wrapped in try/except so a missing table on a
# given deploy doesn't abort the run.
WIPE_TABLES = [
    "article_comment_votes",
    "article_comments",
    "user_bookmarks",
    "company_article_view",
    "article_pool",
    "article_index",
    "article_analysis_status",
    "slug_aliases",
    "onboarding_status",
    "onboarding_events",
    "companies",
    "tenant_registry",
]

PRESERVE_NOTE = (
    "chat_conversations, chat_messages, tenant_memory, scheduler_state, "
    "llm_calls (preserved)"
)


def _assert_postgres() -> None:
    from engine.db.connection import get_backend, is_postgres
    if not is_postgres():
        logger.error(
            "Backend is '%s' — this script is Supabase-Postgres ONLY. "
            "Set SNOWKAP_DB_BACKEND=postgres + SUPABASE_DATABASE_URL.",
            get_backend(),
        )
        sys.exit(2)


def _trim_companies_json() -> None:
    path = _REPO_ROOT / "config" / "companies.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    companies = data.get("companies", [])
    before = len(companies)
    kept = [c for c in companies if c.get("slug") in KEEP_SLUGS]
    data["companies"] = kept
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    dropped = [c.get("slug") for c in companies if c.get("slug") not in KEEP_SLUGS]
    logger.info("config/companies.json trimmed: %d -> %d (dropped: %s)",
                before, len(kept), ", ".join(dropped) or "none")


def _wipe_postgres() -> None:
    from engine.db.connection import connect
    with connect() as c:
        for table in WIPE_TABLES:
            try:
                c.execute(f"DELETE FROM {table}")
                logger.info("  wiped %s", table)
            except Exception as exc:  # noqa: BLE001
                logger.warning("  skip %s (%s)", table, type(exc).__name__)
        c.commit()
    logger.info("Postgres wiped. %s", PRESERVE_NOTE)


def _wipe_disk() -> None:
    for rel in ("data/inputs/news", "data/outputs"):
        d = _REPO_ROOT / rel
        if d.exists():
            for child in d.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("  could not remove %s (%s)", child, exc)
            logger.info("  cleared %s/*", rel)
    # Drop the processed-hash ledger so re-onboard isn't deduped against
    # the wiped corpus.
    ph = _REPO_ROOT / "data" / "processed" / "article_hashes.json"
    if ph.exists():
        ph.unlink()
        logger.info("  cleared data/processed/article_hashes.json")


def _reseed_forum() -> None:
    try:
        from engine.models import forum_threads
        forum_threads.seed_welcome_threads()
        logger.info("Forum welcome threads reseeded.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Forum reseed failed (non-fatal): %s", exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="actually wipe")
    args = ap.parse_args()

    _assert_postgres()

    if not args.confirm:
        logger.warning("DRY RUN. Pass --confirm to wipe. Would:")
        logger.warning("  - trim config/companies.json to %d companies", len(KEEP_SLUGS))
        logger.warning("  - DELETE FROM: %s", ", ".join(WIPE_TABLES))
        logger.warning("  - clear data/inputs/news/* + data/outputs/*")
        logger.warning("  - reseed forum welcome threads")
        return 0

    logger.info("=== Phase 48.G clean slate (Postgres) ===")
    _trim_companies_json()
    _wipe_postgres()
    _wipe_disk()
    _reseed_forum()

    # Invalidate the in-process company cache so the next load reflects the trim.
    try:
        from engine.config import invalidate_companies_cache
        invalidate_companies_cache()
    except Exception:  # noqa: BLE001
        pass

    logger.info("=== Clean slate done. Run scripts/reonboard_nine.py next. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
