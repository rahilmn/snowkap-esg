"""W5 — Wipe + regenerate all news on the new W4 per-role pipeline.

For every active tenant (config/companies.json + tenant_registry):

  1. DELETE rows in Supabase article_index for that slug
  2. DELETE rows in Supabase article_analysis_status for that slug
  3. rm -rf data/outputs/{slug}/insights/*
  4. rm -rf data/outputs/{slug}/perspectives/*
  5. clear data/inputs/news/{slug}/.processed_hashes.json (forces re-fetch)
  6. enqueue an onboarding job in mode='regen' so the worker
     re-fetches news + runs the new W4-flavoured pipeline

DRY-RUN BY DEFAULT — pass `--confirm yes` to actually destroy data.

Usage:
    python scripts/wipe_for_role_rebuild.py                  # dry-run (shows counts)
    python scripts/wipe_for_role_rebuild.py --confirm yes    # destroy + regen
    python scripts/wipe_for_role_rebuild.py --slug icici-bank --confirm yes  # one tenant only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so SUPABASE_DATABASE_URL is available when invoked from any cwd.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

OUTPUTS = ROOT / "data" / "outputs"
INPUTS_NEWS = ROOT / "data" / "inputs" / "news"


def _list_target_slugs() -> list[str]:
    """Return every tenant slug — config targets + onboarded registry."""
    out: list[str] = []
    seen: set[str] = set()

    # Config-based targets
    cfg_path = ROOT / "config" / "companies.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        for c in (cfg.get("companies") or []):
            slug = c.get("slug")
            if slug and slug not in seen:
                out.append(slug)
                seen.add(slug)
    except Exception as exc:
        print(f"warning: could not read companies.json: {exc}")

    # Onboarded registry (Supabase or SQLite depending on backend)
    try:
        from engine.index import tenant_registry
        for row in tenant_registry.list_tenants():
            slug = row.get("slug")
            if slug and slug not in seen:
                out.append(slug)
                seen.add(slug)
    except Exception as exc:
        print(f"warning: could not read tenant_registry: {exc}")

    return out


def _wipe_supabase(slug: str, *, dry_run: bool) -> dict[str, int]:
    """DELETE from article_index + article_analysis_status for this slug.
    Returns {"article_index": N, "article_analysis_status": M}.
    """
    counts = {"article_index": 0, "article_analysis_status": 0}
    try:
        from engine.db.connection import connect
    except Exception as exc:
        print(f"  [{slug}] could not import engine.db.connection: {exc}")
        return counts

    # The connection wrapper translates `?` -> `%s` for Postgres internally,
    # so we always use `?` placeholders (works for both SQLite + Postgres).
    try:
        with connect() as c:
            cur = c.cursor()
            cur.execute("SELECT COUNT(*) AS n FROM article_index WHERE company_slug = ?", (slug,))
            row = cur.fetchone()
            counts["article_index"] = int(row["n"] if hasattr(row, "__getitem__") else row[0])

            cur.execute(
                """SELECT COUNT(*) AS n FROM article_analysis_status
                   WHERE article_id IN (SELECT id FROM article_index WHERE company_slug = ?)""",
                (slug,),
            )
            row = cur.fetchone()
            counts["article_analysis_status"] = int(row["n"] if hasattr(row, "__getitem__") else row[0])

            if not dry_run:
                cur.execute(
                    """DELETE FROM article_analysis_status
                       WHERE article_id IN (SELECT id FROM article_index WHERE company_slug = ?)""",
                    (slug,),
                )
                cur.execute("DELETE FROM article_index WHERE company_slug = ?", (slug,))
    except Exception as exc:
        print(f"  [{slug}] db wipe failed: {type(exc).__name__}: {exc}")
    return counts


def _wipe_disk(slug: str, *, dry_run: bool) -> dict[str, int]:
    """Remove on-disk insights + perspectives + processed_hashes file for this slug.
    Returns {"insights_files": N, "perspectives_files": M, "processed_hashes_cleared": 0|1}.
    """
    counts = {"insights_files": 0, "perspectives_files": 0, "processed_hashes_cleared": 0}

    insights_dir = OUTPUTS / slug / "insights"
    perspectives_dir = OUTPUTS / slug / "perspectives"
    processed_hashes = INPUTS_NEWS / slug / ".processed_hashes.json"

    if insights_dir.exists():
        files = list(insights_dir.glob("*.json"))
        counts["insights_files"] = len(files)
        if not dry_run:
            for f in files:
                try:
                    f.unlink()
                except Exception as exc:
                    print(f"  [{slug}] could not delete {f.name}: {exc}")

    if perspectives_dir.exists():
        files = list(perspectives_dir.glob("*.json"))
        counts["perspectives_files"] = len(files)
        if not dry_run:
            for f in files:
                try:
                    f.unlink()
                except Exception as exc:
                    print(f"  [{slug}] could not delete {f.name}: {exc}")

    if processed_hashes.exists():
        counts["processed_hashes_cleared"] = 1
        if not dry_run:
            try:
                processed_hashes.unlink()
            except Exception as exc:
                print(f"  [{slug}] could not delete processed_hashes: {exc}")

    return counts


def _enqueue_regen(slug: str, *, dry_run: bool) -> bool:
    """Queue an onboarding job in mode='regen' so the worker re-fetches +
    re-runs the pipeline. Returns True if enqueued."""
    if dry_run:
        return False
    try:
        from engine.jobs import onboard_queue
        from engine.config import get_company

        try:
            company = get_company(slug)
            domain = company.domain or ""
            name = company.name
        except Exception:
            domain = ""
            name = slug

        onboard_queue.enqueue(
            slug=slug,
            name=name,
            ticker_hint=None,
            domain=domain,
            item_limit=10,
        )
        return True
    except Exception as exc:
        print(f"  [{slug}] could not enqueue regen: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm",
        choices=["no", "yes"],
        default="no",
        help="'yes' actually wipes; 'no' is a dry run (default).",
    )
    parser.add_argument(
        "--slug",
        help="Limit to one tenant slug (default: all tenants).",
    )
    parser.add_argument(
        "--skip-regen",
        action="store_true",
        help="Wipe only — do NOT enqueue regen jobs (useful for tests).",
    )
    args = parser.parse_args()

    dry_run = args.confirm != "yes"
    mode_label = "DRY RUN" if dry_run else "LIVE WIPE"

    if args.slug:
        slugs = [args.slug]
    else:
        slugs = _list_target_slugs()

    print(f"=== W5 wipe-for-role-rebuild [{mode_label}] ===")
    print(f"Tenants in scope: {len(slugs)}")
    print(f"DB backend: {os.environ.get('SNOWKAP_DB_BACKEND', 'sqlite')}")
    print()

    totals = {"article_index": 0, "article_analysis_status": 0,
              "insights_files": 0, "perspectives_files": 0,
              "processed_hashes_cleared": 0, "regen_queued": 0}

    print(f"{'slug':36s}  {'idx':>5s} {'status':>6s}  {'insights':>8s} {'persp':>6s}  {'queued':>6s}")
    print("-" * 80)
    for slug in slugs:
        db_counts = _wipe_supabase(slug, dry_run=dry_run)
        disk_counts = _wipe_disk(slug, dry_run=dry_run)
        queued = (
            _enqueue_regen(slug, dry_run=dry_run) if not args.skip_regen else False
        )

        for k, v in db_counts.items():
            totals[k] += v
        for k, v in disk_counts.items():
            totals[k] += v
        if queued:
            totals["regen_queued"] += 1

        print(
            f"{slug:36s}  "
            f"{db_counts['article_index']:>5d} "
            f"{db_counts['article_analysis_status']:>6d}  "
            f"{disk_counts['insights_files']:>8d} "
            f"{disk_counts['perspectives_files']:>6d}  "
            f"{'yes' if queued else '-':>6s}"
        )

    print("-" * 80)
    print(
        f"{'TOTALS':36s}  "
        f"{totals['article_index']:>5d} "
        f"{totals['article_analysis_status']:>6d}  "
        f"{totals['insights_files']:>8d} "
        f"{totals['perspectives_files']:>6d}  "
        f"{totals['regen_queued']:>6d}"
    )
    print()

    if dry_run:
        print("DRY RUN — no data was modified. Re-run with --confirm yes to apply.")
    else:
        print("LIVE WIPE complete. Worker will drain the regen queue over the next few hours.")
        print("Monitor with: tail -f data/logs/onboarding_worker.log  (or check Supabase onboarding_status)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
