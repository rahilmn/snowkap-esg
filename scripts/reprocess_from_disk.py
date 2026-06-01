"""Phase 48 — re-process every company's EXISTING on-disk input articles
through the (now-fixed) deck builder. Spends ZERO NewsAPI.ai tokens (the
articles were already fetched); only OpenRouter for Stage 10-12 + lede +
approval. Used to apply the lede/truncation/grounding fixes to the decks
without re-fetching.

Usage:
    python scripts/reprocess_from_disk.py              # all companies with input files
    python scripts/reprocess_from_disk.py --only icici-bank
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("reprocess")


def _load_articles(slug: str):
    from engine.ingestion.news_fetcher import IngestedArticle
    arts = []
    d = _ROOT / "data/inputs/news" / slug
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
    from engine.db.connection import is_postgres, get_backend
    if not is_postgres():
        logger.error("Backend is '%s' — Postgres only.", get_backend())
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    args = ap.parse_args()

    from engine.config import get_company
    from engine.analysis.deck_builder import build_company_deck
    from engine.models import company_article_view, onboarding_status
    from engine.db.connection import connect

    news_root = _ROOT / "data/inputs/news"
    slugs = ([args.only] if args.only
             else sorted(p.name for p in news_root.iterdir() if p.is_dir()))

    results = []
    for slug in slugs:
        co = get_company(slug)
        if co is None:
            logger.warning("no company row for %s — skipping", slug); continue
        arts = _load_articles(slug)
        if not arts:
            results.append((slug, 0, 0, 0)); continue
        logger.warning("=== %s: re-processing %d on-disk articles ===", slug, len(arts))
        # Clear the existing deck rows so the rebuild is clean (no stale criticals).
        try:
            with connect() as c:
                c.execute("DELETE FROM company_article_view WHERE company_slug = ?", (slug,))
                c.commit()
        except Exception as exc:
            logger.warning("could not clear deck for %s: %s", slug, exc)
        t0 = time.monotonic()
        deck = build_company_deck(co, arts, n_critical=3, n_total=10)
        onboarding_status.mark_ready(
            slug, fetched=deck.fetched,
            analysed=deck.critical_published + deck.light_published,
            home_count=deck.critical_published, created_by_user="ci@snowkap.com",
        )
        results.append((slug, deck.critical_published, deck.light_published, deck.approval_rejected))
        logger.warning("  -> critical=%d light=%d rejected=%d (%.0fs)",
                       deck.critical_published, deck.light_published,
                       deck.approval_rejected, time.monotonic() - t0)

    print("\n" + "=" * 64)
    print(f"  {'slug':22} {'crit':>4} {'light':>5} {'rej':>4}")
    for slug, crit, light, rej in results:
        print(f"  {slug:22} {crit:>4} {light:>5} {rej:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
