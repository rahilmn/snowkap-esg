"""CLI: onboard a new company into the Snowkap pipeline (Phase 8).

Usage:
    python scripts/onboard_company.py --name "Tata Steel" --ticker TATASTEEL.NS
    python scripts/onboard_company.py --name "Tata Power"   # resolves ticker via yfinance search
    python scripts/onboard_company.py --name "Tata Power" --dry-run  # preview only

After onboarding succeeds, run:
    python engine/main.py ingest --company tata-power
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.ingestion.company_onboarder import onboard_company


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Onboard a new company")
    parser.add_argument("--name", required=True, help="Company name (e.g. 'Tata Steel')")
    parser.add_argument("--ticker", help="Optional yfinance ticker hint (e.g. TATASTEEL.NS)")
    parser.add_argument("--domain", help="Company website domain (optional)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing entry")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to config")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # UTF-8 stdout
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    result = onboard_company(
        company_name=args.name,
        ticker_hint=args.ticker,
        domain=args.domain,
        force=args.force,
        dry_run=args.dry_run,
    )

    if result is None:
        print(f"ERROR: could not onboard '{args.name}'. Try providing --ticker.", file=sys.stderr)
        return 1

    print(f"\nOnboarded: {result.name}")
    print(f"  slug:     {result.slug}")
    print(f"  ticker:   {result.ticker}")
    print(f"  industry: {result.industry}")
    print(f"  cap tier: {result.market_cap}")
    print(f"  queries:  {result.queries}")
    if result.already_existed and not args.force:
        print("  (company already existed; use --force to overwrite)")
    elif result.added_to_config:
        print(f"\nRun next: python engine/main.py ingest --company {result.slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
