"""Phase 50.1 — promote ONE specific on-disk article to a full CRITICAL card
(Stage 10-12 + lede + recs + Opus/gpt approval), without disturbing the rest of
the company's deck.

Unlike the deck builder (which ranks and may leave a positive story in the light
tier), this forces the chosen article through `_publish_critical`. The approval
gate still runs — if the composed analysis isn't grounded, it's rejected and
demoted to light (we never show a fabricated critical).

Usage:
    python scripts/promote_to_critical.py --slug adani-power --match "Invest ₹6.5 Lakh Cr"
    python scripts/promote_to_critical.py --slug adani-power --match "GVK Energy"
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
logger = logging.getLogger("promote")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--match", required=True, help="case-insensitive title substring")
    args = ap.parse_args()

    from engine.db.connection import is_postgres
    if not is_postgres():
        logger.error("Postgres only.")
        return 2

    from engine.config import get_company
    from engine.ingestion.news_fetcher import IngestedArticle
    from engine.analysis.deck_builder import build_company_deck

    co = get_company(args.slug)
    d = _ROOT / "data" / "inputs" / "news" / args.slug
    matches = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if args.match.lower() in (j.get("title") or "").lower():
            matches.append(IngestedArticle(
                id=j["id"], title=j.get("title", ""), content=j.get("content", ""),
                summary=j.get("summary", ""), source=j.get("source", ""), url=j.get("url", ""),
                published_at=j.get("published_at", ""), company_slug=args.slug,
                source_type=j.get("source_type", "newsapi_ai"), metadata=j.get("metadata", {}),
            ))
    if not matches:
        logger.error("no on-disk article matching %r for %s", args.match, args.slug)
        return 1
    art = matches[0]
    print(f"Promoting: {art.title[:80]}")
    print(f"  body chars: {len(art.content)}")

    t0 = time.monotonic()
    # n_critical=1, n_total=1 → the single article is forced through the
    # critical pipeline; build_company_deck does NOT clear the deck, so the rest
    # of the company's cards are untouched (this article's row is upserted).
    deck = build_company_deck(co, [art], n_critical=1, n_total=1)
    print(f"\n  critical_published={deck.critical_published} "
          f"light_published={deck.light_published} "
          f"approval_rejected={deck.approval_rejected} ({time.monotonic()-t0:.0f}s)")
    if deck.published_items:
        for it in deck.published_items:
            print(f"   -> tier={it.get('tier')} has_recs={it.get('has_recs')} | {it.get('title','')[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
