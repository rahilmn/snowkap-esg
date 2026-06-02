"""Phase 50.1 — fetch FRESH news from NewsAPI.ai for one company, run the full
pipeline, and publish the single most-critical article as a CRITICAL card
(additive — does NOT clear the company's existing deck).

Demonstrates the live end-to-end flow: NewsAPI.ai fetch -> parse -> 12-stage
analysis -> lede + recs -> approval -> deck. The approval gate still gates
fabrication, so whatever lands is grounded.

Usage:
    python scripts/fetch_one_critical.py --slug adani-power
    python scripts/fetch_one_critical.py --slug idfc-first-bank
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
logger = logging.getLogger("fetch1crit")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--n-critical", type=int, default=1)
    args = ap.parse_args()

    from engine.db.connection import is_postgres
    if not is_postgres():
        logger.error("Postgres only.")
        return 2

    from engine.config import get_company
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.analysis.deck_builder import build_company_deck

    co = get_company(args.slug)
    t0 = time.monotonic()
    fresh = fetch_for_company(co, max_per_query=18)  # NewsAPI.ai call + guards
    print(f"fetched {len(fresh)} fresh article(s) for {args.slug}")
    for a in fresh[:12]:
        print(f"   - {(a.title or '')[:78]}")
    if not fresh:
        print("No NEW fresh articles (recent window already processed). "
              "Try a different company or widen the window.")
        return 0

    # Build additively: publish the top-n_critical as CRITICAL, no light backfill,
    # and DON'T clear the existing deck (build_company_deck only upserts).
    deck = build_company_deck(co, fresh, n_critical=args.n_critical, n_total=args.n_critical)
    print(f"\n  critical_published={deck.critical_published} "
          f"approval_rejected={deck.approval_rejected} ({time.monotonic()-t0:.0f}s)")
    for it in deck.published_items:
        print(f"   -> tier={it.get('tier')} has_recs={it.get('has_recs')} | {it.get('title','')[:72]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
