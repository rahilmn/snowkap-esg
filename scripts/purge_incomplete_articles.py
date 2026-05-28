"""Phase 46.I — One-time cleanup of legacy article_pool + company_article_view rows
that lack the unified-analysis contract (criticality_summary populated).

Run once after deploying Phase 46 to clear out pre-rebuild junk from the
deck. After this, /api/now/feed will only show articles that satisfy
the contract by construction. Idempotent — re-running is a no-op.

Usage:
    python scripts/purge_incomplete_articles.py --dry-run
    python scripts/purge_incomplete_articles.py --apply

Strict Postgres-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _source_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_source_env()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report counts; don't delete")
    p.add_argument("--apply", action="store_true", help="Actually delete the rows")
    p.add_argument(
        "--company",
        default=None,
        help="Limit to one company slug (default: scan all)",
    )
    args = p.parse_args()

    if not args.dry_run and not args.apply:
        p.error("Pass either --dry-run or --apply")

    from engine.db.connection import connect, is_postgres
    if not is_postgres():
        print("ERROR: This script is Postgres-only.")
        return 2

    with connect() as conn:
        # 1. Identify rows in company_article_view where personalised_analysis
        #    lacks the criticality_summary contract.
        if args.company:
            view_sql = (
                "SELECT article_id, company_slug, personalised_analysis "
                "FROM company_article_view WHERE company_slug = ?"
            )
            params = (args.company,)
        else:
            view_sql = (
                "SELECT article_id, company_slug, personalised_analysis "
                "FROM company_article_view"
            )
            params = ()

        rows = conn.execute(view_sql, params).fetchall()
        print(f"Scanned {len(rows)} company_article_view rows...")

        to_delete: list[tuple[str, str]] = []  # (article_id, company_slug)
        for r in rows:
            article_id = r[0] if not hasattr(r, "keys") else r["article_id"]
            slug = r[1] if not hasattr(r, "keys") else r["company_slug"]
            pa_raw = r[2] if not hasattr(r, "keys") else r["personalised_analysis"]
            try:
                pa = pa_raw if isinstance(pa_raw, dict) else json.loads(pa_raw or "{}")
            except Exception:
                pa = {}
            why = (pa.get("why_it_matters") or {}) if isinstance(pa, dict) else {}
            if not why.get("criticality_summary"):
                to_delete.append((article_id, slug))

        print(f"Found {len(to_delete)} rows missing criticality_summary "
              f"({len(rows) - len(to_delete)} valid).")

        if not to_delete:
            print("Nothing to purge. Database is clean.")
            return 0

        # Show a sample
        print("\nSample (first 5):")
        for aid, slug in to_delete[:5]:
            print(f"  {slug} / {aid}")

        if args.dry_run:
            print("\n--dry-run: no changes made. Re-run with --apply to delete.")
            return 0

        # 2. Delete from company_article_view (preserves article_pool —
        #    other companies might have valid view rows for the same
        #    article).
        deleted = 0
        for aid, slug in to_delete:
            cur = conn.execute(
                "DELETE FROM company_article_view "
                "WHERE article_id = ? AND company_slug = ?",
                (aid, slug),
            )
            deleted += cur.rowcount or 0
        print(f"\nDeleted {deleted} row(s) from company_article_view.")

        # 3. Garbage-collect orphan article_pool rows — articles with NO
        #    remaining company_article_view rows.
        orphan_sql = (
            "DELETE FROM article_pool WHERE id NOT IN "
            "(SELECT DISTINCT article_id FROM company_article_view)"
        )
        cur = conn.execute(orphan_sql)
        orphans = cur.rowcount or 0
        print(f"Deleted {orphans} orphan article_pool row(s).")

        # 4. Garbage-collect article_index legacy rows for the same articles
        #    (article_index is the pre-Phase-46 read path; some surfaces
        #    still read it).
        try:
            for aid, slug in to_delete:
                conn.execute(
                    "DELETE FROM article_index "
                    "WHERE id = ? AND company_slug = ?",
                    (aid, slug),
                )
        except Exception as exc:
            # article_index might not exist in newer schemas — that's fine
            print(f"  (article_index cleanup skipped: {exc})")

        # Commit if Postgres needs an explicit commit (psycopg2 in
        # autocommit usually doesn't, but be safe).
        try:
            conn.commit()
        except Exception:
            pass

    print("\nCleanup complete. Re-run validate_phase46.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
