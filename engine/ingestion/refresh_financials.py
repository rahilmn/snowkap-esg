"""CLI script to refresh primitive_calibration values for all 7 companies.

Reads config/companies.json, calls the financial_fetcher fallback chain
(EODHD → yfinance → hardcoded) per company, writes the updated JSON back.

Usage:
    python -m engine.ingestion.refresh_financials         # refresh stale only
    python -m engine.ingestion.refresh_financials --force # refresh all
    python -m engine.ingestion.refresh_financials --company adani-power

After a run, `primitive_calibration._source` and `_fetched_at` reflect the
chosen data source and timestamp. Share ratios (energy_share_of_opex, etc.)
are preserved — only top-line figures are replaced.

Freshness: default max age 90 days. Cached values are reused inside that
window unless --force is passed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow `python -m engine.ingestion.refresh_financials` without PYTHONPATH.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.config import CONFIG_DIR  # noqa: E402
from engine.ingestion.financial_fetcher import enrich_calibration  # noqa: E402

logger = logging.getLogger(__name__)


def refresh_all(force: bool = False, only_slug: str | None = None) -> dict[str, str]:
    """Refresh primitive_calibration for all companies (or one by slug).

    Returns {slug: source} summary, where `source` is "yfinance", "eodhd",
    or "hardcoded" (the resolved data origin for that company).
    """
    path = CONFIG_DIR / "companies.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    summary: dict[str, str] = {}
    for company in data["companies"]:
        slug = company["slug"]
        if only_slug and slug != only_slug:
            continue

        base = company.get("primitive_calibration") or {}
        yf_ticker = company.get("yfinance_ticker")
        eo_ticker = company.get("eodhd_ticker")

        if not yf_ticker and not eo_ticker:
            # Private / unlisted — keep hardcoded and stamp
            logger.info("%s: no tickers (unlisted) — keeping hardcoded", slug)
            base.setdefault("_source", "hardcoded")
            base.setdefault("_fetched_at", "")
            company["primitive_calibration"] = base
            summary[slug] = "hardcoded"
            continue

        updated = enrich_calibration(
            base,
            yfinance_ticker=yf_ticker,
            eodhd_ticker=eo_ticker,
            force_refresh=force,
        )
        company["primitive_calibration"] = updated
        src = updated.get("_source", "unknown")
        summary[slug] = src
        rev = updated.get("revenue_cr", 0)
        logger.info("%s: source=%s revenue=₹%s Cr fy=%s",
                    slug, src, rev, updated.get("fy_year"))

    # Write back atomically via temp file
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    tmp_path.replace(path)
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Refresh company financial calibration")
    parser.add_argument("--force", action="store_true",
                        help="Ignore freshness cache; refetch every company")
    parser.add_argument("--company", help="Only refresh this slug")
    args = parser.parse_args(argv)

    summary = refresh_all(force=args.force, only_slug=args.company)

    print("\nRefresh summary:")
    for slug, src in summary.items():
        print(f"  {slug:20s} -> {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
