#!/usr/bin/env python
"""Phase 25 W6 (automated) — batch-onboard customers from a HubSpot CSV.

Server-side counterpart to the ``/settings/onboard/batch`` admin UI.
Bypasses the API + JWT auth so the operator can run this from cron or
a one-shot deploy script:

    python scripts/batch_onboard_from_csv.py \
        --csv ../hubspot-crm-exports-all-deals-2026-05-01.csv

Behaviour:
  1. Parse the CSV via ``engine.ingestion.csv_batch_onboarder.parse_csv``
     (filters to ``Active Status = "Active" AND Deal Stage IN ('Won',
     'Negotiation')``).
  2. Print the per-row roster + disambiguation flags.
  3. With ``--commit`` (or ``--yes``): enqueue each row through the
     existing ``engine.jobs.onboard_queue.enqueue`` so the existing
     ``scripts/onboarding_worker.py`` drains them.
  4. With ``--wait``: also block until every enqueued job completes,
     polling ``onboarding_status`` every 10s. Useful for the
     ``phase25_bootstrap.py`` orchestrator that needs to know when
     onboarding is done before triggering the overnight batch.
  5. ``--skip-existing`` (default true): rows whose slug already exists
     in ``config/companies.json`` are skipped to avoid clobbering the
     original 7 target companies.

Exit codes:
  * 0 — success (all rows enqueued + drained, or dry-run completed)
  * 1 — CSV parse error
  * 2 — partial failure (some jobs failed, some succeeded)
  * 3 — fatal (queue write failed)

Idempotent: re-running with ``--skip-existing`` is a no-op once tenants
are onboarded. Re-running with ``--no-skip-existing`` re-enqueues all
17 jobs (the worker is itself idempotent — same slug → same canonical
yfinance lookup → no duplicate writes).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


logger = logging.getLogger("phase25.batch_onboard")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 25 W6 — batch onboard customers from a HubSpot CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--csv",
        required=True,
        help="Path to the HubSpot deals CSV export (e.g. "
             "hubspot-crm-exports-all-deals-2026-05-01.csv)",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="Actually enqueue jobs (default: dry-run, just print roster)",
    )
    # Alias used by the bootstrap orchestrator
    p.add_argument(
        "--yes", "-y", dest="commit", action="store_true",
        help="Alias for --commit",
    )
    p.add_argument(
        "--no-skip-existing",
        dest="skip_existing", action="store_false",
        default=True,
        help="Re-enqueue rows whose slug already exists in companies.json "
             "(default: skip existing slugs)",
    )
    p.add_argument(
        "--wait",
        action="store_true",
        help="Block until every enqueued job completes (or fails). Polls "
             "onboarding_status every 10s. Default: enqueue + exit "
             "(worker drains in background).",
    )
    p.add_argument(
        "--wait-timeout-seconds",
        type=int, default=3600,
        help="Maximum wait time when --wait is set (default: 3600s = 1h). "
             "After timeout, prints unfinished slugs and exits with code 2.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Force UTF-8 so Süd-Chemie + ₹ symbols render in Windows consoles
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        return 1

    # 1. Parse + summarise
    from engine.ingestion.csv_batch_onboarder import parse_csv, summarise_roster
    try:
        roster = parse_csv(csv_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("CSV parse failed: %s", exc)
        return 1

    summary = summarise_roster(roster)
    print()
    print("=" * 70)
    print(f"Phase 25 W6 — batch onboard from {csv_path.name}")
    print("=" * 70)
    print(f"  Total eligible: {summary['total']}")
    print(f"  Won:            {summary['won']}")
    print(f"  Negotiation:    {summary['negotiation']}")
    print(f"  Countries:      {', '.join(summary['countries'])}")
    print()

    # 2. Run the disambiguator + print per-row status
    from engine.ingestion.ticker_disambiguator import disambiguate
    enriched_roster = []
    auto_resolvable = 0
    needs_review = 0
    for r in roster:
        review_needed, candidates = disambiguate(r.company_name)
        enriched_roster.append((r, review_needed, candidates))
        if review_needed:
            needs_review += 1
        else:
            auto_resolvable += 1

    print(f"Disambiguation: {auto_resolvable} auto-resolvable, "
          f"{needs_review} need review")
    print()
    print("Roster:")
    print(f"  {'#':>3} {'Stage':12s} {'Company':35s} {'Slug':30s} Disambig")
    print(f"  {'-' * 3} {'-' * 12} {'-' * 35} {'-' * 30} {'-' * 8}")
    for i, (r, review_needed, candidates) in enumerate(enriched_roster, 1):
        flag = f"REVIEW ({len(candidates)})" if review_needed else "auto"
        print(f"  {i:3d} {r.deal_stage:12s} {r.company_name[:34]:35s} "
              f"{r.slug[:29]:30s} {flag}")
    print()

    # 3. Skip-existing check
    existing_slugs: set[str] = set()
    if args.skip_existing:
        try:
            from engine.config import load_companies
            existing_slugs = {c.slug for c in load_companies()}
            already_in = [
                r.slug for r, _, _ in enriched_roster if r.slug in existing_slugs
            ]
            if already_in:
                print(f"Skip-existing ON ({len(already_in)} slug{'s' if len(already_in) != 1 else ''} "
                      f"already in companies.json):")
                for slug in already_in:
                    print(f"    - {slug}")
                print()
        except Exception as exc:  # noqa: BLE001
            logger.warning("load_companies failed (%s); proceeding without skip", exc)

    # 4. Dry-run vs commit
    if not args.commit:
        print("DRY-RUN — no jobs enqueued. Re-run with --commit to enqueue.")
        print()
        return 0

    # Commit path: enqueue each new row through onboard_queue
    from engine.jobs.onboard_queue import enqueue as _enqueue
    enqueued: list[tuple[str, int]] = []  # (slug, job_id)
    skipped_existing: list[str] = []
    failed: list[tuple[str, str]] = []  # (slug, error_message)

    print("Enqueueing jobs...")
    for r, _, candidates in enriched_roster:
        if r.slug in existing_slugs:
            skipped_existing.append(r.slug)
            continue
        # Pick the highest-confidence non-private ticker as hint; fall back to None
        ticker_hint = None
        for c in candidates:
            tk = c.ticker
            if tk and not tk.startswith("PRIVATE:") and tk != "UNKNOWN":
                ticker_hint = tk
                break
        try:
            job_id = _enqueue(
                slug=r.slug,
                name=r.company_name,
                ticker_hint=ticker_hint,
                domain=None,  # CSV doesn't carry domains
                item_limit=10,
            )
            enqueued.append((r.slug, job_id))
            logger.info("enqueued slug=%s job_id=%d ticker_hint=%s",
                        r.slug, job_id, ticker_hint or "<none>")
        except Exception as exc:  # noqa: BLE001
            failed.append((r.slug, str(exc)))
            logger.error("enqueue failed for slug=%s: %s", r.slug, exc)

    print()
    print(f"Enqueued: {len(enqueued)} jobs")
    print(f"Skipped:  {len(skipped_existing)} (already onboarded)")
    print(f"Failed:   {len(failed)} (queue write errors)")
    print()
    if failed:
        print("Failures:")
        for slug, err in failed:
            print(f"    - {slug}: {err[:120]}")
        print()

    if not enqueued and not failed:
        print("Nothing to do — all rows already onboarded.")
        return 0

    if failed and not enqueued:
        return 3  # fatal queue failure

    # 5. --wait: poll until all enqueued slugs finish
    if args.wait and enqueued:
        rc = _wait_for_completion(
            [slug for slug, _ in enqueued],
            timeout_seconds=args.wait_timeout_seconds,
        )
        if rc != 0:
            return rc

    if failed:
        return 2  # partial failure
    return 0


# ---------------------------------------------------------------------------
# Wait helper — polls onboarding_status until all slugs are ready/failed
# ---------------------------------------------------------------------------


def _wait_for_completion(slugs: list[str], *, timeout_seconds: int) -> int:
    """Poll onboarding_status until every slug is in a terminal state.

    Terminal states: 'ready' | 'failed'. Returns:
      * 0 — all slugs reached 'ready'
      * 2 — some slugs reached 'failed' OR timeout hit
    """
    from engine.models import onboarding_status as _os

    print(f"Waiting for {len(slugs)} onboarding job(s) to complete "
          f"(timeout {timeout_seconds}s)...")
    start = time.time()
    poll_interval = 10.0
    last_print: dict[str, str] = {}

    while True:
        elapsed = time.time() - start
        if elapsed >= timeout_seconds:
            print(f"\nTIMEOUT after {elapsed:.0f}s. Unfinished slugs:")
            for slug in slugs:
                row = _os.get(slug)
                state = row.state if row else "(no status row)"
                if state not in {"ready", "failed"}:
                    print(f"    - {slug}: state={state}")
            return 2

        all_terminal = True
        states: dict[str, str] = {}
        for slug in slugs:
            row = _os.get(slug)
            state = row.state if row else "pending"
            states[slug] = state
            if state not in {"ready", "failed"}:
                all_terminal = False

        # Print state changes
        for slug, state in states.items():
            if last_print.get(slug) != state:
                print(f"  [{elapsed:5.0f}s] {slug:30s} -> {state}")
                last_print[slug] = state

        if all_terminal:
            ready = sum(1 for s in states.values() if s == "ready")
            failed_n = sum(1 for s in states.values() if s == "failed")
            print()
            print(f"Done. {ready} ready, {failed_n} failed "
                  f"(total elapsed {elapsed:.0f}s)")
            return 0 if failed_n == 0 else 2

        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
