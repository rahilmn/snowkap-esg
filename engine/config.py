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


@lru_cache(maxsize=1)
def load_companies() -> list[Company]:
    """Load the 7 target companies from config/companies.json."""
    data = _load_json(CONFIG_DIR / "companies.json")
    return [Company.from_dict(c) for c in data["companies"]]


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
