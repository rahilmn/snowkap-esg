"""Phase 49.2 — re-fetch + rebuild a single company's deck WITHOUT re-resolving.

Unlike `reonboard_nine.py`, this does NOT call the LLM company resolver and does
NOT upsert the company row — so it PRESERVES the seeded `news_concept_uri` /
`news_aliases` in `primitive_calibration_json` (the disambiguation keys for
niche tenants like MAHLE / Singularity AMC). It simply:

  1. loads the existing company (with its seeded calibration) via get_company,
  2. clears the company's current deck rows,
  3. fetch_for_company (NewsAPI.ai concept/alias query + roundup guard),
  4. build_company_deck (3 critical + 7 light, with the approval gate),
  5. marks onboarding ready.

Usage:
    python scripts/refetch_company.py --only mahle
    python scripts/refetch_company.py --only singularity-amc
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("refetch")


def _refetch_one(slug: str) -> dict:
    from engine.config import get_company, invalidate_companies_cache
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.analysis.deck_builder import build_company_deck
    from engine.models import onboarding_status
    from engine.db.connection import connect

    invalidate_companies_cache()
    try:
        co = get_company(slug)
    except Exception as exc:  # noqa: BLE001
        return {"slug": slug, "status": f"company_load_failed: {exc}"}

    t0 = time.monotonic()
    # clear current deck rows so the rebuild replaces rather than accumulates
    try:
        with connect() as c:
            c.execute("DELETE FROM company_article_view WHERE company_slug = ?", (slug,))
            c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not clear deck for %s: %s", slug, exc)

    fresh = fetch_for_company(co, max_per_query=18)
    deck = build_company_deck(co, fresh, n_critical=3, n_total=10)

    try:
        onboarding_status.mark_ready(
            slug, fetched=deck.fetched,
            analysed=deck.critical_published + deck.light_published,
            home_count=deck.critical_published, created_by_user="ci@snowkap.com",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_ready failed for %s: %s", slug, exc)

    return {
        "slug": slug,
        "fetched": deck.fetched,
        "critical": deck.critical_published,
        "light": deck.light_published,
        "approval_rejected": getattr(deck, "approval_rejected", "?"),
        "elapsed": round(time.monotonic() - t0, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", required=True, help="company slug to re-fetch")
    args = ap.parse_args()

    from engine.db.connection import is_postgres
    if not is_postgres():
        logger.error("Postgres only.")
        return 2

    res = _refetch_one(args.only)
    print("\n" + "=" * 56)
    print("  REFETCH REPORT")
    for k, v in res.items():
        print(f"    {k:<18} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
