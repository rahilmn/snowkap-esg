"""Phase 8 — Any-company onboarding engine.

Takes a minimal input (company name + optional ticker) and produces a
fully calibrated company entry ready for the 12-stage pipeline:

  - yfinance financials (revenue, opex, capex, debt/equity, WACC proxy)
  - Industry → SASB category mapping
  - Market cap tier (Large / Mid / Small)
  - 28 industry-tailored news queries (matches Phase 1 cluster structure)
  - Written atomically to config/companies.json

SLA target: < 5 minutes from "tatasteel" to a working company entry.

Caveat: this does NOT run the first pipeline pass for the new company —
that's a separate step (`python engine/main.py ingest --company <slug>`).
Keeping the two operations decoupled makes the onboarding idempotent and
cheap. The pipeline pass is the expensive bit and should be explicit.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.config import CONFIG_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Industry → SASB category map (our 7 companies + common Indian sectors)
# ---------------------------------------------------------------------------

_INDUSTRY_TO_SASB: dict[str, str] = {
    "Financials/Banking": "Commercial Banks",
    "Banks - Regional": "Commercial Banks",
    "Banks - Diversified": "Commercial Banks",
    "Asset Management": "Asset Management & Custody Activities",
    "Insurance - Life": "Insurance",
    "Insurance - Property & Casualty": "Insurance",
    "Power/Energy": "Electric Utilities & Power Generators",
    "Utilities - Regulated Electric": "Electric Utilities & Power Generators",
    "Utilities - Independent Power Producers": "Electric Utilities & Power Generators",
    "Renewable Energy": "Solar Technology & Project Developers",
    "Solar": "Solar Technology & Project Developers",
    "Oil & Gas Integrated": "Oil & Gas — Exploration & Production",
    "Oil & Gas Refining & Marketing": "Oil & Gas — Refining & Marketing",
    "Steel": "Iron & Steel Producers",
    "Metals & Mining": "Metals & Mining",
    "Auto Manufacturers": "Automobiles",
    "Auto Parts": "Auto Parts",
    "Chemicals": "Chemicals",
    "Pharmaceuticals": "Pharmaceuticals",
    "Information Technology": "Software & IT Services",
    "Consumer/Beverage": "Non-Alcoholic Beverages",
    "FMCG": "Processed Foods",
}


def _infer_our_industry(yf_industry: str, yf_sector: str) -> str:
    """Map yfinance industry/sector to our ontology's 7 canonical industries.

    Our canonical industries (from companies.json + ontology):
      - Financials/Banking, Asset Management, Power/Energy,
        Renewable Energy, Automotive, Information Technology,
        Oil & Gas, Steel, Consumer/Beverage, Pharmaceuticals
    """
    combined = f"{yf_sector or ''} {yf_industry or ''}".lower()
    if "bank" in combined:
        return "Financials/Banking"
    if "asset management" in combined or "capital market" in combined:
        return "Asset Management"
    if "insurance" in combined:
        return "Financials/Insurance"
    if "utilit" in combined and ("electric" in combined or "power" in combined):
        return "Power/Energy"
    if "renewable" in combined or "solar" in combined:
        return "Renewable Energy"
    if "oil" in combined or "gas" in combined:
        return "Oil & Gas"
    if "steel" in combined or "iron" in combined:
        return "Steel"
    if "automobile" in combined or "auto manuf" in combined:
        return "Automotive"
    if "chemical" in combined:
        return "Chemicals"
    if "pharma" in combined or "drug manuf" in combined:
        return "Pharmaceuticals"
    if "software" in combined or "information technology" in combined or "it service" in combined:
        return "Information Technology"
    if "beverage" in combined or "food" in combined:
        return "Consumer/Beverage"
    return yf_industry or yf_sector or "Other"


# ---------------------------------------------------------------------------
# Market-cap tiering (INR Cr)
# ---------------------------------------------------------------------------


def _infer_cap_tier(market_cap_raw_inr: float) -> str:
    """Follow SEBI's broad bands (simplified):
    Large Cap ≥ ₹20,000 Cr; Mid Cap ₹5,000-20,000 Cr; Small Cap < ₹5,000 Cr.
    """
    cr = market_cap_raw_inr / 1e7
    if cr >= 20_000:
        return "Large Cap"
    if cr >= 5_000:
        return "Mid Cap"
    return "Small Cap"


# ---------------------------------------------------------------------------
# Slug + queries
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
    return re.sub(r"-+", "-", s).strip("-")


# The 25 common query terms from Phase 1. Industry-specific suffixes are
# appended after. Every company gets the same 5 topic clusters: SEBI,
# compliance, labour, climate, land/waste.
_COMMON_QUERIES = [
    "{company} SEBI fine",
    "{company} SEBI penalty",
    "{company} SEBI show cause",
    "{company} SEBI enforcement",
    "{company} BRSR filing",
    "{company} CSRD",
    "{company} TCFD disclosure",
    "{company} climate stress test",
    "{company} climate disclosure",
    "{company} mandatory disclosure",
    "{company} forced labour",
    "{company} child labour",
    "{company} modern slavery",
    "{company} wage theft",
    "{company} factory audit",
    "{company} flood",
    "{company} drought",
    "{company} extreme weather",
    "{company} water stress",
    "{company} heatwave",
    "{company} land acquisition",
    "{company} biodiversity",
    "{company} hazardous waste",
    "{company} EPR compliance",
    "{company} ESG rating",
]

# Phase 17 — industry-specific calibration defaults for onboarded companies.
# Numbers mirror the hand-calibrated values for the 7 target companies in
# `config/companies.json`. They drive `primitive_engine.compute_cascade()`'s
# per-company β multiplier so an onboarded steel mill gets sensible "EP→OX"
# elasticity (40% energy share) instead of the generic 10% placeholder.
#
# Schema per row:
#   energy_share, labor_share, freight_intensity, water_intensity,
#   commodity_exposure (commodity → fraction of opex),
#   key_exposure (list of dominant risk types: regulatory / energy / climate / ...)
#
# When industry is unknown, fall through to `__fallback__` (conservative
# defaults that won't dominate the cascade in either direction).
_INDUSTRY_CALIBRATION_DEFAULTS: dict[str, dict] = {
    "Financials/Banking": {
        "energy_share": 0.01, "labor_share": 0.32,
        "freight_intensity": 0.00, "water_intensity": 0.00,
        "commodity_exposure": {},
        "key_exposure": ["regulatory", "credit", "reputation"],
    },
    "Asset Management": {
        "energy_share": 0.005, "labor_share": 0.60,
        "freight_intensity": 0.00, "water_intensity": 0.00,
        "commodity_exposure": {},
        "key_exposure": ["regulatory", "reputation"],
    },
    "Power/Energy": {
        "energy_share": 0.40, "labor_share": 0.08,
        "freight_intensity": 0.05, "water_intensity": 0.10,
        "commodity_exposure": {"coal": 0.35, "natural_gas": 0.10},
        "key_exposure": ["energy", "coal", "climate", "regulatory"],
    },
    "Renewable Energy": {
        "energy_share": 0.15, "labor_share": 0.20,
        "freight_intensity": 0.06, "water_intensity": 0.02,
        "commodity_exposure": {"polysilicon": 0.18, "copper": 0.08},
        "key_exposure": ["commodity", "supply_chain", "regulatory"],
    },
    "Steel": {
        "energy_share": 0.30, "labor_share": 0.15,
        "freight_intensity": 0.07, "water_intensity": 0.06,
        "commodity_exposure": {"coking_coal": 0.25, "iron_ore": 0.20},
        "key_exposure": ["energy", "commodity", "climate", "trade"],
    },
    "Automotive": {
        "energy_share": 0.08, "labor_share": 0.18,
        "freight_intensity": 0.06, "water_intensity": 0.02,
        "commodity_exposure": {"steel": 0.18, "aluminium": 0.05},
        "key_exposure": ["regulatory", "supply_chain", "labour"],
    },
    "Oil & Gas": {
        "energy_share": 0.30, "labor_share": 0.10,
        "freight_intensity": 0.08, "water_intensity": 0.05,
        "commodity_exposure": {"crude_oil": 0.50},
        "key_exposure": ["climate", "regulatory", "commodity"],
    },
    "Chemicals": {
        "energy_share": 0.20, "labor_share": 0.15,
        "freight_intensity": 0.05, "water_intensity": 0.08,
        "commodity_exposure": {"naphtha": 0.20},
        "key_exposure": ["regulatory", "climate", "safety"],
    },
    "Pharmaceuticals": {
        "energy_share": 0.07, "labor_share": 0.25,
        "freight_intensity": 0.04, "water_intensity": 0.04,
        "commodity_exposure": {"api_inputs": 0.15},
        "key_exposure": ["regulatory", "supply_chain"],
    },
    "Information Technology": {
        "energy_share": 0.04, "labor_share": 0.55,
        "freight_intensity": 0.01, "water_intensity": 0.01,
        "commodity_exposure": {},
        "key_exposure": ["cyber", "labour", "regulatory"],
    },
    "Consumer/Beverage": {
        "energy_share": 0.10, "labor_share": 0.18,
        "freight_intensity": 0.07, "water_intensity": 0.12,
        "commodity_exposure": {"agri_inputs": 0.18, "packaging": 0.05},
        "key_exposure": ["water", "supply_chain", "regulatory"],
    },
    "Other": {
        "energy_share": 0.10, "labor_share": 0.20,
        "freight_intensity": 0.04, "water_intensity": 0.02,
        "commodity_exposure": {},
        "key_exposure": ["regulatory"],
    },
    "__fallback__": {
        "energy_share": 0.10, "labor_share": 0.20,
        "freight_intensity": 0.04, "water_intensity": 0.02,
        "commodity_exposure": {},
        "key_exposure": ["regulatory"],
    },
}


# Industry-specific extras (3 per industry) — supplements the common 25
_INDUSTRY_SUFFIXES: dict[str, list[str]] = {
    "Financials/Banking": ["fossil fuel financing", "green loan", "NPA fraud"],
    "Asset Management": ["stewardship code", "fund governance", "divestment"],
    "Power/Energy": ["coal emissions", "pollution emission norms", "RBI action"],
    "Renewable Energy": ["Xinjiang polysilicon", "e-waste", "solar tariff"],
    "Steel": ["emission norms", "coking coal", "imported scrap"],
    "Automotive": ["recall", "emission norms", "supplier audit"],
    "Oil & Gas": ["oil spill", "refinery emissions", "carbon levy"],
    "Chemicals": ["hazardous emissions", "effluent discharge", "process safety"],
    "Pharmaceuticals": ["product recall", "FDA warning", "clinical trial"],
    "Information Technology": ["cybersecurity breach", "moonlighting", "H1B"],
    "Consumer/Beverage": ["water stress", "plastic packaging", "product recall"],
    "Other": ["regulatory enforcement", "labour rights", "environmental"],
}


def _build_queries(company_name: str, industry: str) -> list[str]:
    common = [q.format(company=company_name) for q in _COMMON_QUERIES]
    suffixes = _INDUSTRY_SUFFIXES.get(industry, _INDUSTRY_SUFFIXES["Other"])
    industry_specific = [f"{company_name} {s}" for s in suffixes]
    # Dedupe while preserving order
    seen = set()
    out = []
    for q in common + industry_specific:
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# Resolver — company name/ticker hint → yfinance ticker
# ---------------------------------------------------------------------------


def _domain_to_search_term(domain: str) -> str:
    """Phase 16 domain-only onboarding helper.

    Convert a bare domain like `tatachemicals.com` or `https://www.tatasteel.com/`
    to a search-friendly stem like `tatachemicals` / `tatasteel`. yfinance's
    Search endpoint is flexible enough to resolve most Indian listed companies
    from this stem alone — no need for a separate name resolver.
    """
    s = (domain or "").strip().lower()
    s = s.removeprefix("https://").removeprefix("http://")
    s = s.removeprefix("www.")
    s = s.split("/", 1)[0]   # drop any path
    s = s.split(":", 1)[0]   # drop any port
    # Take the leftmost label of the domain (skips .com/.in/.co.in/etc.)
    return s.split(".", 1)[0] or s


def _split_indian_compound_stem(stem: str) -> list[str]:
    """Yield search-term candidates derived from an Indian-corp domain stem.

    Example: "tatachemicals" → ["tatachemicals", "tata chemicals", "tata"].
    Yfinance's Search endpoint doesn't index camelCased / glued-together
    company-name domains, so we split on common Indian conglomerate
    prefixes before falling back to the bare stem.

    Live-fail (2026-04-29): `tatachemicals.com` onboarding returned
    "could not resolve ticker" because yf.Search("tatachemicals") was
    empty. Splitting → "tata chemicals" surfaces TATACHEM.NS correctly.
    """
    candidates = [stem]
    s = stem.lower()
    # Ordered longest → shortest so "tata" doesn't shadow "tatasteel"
    INDIAN_PREFIXES = (
        "adityabirla", "aditya", "mahindra", "reliance", "infosys",
        "kotak", "icici", "axis", "yesbank", "hdfc", "sbi",
        "bharti", "godrej", "dabur", "larsen", "wipro", "tcs",
        "bajaj", "hero", "tata", "jsw", "adani", "ola", "ola",
        "torrent", "vedanta", "lupin", "cipla", "asianpaints",
        "marico", "britannia", "ultratech", "nestle", "ambuja",
        "havells", "dlf", "godrej", "biocon",
    )
    for pre in INDIAN_PREFIXES:
        if s.startswith(pre) and len(s) > len(pre) + 1:
            tail = s[len(pre):]
            if tail.isalpha():  # only split if tail is clean letters
                candidates.append(f"{pre} {tail}")
                break  # first prefix wins
    # Always also try the bare prefix as a last resort (Tata, Adani, etc.
    # — finds the holding co's main listing if nothing else hits)
    for pre in INDIAN_PREFIXES:
        if s.startswith(pre):
            candidates.append(pre)
            break
    return candidates


def _resolve_from_domain(domain: str) -> tuple[str, dict] | None:
    """Resolve (ticker, info) given just a domain. Phase 16 — supports
    the new "enter domain → personalised app" onboarding flow.

    Strategy:
      1. Map the domain stem (e.g. "tatachemicals" from "tatachemicals.com")
         and search yfinance with it. Try multiple stem variants
         (`tatachemicals`, `tata chemicals`, `tata`) so multi-word Indian
         conglomerate domains resolve correctly.
      2. Prefer a hit whose website field matches our domain.
      3. Otherwise fall back to whichever yfinance hit looks plausible
         (NSE listing preferred, has revenue, name overlaps the stem).

    Returns None on failure (caller can ask for ticker_hint instead).
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — cannot resolve from domain")
        return None

    stem = _domain_to_search_term(domain)
    if not stem:
        return None

    norm_domain = (domain or "").lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/", 1)[0]

    # Phase 21 — try multiple search-term variants for Indian-conglomerate
    # domains. yf.Search("tatachemicals") returns 0 hits but
    # yf.Search("tata chemicals") returns TATACHEM.NS.
    quotes: list[dict] = []
    seen_symbols: set[str] = set()
    for variant in _split_indian_compound_stem(stem):
        try:
            search = yf.Search(variant)
            for q in (getattr(search, "quotes", []) or []):
                sym = q.get("symbol", "")
                if sym and sym not in seen_symbols:
                    seen_symbols.add(sym)
                    quotes.append(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance search failed for variant %r: %s", variant, exc)
    if not quotes:
        return None

    # Pass 1 — prefer a hit whose website field matches the input domain
    for q in quotes[:8]:
        sym = q.get("symbol", "")
        if not sym:
            continue
        try:
            tk = yf.Ticker(sym)
            info = tk.info or {}
        except Exception:
            continue
        website = (info.get("website") or "").lower()
        website = website.removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/", 1)[0]
        if website and norm_domain and (website == norm_domain or website.endswith(norm_domain) or norm_domain.endswith(website)):
            if info.get("totalRevenue"):
                return sym, info

    # Pass 2 — prefer NSE listing with revenue
    for q in quotes[:8]:
        sym = q.get("symbol", "")
        if sym.endswith(".NS"):
            try:
                tk = yf.Ticker(sym)
                info = tk.info or {}
                if info.get("totalRevenue"):
                    return sym, info
            except Exception:
                continue

    # Pass 3 — first hit with revenue
    for q in quotes[:8]:
        sym = q.get("symbol", "")
        if not sym:
            continue
        try:
            tk = yf.Ticker(sym)
            info = tk.info or {}
            if info.get("totalRevenue"):
                return sym, info
        except Exception:
            continue
    return None


def _resolve_yfinance_ticker(
    company_name: str,
    ticker_hint: str | None = None,
) -> tuple[str, dict] | None:
    """Return (ticker, info dict) on success, None on failure.

    Order of precedence: ticker_hint → yfinance search by name.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed — cannot resolve ticker")
        return None

    # Try ticker hint first
    if ticker_hint:
        try:
            tk = yf.Ticker(ticker_hint)
            info = tk.info
            name = info.get("longName") or info.get("shortName") or ""
            if info.get("totalRevenue") and name:
                return ticker_hint, info
        except Exception as exc:  # noqa: BLE001
            logger.debug("ticker_hint %s failed: %s", ticker_hint, exc)

    # yfinance search by name
    try:
        search = yf.Search(company_name)
        quotes = getattr(search, "quotes", [])
        for q in quotes[:5]:
            sym = q.get("symbol", "")
            # Prefer .NS (NSE) listings
            if sym.endswith(".NS"):
                tk = yf.Ticker(sym)
                info = tk.info
                if info.get("totalRevenue"):
                    return sym, info
        # Fall back to first hit with revenue
        for q in quotes[:5]:
            sym = q.get("symbol", "")
            if not sym:
                continue
            tk = yf.Ticker(sym)
            info = tk.info
            if info.get("totalRevenue"):
                return sym, info
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance search failed for %s: %s", company_name, exc)
    return None


# ---------------------------------------------------------------------------
# Main onboarding function
# ---------------------------------------------------------------------------


@dataclass
class OnboardResult:
    slug: str
    name: str
    ticker: str
    industry: str
    market_cap: str
    queries: int
    added_to_config: bool
    already_existed: bool


def onboard_company(
    company_name: str | None = None,
    ticker_hint: str | None = None,
    domain: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> OnboardResult | None:
    """Onboard a new company into the pipeline.

    Phase 16: at least one of `company_name`, `ticker_hint`, or `domain`
    must be provided. The fastest happy path is **domain-only** entry —
    enter `tatachemicals.com` and the resolver finds the ticker, name,
    industry, financials, and news queries in one shot. The previous
    name-required flow still works (back-compat).

    Steps:
      1. Resolve yfinance ticker + info (domain → name → ticker_hint cascade)
      2. Infer our-industry from yfinance sector/industry strings
      3. Infer market-cap tier from marketCap field
      4. Fetch financials via financial_fetcher (reuses Phase 2)
      5. Generate industry-tailored news queries
      6. Write company entry to config/companies.json (atomic)

    Returns None on resolution failure.
    """
    # 1. Resolve ticker + info FIRST so domain-only entry can derive the
    # canonical name from yfinance before we slugify anything. The cascade:
    #   (a) if ticker_hint given → trust it
    #   (b) elif company_name given → search yfinance by name
    #   (c) elif domain given → derive stem + search yfinance
    if not (company_name or ticker_hint or domain):
        logger.error("onboard_company: must provide at least one of name / ticker_hint / domain")
        return None

    resolved: tuple[str, dict] | None = None
    if ticker_hint:
        resolved = _resolve_yfinance_ticker(company_name or "", ticker_hint)
    if resolved is None and company_name:
        resolved = _resolve_yfinance_ticker(company_name, None)
    if resolved is None and domain:
        resolved = _resolve_from_domain(domain)
    if resolved is None:
        logger.error(
            "could not resolve yfinance ticker (name=%r, hint=%r, domain=%r)",
            company_name, ticker_hint, domain,
        )
        return None
    ticker, info = resolved
    resolved_name = (
        info.get("longName")
        or info.get("shortName")
        or company_name
        or _domain_to_search_term(domain or "").title()
    )

    # 2. Now compute the slug from the canonical resolved name (NOT from
    # whatever the user typed — domain-only entry shouldn't leave us with
    # a slug like "tatachemicals-com").
    slug = _slugify(resolved_name)

    # Check if company already exists
    path = CONFIG_DIR / "companies.json"
    existing = json.loads(path.read_text(encoding="utf-8"))

    found_existing = next((c for c in existing["companies"] if c["slug"] == slug), None)
    if found_existing and not force:
        logger.info("company %s already exists — set force=True to overwrite", slug)
        return OnboardResult(
            slug=slug, name=resolved_name,
            ticker=found_existing.get("yfinance_ticker", ""),
            industry=found_existing.get("industry", ""),
            market_cap=found_existing.get("market_cap", ""),
            queries=len(found_existing.get("news_queries", [])),
            added_to_config=False, already_existed=True,
        )

    # 2. Industry + cap tier
    industry = _infer_our_industry(info.get("industry", ""), info.get("sector", ""))
    cap_tier = _infer_cap_tier(info.get("marketCap", 0) or 0)

    # 3. SASB category
    sasb_category = _INDUSTRY_TO_SASB.get(industry, "Other / General")

    # 4. Fetch financials via Phase 2 module
    from engine.ingestion.financial_fetcher import fetch_yfinance_financials
    fin = fetch_yfinance_financials(ticker)
    # Phase 17 — industry-aware calibration defaults so the cascade engine
    # produces meaningful ₹ figures for any onboarded company, not just the
    # 7 hand-calibrated targets. Each row mirrors the energy / labor / freight
    # / water shares already used for the target companies in companies.json.
    industry_defaults = _INDUSTRY_CALIBRATION_DEFAULTS.get(
        industry, _INDUSTRY_CALIBRATION_DEFAULTS["__fallback__"]
    )
    calibration = {
        "revenue_cr": 0.0,
        "opex_cr": 0.0,
        "capex_cr": 0.0,
        "energy_share_of_opex": industry_defaults["energy_share"],
        "labor_share_of_opex": industry_defaults["labor_share"],
        "freight_intensity": industry_defaults["freight_intensity"],
        "water_intensity": industry_defaults["water_intensity"],
        "commodity_exposure": dict(industry_defaults.get("commodity_exposure", {})),
        "key_exposure": list(industry_defaults.get("key_exposure", [])),
        "debt_to_equity": 1.0,
        "cost_of_capital_pct": 12.0,
        "fy_year": "FY25",
        "_source": "onboarder_default",
    }
    if fin:
        calibration.update(fin.to_calibration_dict(calibration))

    # 5. News queries
    queries = _build_queries(resolved_name, industry)

    # 6. Domain + HQ heuristics
    hq_city = info.get("city") or "Mumbai"
    hq_country = info.get("country") or "India"
    hq_region = "Asia-Pacific" if hq_country == "India" else "Other"

    # 7. Build company entry
    entry = {
        "name": resolved_name,
        "slug": slug,
        "domain": domain or (info.get("website") or "").replace("https://", "").replace("http://", "").rstrip("/"),
        "industry": industry,
        "sasb_category": sasb_category,
        "market_cap": cap_tier,
        "listing_exchange": "NSE" if ticker.endswith(".NS") else "BSE",
        "headquarter_city": hq_city,
        "headquarter_country": hq_country,
        "headquarter_region": hq_region,
        "news_queries": queries,
        "primitive_calibration": calibration,
        "yfinance_ticker": ticker,
        "eodhd_ticker": ticker.replace(".NS", ".NSE") if ticker.endswith(".NS") else ticker,
    }

    if dry_run:
        logger.info("dry-run — company entry NOT written. Preview:")
        logger.info(json.dumps(entry, indent=2)[:600])
        return OnboardResult(
            slug=slug, name=resolved_name, ticker=ticker,
            industry=industry, market_cap=cap_tier,
            queries=len(queries), added_to_config=False, already_existed=False,
        )

    # 8. Write atomically
    if found_existing:
        existing["companies"] = [c for c in existing["companies"] if c["slug"] != slug]
    existing["companies"].append(entry)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    tmp_path.replace(path)
    logger.info("onboarded %s (%s) — %d queries, industry=%s, cap=%s",
                resolved_name, ticker, len(queries), industry, cap_tier)

    # Clear load_companies lru_cache so the new entry is visible immediately
    try:
        from engine.config import load_companies
        load_companies.cache_clear()
    except Exception:  # noqa: BLE001
        pass

    return OnboardResult(
        slug=slug, name=resolved_name, ticker=ticker,
        industry=industry, market_cap=cap_tier,
        queries=len(queries), added_to_config=True, already_existed=False,
    )
