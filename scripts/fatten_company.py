"""Phase 49.3 — fatten a thin company's deck: deep broad fetch (append new
genuine articles to disk) + rebuild the deck from the FULL on-disk corpus.

Unlike refetch_company (which rebuilds only from the fresh fetch and loses
already-processed genuine articles to URL-dedup), this keeps everything:
  1. fetch_for_company(max_per_query=50) — broad query (the slug is in
     `broad_query_companies`), persists NEW genuine articles to disk; the
     roundup + about-company guards drop market noise.
  2. rebuild build_company_deck from ALL on-disk articles (old genuine + new).

Preserves seeded calibration (uses get_company, no resolver). Postgres-only.

Usage:
    python scripts/fatten_company.py --only icici-bank
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("fatten")


def _load_disk_articles(slug: str):
    from engine.ingestion.news_fetcher import IngestedArticle
    arts, d = [], _ROOT / "data/inputs/news" / slug
    if not d.exists():
        return arts
    for f in sorted(d.glob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        arts.append(IngestedArticle(
            id=j["id"], title=j.get("title", ""), content=j.get("content", ""),
            summary=j.get("summary", ""), source=j.get("source", ""), url=j.get("url", ""),
            published_at=j.get("published_at", ""), company_slug=slug,
            source_type=j.get("source_type", "newsapi_ai"), metadata=j.get("metadata", {}),
        ))
    return arts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", required=True)
    ap.add_argument("--max-fetch", type=int, default=50)
    args = ap.parse_args()

    from engine.db.connection import is_postgres, connect
    if not is_postgres():
        logger.error("Postgres only."); return 2

    from engine.config import get_company, invalidate_companies_cache
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.analysis.deck_builder import build_company_deck
    from engine.models import onboarding_status

    invalidate_companies_cache()
    co = get_company(args.only)
    t0 = time.monotonic()

    # 1. deep broad fetch — appends NEW genuine articles to disk (persist=True)
    try:
        fetch_for_company(co, max_per_query=args.max_fetch)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch failed for %s: %s", args.only, exc)

    # 2. rebuild from the FULL on-disk corpus
    arts = _load_disk_articles(args.only)
    logger.warning("=== %s: rebuilding from %d on-disk articles ===", args.only, len(arts))
    try:
        with connect() as c:
            c.execute("DELETE FROM company_article_view WHERE company_slug = ?", (args.only,))
            c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear deck failed: %s", exc)

    deck = build_company_deck(co, arts, n_critical=3, n_total=10)
    try:
        onboarding_status.mark_ready(
            args.only, fetched=deck.fetched,
            analysed=deck.critical_published + deck.light_published,
            home_count=deck.critical_published, created_by_user="ci@snowkap.com")
    except Exception:
        pass

    print("\n" + "=" * 56)
    print("  FATTEN REPORT")
    print(f"    slug             {args.only}")
    print(f"    on-disk corpus   {len(arts)}")
    print(f"    critical         {deck.critical_published}")
    print(f"    light            {deck.light_published}")
    print(f"    approval_rejected{deck.approval_rejected}")
    print(f"    elapsed          {round(time.monotonic()-t0,1)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
