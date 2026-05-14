"""Phase 1.3 backfill — for every tenant, ensure painpoints.ttl exists
(running W3 if not) + compute the embedding cache used by the Phase 1
Criticality scorer's painpoint_match component.

Two-pass:
  1. For each tenant in companies.json, if painpoints.ttl is missing, run
     ``discover_painpoints()`` + ``write_painpoints_ttl()`` (~$0.05/tenant).
  2. For each tenant, if painpoint_embeddings.json is stale or missing,
     compute embeddings with text-embedding-3-small (~$0.0001/tenant).

Total cost ceiling at 27 tenants:
  - W3 discovery: 27 × $0.05 = ~$1.35 (only if no painpoints.ttl yet)
  - Embeddings:  27 × $0.0001 = ~$0.003
  - Real total at first run: ~$1.35

Idempotent — re-runs are no-ops once everything is fresh.

Usage:
    python scripts/backfill_painpoint_embeddings.py             # all tenants
    python scripts/backfill_painpoint_embeddings.py --slug X    # one tenant
    python scripts/backfill_painpoint_embeddings.py --skip-discovery  # embeddings only
    python scripts/backfill_painpoint_embeddings.py --dry-run   # show what would change
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so OPENAI_API_KEY is available when invoked from any cwd
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("backfill")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slug", help="Limit to one tenant slug (default: all in companies.json).",
    )
    parser.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip W3 painpoint discovery — only refresh embeddings for tenants that already have painpoints.ttl.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would change without spending LLM dollars.",
    )
    args = parser.parse_args()

    # Resolve target tenants from companies.json (the canonical truth)
    cfg_path = ROOT / "config" / "companies.json"
    if not cfg_path.exists():
        print(f"FATAL: missing {cfg_path}", file=sys.stderr)
        return 1
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    all_companies = cfg.get("companies") or []
    if args.slug:
        targets = [c for c in all_companies if c.get("slug") == args.slug]
    else:
        targets = all_companies

    print(f"=== Phase 1.3 painpoint backfill [{('DRY RUN' if args.dry_run else 'LIVE')}] ===")
    print(f"Target tenants: {len(targets)}")
    print()

    from engine.ingestion.painpoint_writer import (
        needs_refresh as painpoints_need_refresh,
        tenant_painpoints_path,
        write_painpoints_ttl,
    )
    from engine.analysis.painpoint_embeddings import (
        embed_painpoints_for_tenant,
        needs_refresh as embeddings_need_refresh,
        tenant_embeddings_path,
    )

    # Pre-flight summary
    print(f"{'slug':35s}  {'pp.ttl':>10s}  {'emb.json':>10s}  {'action':>30s}")
    print("-" * 95)
    plan: list[tuple[str, dict]] = []
    for c in targets:
        slug = c.get("slug")
        if not slug:
            continue
        pp_exists = tenant_painpoints_path(slug).exists()
        emb_exists = tenant_embeddings_path(slug).exists()
        emb_needs = embeddings_need_refresh(slug)
        actions: list[str] = []
        if not pp_exists and not args.skip_discovery:
            actions.append("W3-discover")
        if not pp_exists or emb_needs:
            actions.append("embed")
        action_str = "+".join(actions) or "skip-fresh"
        print(f"{slug:35s}  {('yes' if pp_exists else 'no'):>10s}  "
              f"{('yes' if emb_exists else 'no'):>10s}  {action_str:>30s}")
        plan.append((slug, {
            "discover": ("W3-discover" in actions),
            "embed": ("embed" in actions),
            "company": c,
        }))
    print()

    if args.dry_run:
        print("DRY RUN — no LLM calls made.")
        return 0

    # Live execution
    discovered = 0
    embedded = 0
    skipped = 0
    failed = 0

    for slug, work in plan:
        if not (work["discover"] or work["embed"]):
            skipped += 1
            continue

        c = work["company"]

        # Pass 1: W3 discovery (only when missing)
        if work["discover"]:
            try:
                from engine.ingestion.painpoint_discoverer import discover_painpoints
                logger.info(
                    "[%s] discovering painpoints (industry=%s, region=%s)",
                    slug, c.get("industry"), c.get("framework_region"),
                )
                report = discover_painpoints(
                    domain=c.get("domain") or "",
                    company_name=c.get("name") or slug,
                    industry=c.get("industry") or "Other",
                    sasb_category=c.get("sasb_category") or "Other / General",
                    region=c.get("framework_region") or "GLOBAL",
                )
                write_painpoints_ttl(
                    tenant_id=slug,
                    report=report,
                    domain=c.get("domain") or "",
                    company_name=c.get("name") or slug,
                    industry=c.get("industry") or "Other",
                    region=c.get("framework_region") or "GLOBAL",
                )
                discovered += 1
                logger.info("[%s] W3 wrote %d painpoints", slug, len(report.painpoints))
            except Exception as exc:
                logger.exception("[%s] W3 discovery failed: %s", slug, exc)
                failed += 1
                continue

        # Pass 2: embeddings
        if work["embed"]:
            try:
                n = embed_painpoints_for_tenant(slug, force=False)
                if n > 0:
                    embedded += 1
                    logger.info("[%s] embedded %d painpoints", slug, n)
                else:
                    logger.info(
                        "[%s] embed_painpoints_for_tenant returned 0 (no painpoints or fresh cache)",
                        slug,
                    )
            except Exception as exc:
                logger.exception("[%s] embedding failed: %s", slug, exc)
                failed += 1
                continue

    print()
    print("=== Backfill complete ===")
    print(f"  W3-discovered: {discovered}")
    print(f"  Embedded:      {embedded}")
    print(f"  Skipped:       {skipped} (already fresh)")
    print(f"  Failed:        {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
