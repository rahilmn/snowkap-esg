#!/usr/bin/env python
"""Phase 25 Bootstrap — one-command stand-up of the full Phase 25 stack.

Replaces the 4 manual post-deploy steps with a single invocation:

    python scripts/phase25_bootstrap.py \
        --csv ../hubspot-crm-exports-all-deals-2026-05-01.csv

Sequence:

  1. **Onboard** — parse the HubSpot CSV, enqueue the 17 customer
     onboarding jobs through ``engine.jobs.onboard_queue``.
  2. **Drain queue** — start an in-process worker (or detect an
     existing one) and wait until every onboarding job reaches
     ``ready`` or ``failed``. Default timeout 60 min.
  3. **Overnight batch** — call
     ``engine.scheduler.run_overnight_batch_job`` directly (no cron
     wait). Fetches articles + selects top-3 + runs full pipeline per
     newly-onboarded tenant. Default ~25 min wall-clock for 17 tenants.
  4. **Morning digest** — compose + send via Resend to
     ``$SNOWKAP_DIGEST_RECIPIENT`` (default ``sales@snowkap.co.in``).
     Skipped automatically when ``SNOWKAP_MORNING_DIGEST_ENABLED=0``.
  5. **Health report** — print the audit-log summary so the operator
     can confirm the stack is alive.

Idempotent: re-running on an already-bootstrapped instance skips
already-onboarded slugs (per ``--skip-existing``). Use
``--skip-onboard`` to re-run just the overnight batch + digest on an
already-onboarded instance.

Common usage:

    # First-time deploy (full bootstrap)
    python scripts/phase25_bootstrap.py --csv hubspot-export.csv --commit

    # Re-run overnight + digest on already-onboarded instance
    python scripts/phase25_bootstrap.py --skip-onboard --commit

    # Dry-run: preview what would happen, no writes
    python scripts/phase25_bootstrap.py --csv hubspot-export.csv

    # CI-friendly: short timeouts, exit non-zero on any failure
    python scripts/phase25_bootstrap.py --csv hubspot-export.csv \\
        --commit --onboard-timeout 1800 --batch-timeout 600 --strict

Exit codes:
  * 0 — every step succeeded
  * 1 — onboarding queue failed (CSV invalid, queue write error)
  * 2 — onboarding partial (some slugs failed; batch + digest skipped
        when ``--strict`` is set, otherwise continues)
  * 3 — overnight batch had errors
  * 4 — digest send failed
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


logger = logging.getLogger("phase25.bootstrap")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 25 — one-command bootstrap for the customer roster",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--csv",
        help="Path to HubSpot CSV (required unless --skip-onboard)",
    )
    p.add_argument(
        "--commit", action="store_true",
        help="Actually run (default: dry-run, prints planned actions)",
    )
    p.add_argument(
        "--skip-onboard", action="store_true",
        help="Skip the CSV onboarding step (use when already onboarded)",
    )
    p.add_argument(
        "--skip-overnight-batch", action="store_true",
        help="Skip the overnight batch step (just send digest)",
    )
    p.add_argument(
        "--skip-digest", action="store_true",
        help="Skip the morning digest step (just onboard + batch)",
    )
    p.add_argument(
        "--no-skip-existing",
        dest="skip_existing", action="store_false",
        default=True,
        help="Re-onboard slugs already in companies.json",
    )
    p.add_argument(
        "--onboard-timeout", type=int, default=3600,
        help="Max seconds to wait for the onboarding queue to drain (default: 1h)",
    )
    p.add_argument(
        "--batch-timeout", type=int, default=10800,
        help="Max seconds to wait for the overnight batch (default: 3h)",
    )
    p.add_argument(
        "--workers", type=int, default=4,
        help="Parallel workers for the overnight batch (default: 4)",
    )
    p.add_argument(
        "--fetch-per-tenant", type=int, default=20,
        help="Articles to fetch per tenant during overnight batch (default: 20)",
    )
    p.add_argument(
        "--select-per-tenant", type=int, default=3,
        help="Top-N articles to run full pipeline on per tenant (default: 3)",
    )
    p.add_argument(
        "--digest-recipient", default=None,
        help="Override SNOWKAP_DIGEST_RECIPIENT (default: sales@snowkap.co.in)",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Stop on the first non-zero step (default: continue past partial failures)",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


# ---------------------------------------------------------------------------
# Step 1 — onboard from CSV
# ---------------------------------------------------------------------------


def step_onboard(args: argparse.Namespace) -> tuple[int, list[str]]:
    """Returns (exit_code, list_of_newly_onboarded_slugs)."""
    print()
    print("STEP 1 / 4 — Onboard customers from HubSpot CSV")
    print("-" * 70)

    if args.skip_onboard:
        print("--skip-onboard set, skipping CSV onboarding")
        # Surface the existing batch tenants so step 2 has something to process
        from engine.scheduler import _discover_batch_tenant_slugs
        return 0, _discover_batch_tenant_slugs()

    if not args.csv:
        print("ERROR: --csv is required unless --skip-onboard is set")
        return 1, []

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        return 1, []

    from engine.ingestion.csv_batch_onboarder import parse_csv, summarise_roster
    try:
        roster = parse_csv(csv_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("CSV parse failed: %s", exc)
        return 1, []

    summary = summarise_roster(roster)
    print(f"Eligible customers: {summary['total']} "
          f"({summary['won']} Won, {summary['negotiation']} Negotiation)")

    # Skip-existing check
    existing_slugs: set[str] = set()
    if args.skip_existing:
        try:
            from engine.config import load_companies
            existing_slugs = {c.slug for c in load_companies()}
        except Exception:  # noqa: BLE001
            pass

    if not args.commit:
        new_slugs = [r.slug for r in roster if r.slug not in existing_slugs]
        skip_n = sum(1 for r in roster if r.slug in existing_slugs)
        print(f"DRY-RUN: would enqueue {len(new_slugs)} new tenants "
              f"(skipping {skip_n} already-onboarded). Pass --commit to proceed.")
        return 0, new_slugs

    # Real enqueue path
    from engine.ingestion.ticker_disambiguator import disambiguate
    from engine.jobs.onboard_queue import enqueue as _enqueue

    enqueued_slugs: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for r in roster:
        if r.slug in existing_slugs:
            skipped.append(r.slug)
            continue
        _, candidates = disambiguate(r.company_name)
        ticker_hint = next(
            (c.ticker for c in candidates
             if c.ticker and not c.ticker.startswith("PRIVATE:") and c.ticker != "UNKNOWN"),
            None,
        )
        try:
            job_id = _enqueue(
                slug=r.slug, name=r.company_name,
                ticker_hint=ticker_hint, domain=None, item_limit=10,
            )
            enqueued_slugs.append(r.slug)
            logger.info("enqueued slug=%s job_id=%d ticker=%s",
                        r.slug, job_id, ticker_hint or "<none>")
        except Exception as exc:  # noqa: BLE001
            failed.append((r.slug, str(exc)))
            logger.error("enqueue failed for slug=%s: %s", r.slug, exc)

    print(f"Enqueued: {len(enqueued_slugs)} | Skipped: {len(skipped)} | Failed: {len(failed)}")
    if failed:
        for slug, err in failed:
            print(f"  FAIL {slug}: {err[:120]}")

    if not enqueued_slugs:
        print("Nothing to drain — queue is empty.")
        # Return existing slugs so step 3 still has tenants to process
        return (0 if not failed else 2), [r.slug for r in roster if r.slug not in {f[0] for f in failed}]

    # Drain wait
    print()
    print(f"Waiting for {len(enqueued_slugs)} onboarding job(s) to drain "
          f"(timeout {args.onboard_timeout}s)...")
    drain_rc = _wait_for_onboarding(enqueued_slugs, timeout_seconds=args.onboard_timeout)
    if drain_rc != 0:
        return (drain_rc, enqueued_slugs)
    if failed:
        return 2, enqueued_slugs
    return 0, enqueued_slugs


def _wait_for_onboarding(slugs: list[str], *, timeout_seconds: int) -> int:
    from engine.models import onboarding_status as _os
    start = time.time()
    last_print: dict[str, str] = {}
    while True:
        elapsed = time.time() - start
        if elapsed >= timeout_seconds:
            print(f"  TIMEOUT after {elapsed:.0f}s. Unfinished:")
            for slug in slugs:
                row = _os.get(slug)
                state = row.state if row else "(no row)"
                if state not in {"ready", "failed"}:
                    print(f"    - {slug}: {state}")
            return 2

        states = {slug: (_os.get(slug).state if _os.get(slug) else "pending")
                  for slug in slugs}

        for slug, state in states.items():
            if last_print.get(slug) != state:
                print(f"  [{elapsed:5.0f}s] {slug:30s} -> {state}")
                last_print[slug] = state

        if all(s in {"ready", "failed"} for s in states.values()):
            ready = sum(1 for s in states.values() if s == "ready")
            failed_n = sum(1 for s in states.values() if s == "failed")
            print()
            print(f"  Done. {ready} ready, {failed_n} failed (elapsed {elapsed:.0f}s)")
            return 0 if failed_n == 0 else 2

        time.sleep(10)


# ---------------------------------------------------------------------------
# Step 2 — overnight batch
# ---------------------------------------------------------------------------


def step_overnight_batch(args: argparse.Namespace, tenant_slugs: list[str]) -> int:
    print()
    print("STEP 2 / 4 — Overnight batch (fetch + select-top-3 + full pipeline)")
    print("-" * 70)

    if args.skip_overnight_batch:
        print("--skip-overnight-batch set, skipping")
        return 0

    if not tenant_slugs:
        print("No tenants to process — skipping batch.")
        return 0

    if not args.commit:
        print(f"DRY-RUN: would process {len(tenant_slugs)} tenant(s) "
              f"(workers={args.workers}, fetch={args.fetch_per_tenant}, "
              f"select={args.select_per_tenant}). Pass --commit to proceed.")
        return 0

    print(f"Processing {len(tenant_slugs)} tenant(s) with {args.workers} workers...")

    from engine.scheduler import run_overnight_batch_job
    counts = run_overnight_batch_job(
        fetch_per_tenant=args.fetch_per_tenant,
        select_per_tenant=args.select_per_tenant,
        workers=args.workers,
        tenant_slugs=tenant_slugs,
    )
    print()
    print(f"  Tenants attempted:   {counts['tenants_attempted']}")
    print(f"  Tenants succeeded:   {counts['tenants_succeeded']}")
    print(f"  Articles fetched:    {counts['articles_fetched']}")
    print(f"  Articles selected:   {counts['articles_selected']}")
    print(f"  Passed CFO preflight: {counts['articles_passed_preflight']}")
    print(f"  Errors:              {counts['errors']}")
    return 3 if counts.get("errors", 0) > 0 else 0


# ---------------------------------------------------------------------------
# Step 3 — morning digest
# ---------------------------------------------------------------------------


def step_morning_digest(args: argparse.Namespace) -> int:
    print()
    print("STEP 3 / 4 — Morning digest send")
    print("-" * 70)

    if args.skip_digest:
        print("--skip-digest set, skipping")
        return 0

    if not args.commit:
        print("DRY-RUN: would compose + send digest. Pass --commit to send.")
        return 0

    from engine.output.digest_email import send_morning_digest
    result = send_morning_digest(recipient=args.digest_recipient)
    status = result.get("status", "unknown")
    print(f"  Recipient:           {result.get('recipient', '?')}")
    print(f"  Subject:             {result.get('subject', '?')}")
    print(f"  Articles included:   {result.get('articles_included', 0)}")
    print(f"  Customers w/ alerts: {result.get('customers_with_alerts', 0)}")
    print(f"  Send status:         {status}")
    if result.get("error"):
        print(f"  Error:               {result['error']}")

    # 'preview' is OK in dev (no Resend key); 'sent'/'ok' is OK in prod;
    # anything else is a failure.
    if status in ("sent", "ok", "preview", "disabled"):
        return 0
    return 4


# ---------------------------------------------------------------------------
# Step 4 — health report
# ---------------------------------------------------------------------------


def step_health_report() -> int:
    print()
    print("STEP 4 / 4 — Health report")
    print("-" * 70)
    try:
        from engine import audit
        runs = list(audit.read_overnight_runs())
        print(f"  Overnight run log: {len(runs)} entr{'ies' if len(runs) != 1 else 'y'}")
        if runs:
            latest = runs[-1]
            print(f"    Latest: {latest.get('completed_at')[:19]} — "
                  f"{latest.get('tenants_succeeded', 0)}/{latest.get('tenants_attempted', 0)} tenants, "
                  f"{latest.get('articles_passed_preflight', 0)} preflight-pass articles")
    except Exception as exc:  # noqa: BLE001
        print(f"  audit.read_overnight_runs failed: {exc}")

    try:
        from engine.config import load_companies
        companies = load_companies()
        print(f"  Companies registered: {len(companies)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  load_companies failed: {exc}")

    try:
        from engine.ontology.tenant_resolver import list_tenants, DEFAULT_TENANT
        tenants = [t for t in list_tenants() if t != DEFAULT_TENANT]
        print(f"  Phase 25 tenants:     {len(tenants)} ({', '.join(tenants[:5])}{'...' if len(tenants) > 5 else ''})")
    except Exception as exc:  # noqa: BLE001
        print(f"  list_tenants failed: {exc}")

    return 0


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    print()
    print("=" * 70)
    print("Phase 25 Bootstrap — one-command stack stand-up")
    print("=" * 70)
    if not args.commit:
        print("DRY-RUN MODE — pass --commit to actually run")

    # Step 1
    rc1, slugs = step_onboard(args)
    if rc1 != 0:
        print(f"\nStep 1 returned exit code {rc1}")
        if args.strict:
            print("--strict set, aborting.")
            return rc1
        if rc1 == 1:
            return rc1  # fatal — no point continuing

    # Step 2
    rc2 = step_overnight_batch(args, slugs)
    if rc2 != 0:
        print(f"\nStep 2 returned exit code {rc2}")
        if args.strict:
            return rc2

    # Step 3
    rc3 = step_morning_digest(args)
    if rc3 != 0:
        print(f"\nStep 3 returned exit code {rc3}")
        if args.strict:
            return rc3

    # Step 4 — always runs
    step_health_report()

    print()
    print("=" * 70)
    final_rc = max(rc1, rc2, rc3) if any([rc1, rc2, rc3]) else 0
    if final_rc == 0:
        print("Phase 25 bootstrap complete. ✓")
    else:
        print(f"Phase 25 bootstrap finished with exit code {final_rc} "
              f"(some steps had issues — see logs above)")
    print("=" * 70)
    return final_rc


if __name__ == "__main__":
    raise SystemExit(main())
