"""CLI for the autoresearcher.

Usage:
    python scripts/run_autoresearcher.py --tier system --budget 50 --seed 42
    python scripts/run_autoresearcher.py --tier system --budget 10 --keep-threshold 0.01
    python scripts/run_autoresearcher.py --build-wiki-pages   # refresh wiki/system/autoresearcher/

Tier-1 and Tier-2 are stubs that return a clear message.

Output (Tier 0):
  - data/autoresearcher/system/experiments.jsonl (append-only)
  - data/audit/decision_log.jsonl entries (autoresearcher_experiment_*)
  - data/audit/advisor_queue.jsonl entries (one per keep)
  - wiki/system/autoresearcher/*.md (when --build-wiki-pages is passed)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _print_summary(label: str, value) -> None:
    print(f"  {label:<22} {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=["system", "tenant", "user"], default="system")
    parser.add_argument("--tenant", type=str, default=None,
                        help="tenant slug (required when --tier tenant)")
    parser.add_argument("--user", type=str, default=None,
                        help="user id (required when --tier user)")
    parser.add_argument("--budget", type=int, default=20,
                        help="max number of experiments")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--keep-threshold", type=float, default=0.02,
                        help="minimum metric Δ to accept (default 0.02)")
    parser.add_argument("--min-age-days", type=int, default=0,
                        help="exclude articles published more recently than N days (default 0)")
    parser.add_argument("--build-wiki-pages", action="store_true",
                        help="refresh wiki/system/autoresearcher/*.md from the ledger")
    args = parser.parse_args()

    if args.build_wiki_pages:
        from engine.wiki.autoresearcher_pages import build_autoresearcher_pages
        result = build_autoresearcher_pages()
        print("Wiki pages built:")
        _print_summary("pages_written", result.pages_written)
        _print_summary("experiments_indexed", result.experiments_indexed)
        return 0

    if args.tier == "tenant":
        if not args.tenant:
            print("--tenant <slug> is required when --tier tenant")
            return 2
        print(f"Autoresearcher Tier-1 run — tenant={args.tenant} budget={args.budget} "
              f"seed={args.seed} keep_threshold={args.keep_threshold}")
        from engine.autoresearcher.tier1.runner import run_tier1
        result = run_tier1(
            tenant_slug=args.tenant,
            budget=args.budget,
            seed=args.seed,
            keep_threshold=args.keep_threshold,
            min_age_days=args.min_age_days,
        )
        print("\nResult:")
        for k, v in result.summary().items():
            _print_summary(k, v)
        return 0

    if args.tier == "user":
        if not args.user:
            print("--user <id> is required when --tier user")
            return 2
        print(f"Autoresearcher Tier-2 run — user={args.user} budget={args.budget} "
              f"seed={args.seed} keep_threshold={args.keep_threshold}")
        from engine.autoresearcher.tier2.runner import run_tier2
        result = run_tier2(
            user_id=args.user,
            budget=args.budget,
            seed=args.seed,
            keep_threshold=args.keep_threshold,
        )
        print("\nResult:")
        for k, v in result.summary().items():
            _print_summary(k, v)
        return 0

    print(f"Autoresearcher Tier-0 run — budget={args.budget} seed={args.seed} "
          f"keep_threshold={args.keep_threshold}")

    from engine.autoresearcher.tier0.runner import run_tier0
    result = run_tier0(
        budget=args.budget,
        seed=args.seed,
        keep_threshold=args.keep_threshold,
        min_age_days=args.min_age_days,
    )
    print("\nResult:")
    summary = result.summary()
    for k, v in summary.items():
        _print_summary(k, v)

    # Auto-build wiki pages after a run
    try:
        from engine.wiki.autoresearcher_pages import build_autoresearcher_pages
        wiki_result = build_autoresearcher_pages()
        _print_summary("wiki_pages_written", wiki_result.pages_written)
    except Exception as exc:  # noqa: BLE001
        print(f"  (wiki page build skipped: {exc})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
