"""Phase 49.2 — purge market-roundup cards already published to decks.

ZERO LLM. The Phase 49.2 fetch-time guard (`_is_market_roundup`) stops future
roundups, but multi-stock roundups already promoted to CRITICAL with fabricated
ESG ledes/recs are live (the Adani "5 Adani stocks ..." + ICICI "Opening Bell
..." cards). This script scans every deck row, and for any whose article TITLE
is a market roundup it:
  * deletes the per-company company_article_view row (stops it showing on /now),
  * deletes the on-disk insight JSON (so a later reprocess/restore can't
    resurrect it).
The industry-shared article_pool row is left intact (harmless; the fetch guard
keeps it out of future decks).

Usage:
    python scripts/drop_roundups_from_decks.py            # dry-run (report only)
    python scripts/drop_roundups_from_decks.py --apply    # actually delete
    python scripts/drop_roundups_from_decks.py --apply --only adani-power
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)5s %(message)s")
logger = logging.getLogger("drop_roundups")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--only", help="restrict to a single company slug")
    args = ap.parse_args()

    from engine.db.connection import connect, is_postgres
    if not is_postgres():
        logger.error("Postgres only — refusing to run on a non-postgres backend.")
        return 2

    from engine.ingestion.news_fetcher import _is_market_roundup

    out_root = _ROOT / "data" / "outputs"
    dropped: dict[str, list[str]] = {}

    with connect() as c:
        where = "WHERE v.company_slug = ?" if args.only else ""
        params = (args.only,) if args.only else ()
        rows = c.execute(
            f"""
            SELECT v.company_slug AS slug, v.article_id AS aid, p.title AS title
            FROM company_article_view v
            JOIN article_pool p ON p.id = v.article_id
            {where}
            ORDER BY v.company_slug
            """,
            params,
        ).fetchall()

        for r in rows:
            slug, aid, title = r["slug"], r["aid"], (r["title"] or "")
            if not _is_market_roundup(title.lower()):
                continue
            dropped.setdefault(slug, []).append(f"{aid}  {title[:80]}")
            if args.apply:
                c.execute(
                    "DELETE FROM company_article_view WHERE company_slug = ? AND article_id = ?",
                    (slug, aid),
                )
                # remove on-disk insight(s) for this article so reprocess/restore
                # cannot resurrect the roundup card
                ins_dir = out_root / slug / "insights"
                if ins_dir.exists():
                    for f in ins_dir.glob(f"*{aid}*.json"):
                        try:
                            f.unlink()
                        except OSError as exc:
                            logger.warning("could not unlink %s: %s", f.name, exc)
        if args.apply:
            c.commit()

    print("\n" + "=" * 64)
    print(f"  MARKET-ROUNDUP CARDS {'DROPPED' if args.apply else '(dry-run — would drop)'}")
    print("=" * 64)
    total = 0
    for slug in sorted(dropped):
        print(f"\n  {slug}  ({len(dropped[slug])})")
        for line in dropped[slug]:
            print(f"      - {line}")
        total += len(dropped[slug])
    if not dropped:
        print("  none found — all decks clean")
    print(f"\n  total: {total}")
    if not args.apply and total:
        print("  (re-run with --apply to delete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
