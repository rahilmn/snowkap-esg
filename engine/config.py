"""Configuration loader for the Snowkap ESG Intelligence Engine.

Loads settings, company profiles, and perspective configurations from
config/*.json files. All consumers should use the helpers defined here
instead of reading config files directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

# Load .env from the project root exactly once at import time.
try:
    from dotenv import load_dotenv

    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    # python-dotenv is optional; env vars may already be in os.environ
    pass


@dataclass(frozen=True)
class Company:
    name: str
    slug: str
    domain: str
    industry: str
    sasb_category: str
    market_cap: str
    listing_exchange: str
    headquarter_city: str
    headquarter_country: str
    headquarter_region: str
    news_queries: list[str]
    primitive_calibration: dict[str, Any] | None = None
    # Phase 2: tickers for live financial data refresh
    yfinance_ticker: str | None = None
    eodhd_ticker: str | None = None
    # Phase 23 reviewer fix — explicit framework jurisdiction. Decouples
    # "where the HQ is" (free-form `headquarter_region` label like
    # "Europe" / "United Kingdom") from "which framework regime applies"
    # (one of INDIA / EU / UK / US / APAC / GLOBAL). When None, the
    # framework matcher falls back to its country/region heuristic.
    framework_region: str | None = None
    # Phase 31 — LLM-crafted live-fetch queries persisted alongside the
    # company profile so /api/news/live can run them on every page load
    # without rebuilding from scratch.
    sustainability_query: str | None = None
    general_query: str | None = None

    @property
    def revenue_cr(self) -> float:
        """Annual revenue in ₹ Crores (from primitive_calibration)."""
        return float((self.primitive_calibration or {}).get("revenue_cr", 0))

    @property
    def opex_cr(self) -> float:
        """Annual opex in ₹ Crores."""
        return float((self.primitive_calibration or {}).get("opex_cr", 0))

    @property
    def capex_cr(self) -> float:
        """Annual capex in ₹ Crores."""
        return float((self.primitive_calibration or {}).get("capex_cr", 0))

    def get_cost_share(self, primitive_slug: str) -> float:
        """Return the company-specific cost share for a primitive (0.0-1.0).

        Maps primitive slugs to calibration fields:
        EP/EU → energy_share_of_opex, LC/WF → labor_share_of_opex,
        FR/LT → freight_intensity, WA → water_intensity.
        """
        cal = self.primitive_calibration or {}
        mapping = {
            "EP": "energy_share_of_opex",
            "EU": "energy_share_of_opex",
            "LC": "labor_share_of_opex",
            "WF": "labor_share_of_opex",
            "FR": "freight_intensity",
            "LT": "freight_intensity",
            "WA": "water_intensity",
        }
        field = mapping.get(primitive_slug.upper(), "")
        return float(cal.get(field, 0.1))  # default 10% if unknown

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Company":
        return cls(
            name=data["name"],
            slug=data["slug"],
            domain=data["domain"],
            industry=data["industry"],
            sasb_category=data["sasb_category"],
            market_cap=data["market_cap"],
            listing_exchange=data["listing_exchange"],
            headquarter_city=data["headquarter_city"],
            headquarter_country=data["headquarter_country"],
            headquarter_region=data["headquarter_region"],
            news_queries=list(data.get("news_queries", [])),
            primitive_calibration=data.get("primitive_calibration"),
            yfinance_ticker=data.get("yfinance_ticker"),
            eodhd_ticker=data.get("eodhd_ticker"),
            framework_region=data.get("framework_region"),
            sustainability_query=data.get("sustainability_query"),
            general_query=data.get("general_query"),
        )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file missing: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    """Load config/settings.json."""
    return _load_json(CONFIG_DIR / "settings.json")


_COMPANIES_CACHE: list[Company] | None = None
_COMPANIES_CACHE_EXPIRES_AT: float = 0.0
_COMPANIES_CACHE_TTL_SECONDS = 15.0


def load_companies() -> list[Company]:
    """Load all known companies — DB table FIRST (Phase 28),
    ``config/companies.json`` as fallback for the 7 baseline tenants
    and for tests that bypass the table.

    Merge rules:
      * DB row beats JSON row for the same slug (DB is the source of
        truth post-Phase-28; JSON is back-compat).
      * Slugs present only in JSON are added (preserves the 7 baseline).
      * Slugs present only in DB are added (post-onboarding tenants).

    Failures reading the DB collapse silently to JSON-only — the
    engine must boot even when the SQLite file is missing or the
    Postgres backend is unreachable. Callers should never observe an
    exception from this function.

    Phase 36 fix — switched from ``@lru_cache`` (per-process, only invalidated
    by the worker process that called ``mark_ready``) to a TTL cache with
    15-second expiry. The API process now sees new tenants within 15s of the
    onboarding worker completing — without needing cross-process cache-bust
    plumbing. Cost: ~10ms Supabase round-trip every 15s, negligible.
    Callers that need an immediate refresh can still call
    ``invalidate_companies_cache()`` (drops the TTL).
    """
    import time as _time
    global _COMPANIES_CACHE, _COMPANIES_CACHE_EXPIRES_AT
    now = _time.monotonic()
    if _COMPANIES_CACHE is not None and now < _COMPANIES_CACHE_EXPIRES_AT:
        return _COMPANIES_CACHE
    _COMPANIES_CACHE = _load_companies_uncached()
    _COMPANIES_CACHE_EXPIRES_AT = now + _COMPANIES_CACHE_TTL_SECONDS
    return _COMPANIES_CACHE


def _load_companies_uncached() -> list[Company]:
    # Step 1 — baseline from JSON (always available)
    json_companies: list[Company] = []
    try:
        data = _load_json(CONFIG_DIR / "companies.json")
        json_companies = [Company.from_dict(c) for c in data["companies"]]
    except FileNotFoundError:
        pass

    # Step 2 — overlay from the DB
    try:
        from engine.models.companies_store import list_all
        db_rows = list_all(status="active")
    except Exception:  # noqa: BLE001 — DB optional, do not crash boot
        db_rows = []

    if not db_rows:
        return json_companies

    by_slug: dict[str, Company] = {c.slug: c for c in json_companies}
    for row in db_rows:
        by_slug[row.slug] = _company_from_db_row(row, fallback=by_slug.get(row.slug))

    # Phase 50.1 — the DB 'held' status is AUTHORITATIVE: a company explicitly
    # held in the DB is dropped from the active roster even if it is a
    # companies.json baseline tenant. This lets us soft-launch (park MAHLE /
    # Singularity AMC until they have real ESG coverage) without deleting their
    # rows — flip status back to 'active' to relaunch. Without this, a held
    # json-baseline tenant would leak back into load_companies via Step 1.
    try:
        from engine.models.companies_store import list_all as _list_all
        held = {r.slug for r in _list_all(status="held")}
        for _slug in held:
            by_slug.pop(_slug, None)
    except Exception:  # noqa: BLE001 — never break boot on the held overlay
        pass

    return list(by_slug.values())


def _company_from_db_row(row: Any, *, fallback: Company | None) -> Company:
    """Build a ``Company`` from a ``CompanyRecord``. JSON ``fallback``
    (when present) supplies fields the DB table doesn't store: news_queries,
    sasb_category, listing_exchange, headquarter_city/country/region.
    Phase-28 onboarded tenants get sensible defaults; Phase 29's ESG-pool
    ingestion makes ``news_queries`` optional (pool ignores it)."""
    fb = fallback
    return Company(
        name=row.name,
        slug=row.slug,
        domain=row.domain or (fb.domain if fb else ""),
        industry=row.industry or (fb.industry if fb else "Unknown"),
        sasb_category=(fb.sasb_category if fb else "Unknown"),
        market_cap=row.market_cap_tier or (fb.market_cap if fb else "Unknown"),
        listing_exchange=(fb.listing_exchange if fb else ""),
        headquarter_city=(fb.headquarter_city if fb else ""),
        headquarter_country=(fb.headquarter_country if fb else ""),
        headquarter_region=(fb.headquarter_region if fb else "GLOBAL"),
        news_queries=(fb.news_queries if fb else []),
        primitive_calibration=row.primitive_calibration() or (fb.primitive_calibration if fb else None),
        yfinance_ticker=row.yfinance_ticker or (fb.yfinance_ticker if fb else None),
        eodhd_ticker=row.eodhd_ticker or (fb.eodhd_ticker if fb else None),
        framework_region=row.framework_region or (fb.framework_region if fb else None),
        sustainability_query=(getattr(row, "sustainability_query", None)
                              or (fb.sustainability_query if fb else None)),
        general_query=(getattr(row, "general_query", None)
                       or (fb.general_query if fb else None)),
    )


def invalidate_companies_cache() -> None:
    """Drop the cached company list. Call after onboarding a new tenant
    so the next ``load_companies()`` re-reads the DB.

    Phase 36 — under the new TTL cache (15s), this just resets the expiry
    so the very next call goes to the DB. Cross-process workers that
    can't reach the API process's memory still get the new tenant in <15s
    via natural TTL expiry, but explicit invalidation drops the wait.
    """
    global _COMPANIES_CACHE, _COMPANIES_CACHE_EXPIRES_AT
    _COMPANIES_CACHE = None
    _COMPANIES_CACHE_EXPIRES_AT = 0.0


@lru_cache(maxsize=1)
def load_perspectives() -> dict[str, Any]:
    """Load config/perspectives.json (CFO, CEO, ESG Analyst lens configs)."""
    return _load_json(CONFIG_DIR / "perspectives.json")["perspectives"]


def get_company(slug: str) -> Company:
    """Return the company with the given slug, or raise KeyError."""
    for company in load_companies():
        if company.slug == slug:
            return company
    raise KeyError(f"Unknown company slug: {slug}")


def get_openai_api_key() -> str:
    """Read the OpenAI API key from the environment."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return key


def get_newsapi_key() -> str | None:
    """Read the NewsAPI.org key (optional — Google News RSS works without it)."""
    return os.environ.get("NEWSAPI_KEY") or None


def get_eodhd_key() -> str | None:
    """Read the EODHD API key (Phase 2). Returns None if not set or empty."""
    return os.environ.get("EODHD_API_KEY") or None


def get_data_path(*parts: str) -> Path:
    """Resolve a path inside the data/ directory."""
    return DATA_DIR.joinpath(*parts)


def get_output_dir(company_slug: str) -> Path:
    """Return data/outputs/<company_slug>/."""
    return get_data_path("outputs", company_slug)
