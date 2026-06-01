"""Phase 48.H — re-onboard the 9 launch companies via NewsAPI.ai.

STRICTLY Postgres. For each domain:
  1. LLM resolver (Opus 4.6) → company profile + painpoints + KPIs + role
  2. Upsert companies row
  3. Register slug alias + tenant_registry (so logins resolve instantly)
  4. Fetch ESG news (ONE NewsAPI.ai call, last 30 days)
  5. build_company_deck → 3 critical (full + approval) + 7 light
  6. mark onboarding_status ready

Per-company try/except: one failure never aborts the batch.

Usage:
    python scripts/reonboard_nine.py                 # all 9
    python scripts/reonboard_nine.py --only mahle.com
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("urllib3", "httpx", "httpcore", "openai", "rdflib"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logger = logging.getLogger("reonboard")

# The 9 launch companies (domain order = display priority).
DOMAINS = [
    "waaree.com",
    "icicibank.com",
    "idfcfirstbank.com",
    "yesbank.in",
    "sbi.co.in",          # State Bank of India
    "mahle.com",
    "singularityamc.com",
    "adanipower.com",
    "jswenergy.com",
]


def _exchange_from_ticker(ticker: str) -> str:
    if not ticker:
        return "Private/Unknown"
    t = ticker.upper()
    sfx = {".NS": "NSE", ".BO": "BSE", ".L": "LSE", ".DE": "Xetra",
           ".PA": "Euronext Paris", ".F": "Frankfurt", ".T": "TSE",
           ".HK": "HKEX", ".SS": "SSE"}
    for s, e in sfx.items():
        if t.endswith(s):
            return e
    return "NASDAQ/NYSE" if "." not in t else "Unknown"


def _onboard_one(domain: str) -> dict:
    from engine.config import Company, invalidate_companies_cache
    from engine.ingestion.llm_company_resolver import resolve_company_from_domain
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.models import companies_store, onboarding_status
    from engine.analysis.deck_builder import build_company_deck

    t0 = time.monotonic()
    info = resolve_company_from_domain(domain)
    if info is None:
        return {"domain": domain, "status": "resolve_failed"}

    companies_store.upsert(
        slug=info.slug, name=info.canonical_name, domain=domain,
        industry=info.industry, market_cap_tier=info.market_cap_tier,
        yfinance_ticker=info.primary_ticker, framework_region=info.framework_region,
        primitive_calibration={
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
            "sasb_category": info.sasb_category,
        },
        created_by_user="ci@snowkap.com", status="active",
    )
    invalidate_companies_cache()

    # slug alias (input domain slug → canonical) + tenant_registry
    try:
        from engine.ingestion.company_onboarder import _slugify
        from engine.index import sqlite_index
        from engine.index import tenant_registry
        input_slug = _slugify(domain.split(".")[0])
        if input_slug and input_slug != info.slug:
            sqlite_index.register_alias(input_slug, info.slug)
        tenant_registry.register_tenant(domain=domain, name=info.canonical_name, source="onboarded")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] alias/tenant register failed: %s", info.slug, exc)

    company_obj = Company(
        name=info.canonical_name, slug=info.slug, domain=domain,
        industry=info.industry, sasb_category=info.sasb_category,
        market_cap=info.market_cap_tier,
        listing_exchange=_exchange_from_ticker(info.primary_ticker or ""),
        headquarter_city=info.headquarter_city or "Unknown",
        headquarter_country=info.headquarter_country or "",
        headquarter_region=info.framework_region,
        news_queries=[], primitive_calibration={
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
        },
        yfinance_ticker=info.primary_ticker, eodhd_ticker=None,
        framework_region=info.framework_region,
        sustainability_query=None, general_query=None,
    )

    fresh = fetch_for_company(company_obj, max_per_query=18)
    deck = build_company_deck(company_obj, fresh, n_critical=3, n_total=10)

    onboarding_status.mark_ready(
        info.slug,
        fetched=deck.fetched,
        analysed=deck.critical_published + deck.light_published,
        home_count=deck.critical_published,
        created_by_user="ci@snowkap.com",
    )

    return {
        "domain": domain,
        "slug": info.slug,
        "name": info.canonical_name,
        "industry": info.industry,
        "region": info.framework_region,
        "status": "ready" if (deck.critical_published + deck.light_published) > 0 else "no_articles",
        "fetched": deck.fetched,
        "critical": deck.critical_published,
        "light": deck.light_published,
        "approval_rejected": deck.approval_rejected,
        "elapsed": round(time.monotonic() - t0, 1),
    }


def main() -> int:
    from engine.db.connection import is_postgres, get_backend
    if not is_postgres():
        logger.error("Backend is '%s' — Postgres ONLY. Aborting.", get_backend())
        return 2

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="onboard a single domain instead of all 9")
    args = ap.parse_args()

    domains = [args.only] if args.only else DOMAINS
    results = []
    for d in domains:
        logger.info("=" * 60)
        logger.info("Onboarding %s ...", d)
        try:
            results.append(_onboard_one(d))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] onboard crashed: %s", d, exc)
            results.append({"domain": d, "status": f"crash: {type(exc).__name__}"})

    print("\n" + "=" * 78)
    print("  RE-ONBOARD REPORT")
    print("=" * 78)
    print(f"  {'domain':22} {'slug':18} {'status':12} {'crit':>4} {'light':>5} {'rej':>4} {'sec':>5}")
    for r in results:
        print(f"  {r.get('domain',''):22} {r.get('slug',''):18} "
              f"{r.get('status',''):12} {r.get('critical',0):>4} "
              f"{r.get('light',0):>5} {r.get('approval_rejected',0):>4} "
              f"{r.get('elapsed',0):>5}")
    ready = sum(1 for r in results if r.get("status") == "ready")
    print(f"\n  {ready}/{len(results)} companies ready")
    return 0 if ready == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
