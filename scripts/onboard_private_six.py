#!/usr/bin/env python
"""Phase 25 hotfix — straight-line onboarder for the 6 private customers.

Replaces the W6 queue + worker + bootstrap stack for a one-time
operation. Six rows, hardcoded curated metadata (since yfinance has
no entries for these), sequential loop, ~2 minutes total wall-clock.

Each onboarding does FOUR things per customer:

  1. Append a fully-populated row to ``config/companies.json`` (industry,
     calibration, news_queries, region — same shape the yfinance path
     produces, just with ``_source: manual_phase25_hotfix``).
  2. Write a per-tenant Layer 3 extension at
     ``data/ontology/tenants/<slug>/extension.ttl`` using the W6
     ``industry_materiality_defaults`` builder — so the tenant graph
     gets industry-specific MaterialityWeight overrides on the next
     graph load.
  3. Bust the ontology graph cache for that tenant so the next API
     request picks up the new extension.
  4. Print readiness confirmation.

Article ingestion is deliberately NOT triggered here — that's the
overnight scheduler's job (or a separate `python engine/main.py ingest
--company <slug>` if you want to backfill immediately).

Idempotent: re-running silently skips slugs already in companies.json.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("phase25.onboard_private")


# ---------------------------------------------------------------------------
# Curated metadata — six private customers, manually researched
# ---------------------------------------------------------------------------
#
# Revenue figures are best-effort estimates from public filings / industry
# reports. Off-by-30% is fine — the cascade engine + materiality weights
# care about order-of-magnitude, not Cr-precision.

PRIVATE_SIX: list[dict] = [
    {
        "name": "Catasynth Speciality Chemicals",
        "slug": "catasynth",
        "domain": "catasynth.com",
        "industry": "Chemicals",
        "sasb_category": "Chemicals",
        "market_cap": "Small Cap",
        "headquarter_city": "Mumbai",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 250.0,
        "opex_cr": 200.0,
        "capex_cr": 25.0,
        "_research_note": "Private specialty-chem manufacturer, catalysts + petrochem intermediates. Estimate based on industry peer benchmarks.",
    },
    {
        "name": "Sud-Chemie India",
        "slug": "sud-chemie-india",
        "domain": "clariant.com",
        "industry": "Chemicals",
        "sasb_category": "Chemicals",
        "market_cap": "Mid Cap",
        "headquarter_city": "Vadodara",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 600.0,
        "opex_cr": 480.0,
        "capex_cr": 60.0,
        "_research_note": "Clariant subsidiary, India catalysts business. Parent revenue ~CHF 4.4B; Indian sub estimated ₹600 Cr.",
    },
    {
        "name": "MAHLE India",
        "slug": "mahle-gmbh",
        "domain": "mahle.com",
        "industry": "Auto Parts",
        "sasb_category": "Auto Parts",
        "market_cap": "Mid Cap",
        "headquarter_city": "Pune",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 1500.0,
        "opex_cr": 1200.0,
        "capex_cr": 180.0,
        "_research_note": "MAHLE GmbH (German parent, EUR 12.6B). Indian subsidiary (Anand-MAHLE filter systems + thermal management) estimated ₹1,500 Cr.",
    },
    {
        "name": "DRT-Anthea Aromatics",
        "slug": "drt-anthea",
        "domain": "antheaaromatics.com",
        "industry": "Chemicals",
        "sasb_category": "Chemicals",
        "market_cap": "Small Cap",
        "headquarter_city": "Mumbai",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 400.0,
        "opex_cr": 320.0,
        "capex_cr": 40.0,
        "_research_note": "Joint venture between Anthea Aromatics (India) + DRT (France). Aromatic chemicals + flavors/fragrances inputs.",
    },
    {
        "name": "Tata AutoComp Systems",
        "slug": "tata-autocomp-systems",
        "domain": "tataautocomp.com",
        "industry": "Auto Parts",
        "sasb_category": "Auto Parts",
        "market_cap": "Large Cap",
        "headquarter_city": "Pune",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 15000.0,
        "opex_cr": 12000.0,
        "capex_cr": 1500.0,
        "_research_note": "Tata Group private subsidiary. Revenue FY24 ~₹15,000 Cr (per Tata Group annual report); auto components + EV systems supplier to TaMo + global OEMs.",
    },
    {
        "name": "MAHAPREIT (Maharashtra Cooperative)",
        "slug": "mahapreit",
        "domain": "mahapreit.in",
        "industry": "Other",
        "sasb_category": "Other / General",
        "market_cap": "Small Cap",
        "headquarter_city": "Mumbai",
        "headquarter_country": "India",
        "headquarter_region": "Asia-Pacific",
        "framework_region": "INDIA",
        "revenue_cr": 800.0,
        "opex_cr": 700.0,
        "capex_cr": 50.0,
        "_research_note": "Maharashtra State Co-op Tribal Federation. Government cooperative, agri/MFP procurement + tribal welfare ops.",
    },
]


# ---------------------------------------------------------------------------
# Calibration assembly — mirrors company_onboarder._INDUSTRY_CALIBRATION_DEFAULTS
# ---------------------------------------------------------------------------


def _calibration_for(spec: dict) -> dict:
    """Build the primitive_calibration dict using industry defaults +
    customer-specific revenue/opex/capex overrides."""
    from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS

    industry = spec["industry"]
    defaults = _INDUSTRY_CALIBRATION_DEFAULTS.get(
        industry, _INDUSTRY_CALIBRATION_DEFAULTS["__fallback__"]
    )
    return {
        "revenue_cr": float(spec["revenue_cr"]),
        "opex_cr": float(spec["opex_cr"]),
        "capex_cr": float(spec["capex_cr"]),
        "energy_share_of_opex": defaults["energy_share"],
        "labor_share_of_opex": defaults["labor_share"],
        "freight_intensity": defaults["freight_intensity"],
        "water_intensity": defaults["water_intensity"],
        "commodity_exposure": defaults.get("commodity_exposure", {}),
        "debt_to_equity": 0.0,
        "cost_of_capital_pct": 11.0,  # private-co generic estimate
        "fy_year": "FY25",
        "_source": "manual_phase25_hotfix",
        "_fetched_at": datetime.now(timezone.utc).isoformat(),
        "_ticker": "PRIVATE",
        "ebitda_cr": 0.0,
        "operating_margin_pct": 0.0,
        "gross_margin_pct": 0.0,
        "market_cap_cr": 0.0,
        "_key_exposure": defaults.get("key_exposure", []),
    }


def _news_queries_for(spec: dict) -> list[str]:
    """Build the news_queries list. Reuses the W6 region/industry-aware
    builder from company_onboarder so private customers get the same
    SEBI/BRSR/CSRD search coverage as listed ones."""
    from engine.ingestion.company_onboarder import _build_queries

    return _build_queries(
        company_name=spec["name"],
        industry=spec["industry"],
        region=spec["framework_region"],
    )


# ---------------------------------------------------------------------------
# Step 1 — append to companies.json
# ---------------------------------------------------------------------------


def _append_to_companies_json(spec: dict) -> bool:
    """Returns True if a new entry was added, False if slug already existed."""
    config_path = Path("config/companies.json")
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    companies = data.setdefault("companies", [])
    if any(c.get("slug") == spec["slug"] for c in companies):
        return False  # idempotent

    entry = {
        "name": spec["name"],
        "slug": spec["slug"],
        "domain": spec["domain"],
        "industry": spec["industry"],
        "sasb_category": spec["sasb_category"],
        "market_cap": spec["market_cap"],
        "listing_exchange": "PRIVATE",
        "headquarter_city": spec["headquarter_city"],
        "headquarter_country": spec["headquarter_country"],
        "headquarter_region": spec["headquarter_region"],
        "framework_region": spec["framework_region"],
        "news_queries": _news_queries_for(spec),
        "primitive_calibration": _calibration_for(spec),
        "yfinance_ticker": None,
        "eodhd_ticker": None,
        "onboarded_via": "manual_phase25_hotfix",
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
        "_research_note": spec["_research_note"],
    }
    companies.append(entry)
    # Write back atomically (write-temp + rename)
    tmp = config_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(config_path)
    return True


# ---------------------------------------------------------------------------
# Step 2 — write per-tenant Layer 3 extension TTL
# ---------------------------------------------------------------------------


def _write_tenant_extension(spec: dict) -> Path:
    from engine.ingestion.industry_materiality_defaults import build_extension_ttl
    from engine.ontology.tenant_resolver import ensure_tenant_dir, tenant_extension_path

    tenant_dir = ensure_tenant_dir(spec["slug"])
    ttl_content = build_extension_ttl(
        tenant_slug=spec["slug"],
        industry=spec["industry"],
    )
    ext_path = tenant_extension_path(spec["slug"])
    ext_path.write_text(ttl_content, encoding="utf-8")
    # Also drop a metadata.json so /discover-tenant-config can pick up
    # the seeded values without re-prompting
    metadata_path = tenant_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "name": spec["name"],
            "slug": spec["slug"],
            "industry": spec["industry"],
            "framework_region": spec["framework_region"],
            "onboarded_via": "manual_phase25_hotfix",
            "onboarded_at": datetime.now(timezone.utc).isoformat(),
            "research_note": spec["_research_note"],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return ext_path


# ---------------------------------------------------------------------------
# Step 3 — bust ontology graph cache for the tenant
# ---------------------------------------------------------------------------


def _reset_tenant_graph(slug: str) -> None:
    try:
        from engine.ontology.graph import reset_graph
        reset_graph(tenant_id=slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph reset failed for %s: %s", slug, exc)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass

    print()
    print("=" * 70)
    print("Phase 25 hotfix — onboarding 6 private customers")
    print("=" * 70)

    added = 0
    skipped = 0
    failed = 0

    for spec in PRIVATE_SIX:
        slug = spec["slug"]
        try:
            print()
            print(f"  → {spec['name']} ({slug})")
            print(f"    industry={spec['industry']}, revenue=₹{spec['revenue_cr']:,.0f} Cr, region={spec['framework_region']}")

            # Step 1: companies.json
            was_added = _append_to_companies_json(spec)
            if not was_added:
                print("    SKIP: slug already in companies.json")
                skipped += 1
                continue

            # Step 2: tenant Layer 3 extension
            ext_path = _write_tenant_extension(spec)
            print(f"    wrote extension.ttl → {ext_path}")

            # Step 3: bust the per-tenant graph cache
            _reset_tenant_graph(slug)

            print(f"    ✓ ready (next API request for tenant={slug} will load fresh)")
            added += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"    ✗ FAILED: {exc}")
            logger.exception("onboarding failed for %s", slug)

    print()
    print("=" * 70)
    print(f"Result: {added} onboarded · {skipped} already existed · {failed} failed")
    print("=" * 70)
    print()
    print("Next steps:")
    print("  • To fetch + analyze articles for one of these:")
    print("      python engine/main.py ingest --company <slug>")
    print("  • To view the new tenant in the dashboard, restart the API:")
    print("      (it will pick up companies.json on next request)")
    print()
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
