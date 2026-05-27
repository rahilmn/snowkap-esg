"""Phase 45 — LLM-driven company resolution.

Replaces the yfinance heuristic that picked the wrong company for
ambiguous domains. Given a domain (e.g. "reliance.com"), an LLM call
to Opus 4.6 returns the canonical company:

  - canonical_name      ("Reliance Industries Limited")
  - slug                ("reliance-industries-limited")
  - primary_ticker      ("RELIANCE.NS")
  - industry            (one of our 14 canonical industries)
  - sasb_category       (SASB sector label)
  - framework_region    (INDIA / EU / UK / US / APAC / GLOBAL)
  - headquarter_country ("IN")
  - headquarter_city    ("Mumbai")
  - market_cap_tier     ("Large Cap" / "Mid Cap" / "Small Cap")
  - description_short   ("India's largest private-sector company...")

Why an LLM here and not yfinance:

  yfinance.Ticker("reliance.com") doesn't exist. We have to feed it a
  ticker. Without a ticker we run a search against a free-text company
  name, which yfinance resolves by substring match. "reliance" matches
  Reliance Steel (NYSE:RS) BEFORE Reliance Industries (NSE:RELIANCE)
  because the steel company's name has fewer ambiguity points. We saw
  this in the 2026-05-28 validation log: reliance.com → industry "Steel"
  (wrong).

  Opus 4.6 given just the domain knows reliance.com → Reliance
  Industries via the company's actual presence on the web. It also
  classifies into our 14-industry taxonomy directly (no yfinance
  industry-string → canonical-industry mapping gap), and returns the
  correct ticker with the .NS suffix needed for NSE-listed names.

Cost: ~$0.04 per onboard (one Opus call). The downstream pipeline is
~$0.50 per onboard so this is a 7% cost lift for catastrophic-quality
prevention (wrong-company onboard = unrecoverable bad first impression).

Postgres-only: this module doesn't touch the database directly — it
just returns the parsed dataclass. The caller (onboard_v2.py) owns the
companies/article_pool/company_article_view writes.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical taxonomies — what the LLM MUST classify into
# ---------------------------------------------------------------------------

CANONICAL_INDUSTRIES = (
    "Financials/Banking",
    "Asset Management",
    "Insurance",
    "Power/Energy",
    "Renewable Energy",
    "Oil & Gas",
    "Steel",
    "Metals & Mining",
    "Automotive",
    "Information Technology",
    "Pharmaceuticals",
    "Chemicals",
    "Consumer/Beverage",
    "FMCG",
    "Footwear & Accessories",
    "Apparel Manufacturing",
    "Luxury Goods",
    "Household & Personal Products",
    "Industrials/Conglomerate",
    "Telecommunications",
    "Real Estate",
    "Aerospace & Defense",
    "Other",
)

CANONICAL_REGIONS = ("INDIA", "EU", "UK", "US", "APAC", "GLOBAL")

# Map industry → SASB sector for the SASB materiality TTL.
# Sectors NOT in this map are valid but won't get SASB-specific
# materiality weights (falls back to neutral 0.5 + sasb_unmapped warning).
INDUSTRY_TO_SASB_DEFAULT = {
    "Financials/Banking": "Commercial Banks",
    "Asset Management": "Asset Management & Custody Activities",
    "Insurance": "Insurance",
    "Power/Energy": "Electric Utilities & Power Generators",
    "Renewable Energy": "Solar Technology & Project Developers",
    "Oil & Gas": "Oil & Gas — Exploration & Production",
    "Steel": "Iron & Steel Producers",
    "Metals & Mining": "Metals & Mining",
    "Automotive": "Automobiles",
    "Information Technology": "Software & IT Services",
    "Pharmaceuticals": "Pharmaceuticals",
    "Chemicals": "Chemicals",
    "Consumer/Beverage": "Non-Alcoholic Beverages",
    "FMCG": "Processed Foods",
    "Footwear & Accessories": "Apparel, Accessories & Footwear",
    "Apparel Manufacturing": "Apparel, Accessories & Footwear",
    "Luxury Goods": "Apparel, Accessories & Footwear",
    "Household & Personal Products": "Household & Personal Products",
    "Industrials/Conglomerate": "Industrial Conglomerates",
    "Telecommunications": "Telecommunication Services",
    "Real Estate": "Real Estate",
    "Aerospace & Defense": "Aerospace & Defense",
}


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompanyInfo:
    """Resolved company profile. All fields populated unless explicitly
    marked optional."""
    canonical_name: str             # "Reliance Industries Limited"
    slug: str                       # "reliance-industries-limited"
    primary_ticker: str             # "RELIANCE.NS"
    industry: str                   # one of CANONICAL_INDUSTRIES
    sasb_category: str              # SASB sector
    framework_region: str           # INDIA / EU / UK / US / APAC / GLOBAL
    headquarter_country: str        # ISO 2-letter ("IN")
    headquarter_city: str           # "Mumbai"
    market_cap_tier: str = "Mid Cap"     # Large Cap / Mid Cap / Small Cap
    description_short: str = ""          # 1-sentence company summary
    confidence: str = "medium"           # high / medium / low — how sure the LLM is

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_name": self.canonical_name,
            "slug": self.slug,
            "primary_ticker": self.primary_ticker,
            "industry": self.industry,
            "sasb_category": self.sasb_category,
            "framework_region": self.framework_region,
            "headquarter_country": self.headquarter_country,
            "headquarter_city": self.headquarter_city,
            "market_cap_tier": self.market_cap_tier,
            "description_short": self.description_short,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are a securities reference desk analyst with deep
knowledge of public companies globally, especially India's top-1000 listed
companies. Your job is to map an internet domain to the canonical PUBLIC
company that owns it.

Rules:
1. Identify the company by inspecting the domain. Use your knowledge of
   which company owns each major .com / .co.in / .com.au domain.
2. If multiple companies share a similar name (e.g. "Reliance" could mean
   Reliance Industries OR Reliance Steel & Aluminum), pick the LARGEST
   publicly-listed entity that actually owns the domain. reliance.com is
   Reliance Industries (Indian conglomerate, NSE:RELIANCE), NOT Reliance
   Steel (NYSE:RS) — the latter owns rsac.com.
3. Always return the company's NSE/BSE ticker for Indian companies (with
   .NS suffix), NYSE/NASDAQ for US (no suffix), LSE for UK (.L suffix),
   Xetra for Germany (.DE suffix), Euronext (.PA/.AS) for France/Netherlands.
4. NEVER guess. If you genuinely don't know what company owns a domain,
   set confidence: "low" and return your best guess + description.

Return STRICT JSON with these fields (no commentary, no markdown fences):

{
  "canonical_name": "Full legal name including suffix (Ltd / Inc / SE / AG)",
  "slug": "url-safe-lowercase-hyphenated-slug",
  "primary_ticker": "TICKER.SUFFIX (e.g. RELIANCE.NS, AAPL, PUM.DE)",
  "industry": "ONE of the canonical industries below",
  "framework_region": "INDIA | EU | UK | US | APAC | GLOBAL",
  "headquarter_country": "2-letter ISO country code (IN, US, DE, GB, etc.)",
  "headquarter_city": "City name (e.g. Mumbai, New York, Munich)",
  "market_cap_tier": "Large Cap | Mid Cap | Small Cap",
  "description_short": "1-sentence company description",
  "confidence": "high | medium | low"
}

Canonical industries (pick exactly one):
- Financials/Banking
- Asset Management
- Insurance
- Power/Energy
- Renewable Energy
- Oil & Gas
- Steel
- Metals & Mining
- Automotive
- Information Technology
- Pharmaceuticals
- Chemicals
- Consumer/Beverage
- FMCG
- Footwear & Accessories
- Apparel Manufacturing
- Luxury Goods
- Household & Personal Products
- Industrials/Conglomerate
- Telecommunications
- Real Estate
- Aerospace & Defense
- Other

Examples:

Input: "reliance.com"
Output: {
  "canonical_name": "Reliance Industries Limited",
  "slug": "reliance-industries",
  "primary_ticker": "RELIANCE.NS",
  "industry": "Industrials/Conglomerate",
  "framework_region": "INDIA",
  "headquarter_country": "IN",
  "headquarter_city": "Mumbai",
  "market_cap_tier": "Large Cap",
  "description_short": "India's largest private-sector conglomerate, spanning oil refining, petrochemicals, telecom (Jio) and retail.",
  "confidence": "high"
}

Input: "underarmour.com"
Output: {
  "canonical_name": "Under Armour, Inc.",
  "slug": "under-armour",
  "primary_ticker": "UAA",
  "industry": "Footwear & Accessories",
  "framework_region": "US",
  "headquarter_country": "US",
  "headquarter_city": "Baltimore",
  "market_cap_tier": "Mid Cap",
  "description_short": "American sports apparel and footwear manufacturer with global presence.",
  "confidence": "high"
}

Input: "tatachemicals.com"
Output: {
  "canonical_name": "Tata Chemicals Limited",
  "slug": "tata-chemicals",
  "primary_ticker": "TATACHEM.NS",
  "industry": "Chemicals",
  "framework_region": "INDIA",
  "headquarter_country": "IN",
  "headquarter_city": "Mumbai",
  "market_cap_tier": "Large Cap",
  "description_short": "Indian chemicals company, part of the Tata Group, producing inorganic chemicals and consumer products.",
  "confidence": "high"
}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    """URL-safe slug — lowercase, hyphen-separated, no special chars."""
    if not value:
        return ""
    out = value.lower().strip()
    out = re.sub(r"[^a-z0-9]+", "-", out)
    out = re.sub(r"-+", "-", out).strip("-")
    return out


def _validate_response(parsed: dict[str, Any], domain: str) -> CompanyInfo:
    """Coerce LLM output into a clean CompanyInfo, with sane defaults."""
    name = (parsed.get("canonical_name") or "").strip()
    if not name:
        raise ValueError("LLM returned empty canonical_name")

    slug = (parsed.get("slug") or "").strip()
    if not slug:
        slug = _slugify(name)
    else:
        # Coerce to safe form even if the LLM emitted weird chars
        slug = _slugify(slug)

    ticker = (parsed.get("primary_ticker") or "").strip()

    industry = (parsed.get("industry") or "Other").strip()
    if industry not in CANONICAL_INDUSTRIES:
        # Soft-coerce — find the closest canonical match
        for canonical in CANONICAL_INDUSTRIES:
            if canonical.lower() == industry.lower():
                industry = canonical
                break
        else:
            logger.warning(
                "llm_company_resolver: LLM returned non-canonical industry %r for %s; "
                "falling back to 'Other'",
                industry, domain,
            )
            industry = "Other"

    region = (parsed.get("framework_region") or "GLOBAL").strip().upper()
    if region not in CANONICAL_REGIONS:
        logger.warning(
            "llm_company_resolver: non-canonical region %r for %s; defaulting to GLOBAL",
            region, domain,
        )
        region = "GLOBAL"

    hq_country = (parsed.get("headquarter_country") or "").strip().upper()
    if len(hq_country) != 2:
        # If LLM emitted "India" instead of "IN", do a quick map
        country_map = {
            "INDIA": "IN", "UNITED STATES": "US", "USA": "US",
            "UNITED KINGDOM": "GB", "GERMANY": "DE", "FRANCE": "FR",
            "NETHERLANDS": "NL", "ITALY": "IT", "SPAIN": "ES",
            "JAPAN": "JP", "CHINA": "CN", "SINGAPORE": "SG",
            "AUSTRALIA": "AU", "CANADA": "CA",
        }
        hq_country = country_map.get(hq_country.upper(), hq_country[:2] if hq_country else "")

    hq_city = (parsed.get("headquarter_city") or "").strip()

    cap_tier = (parsed.get("market_cap_tier") or "Mid Cap").strip()
    if cap_tier not in ("Large Cap", "Mid Cap", "Small Cap"):
        cap_tier = "Mid Cap"

    description = (parsed.get("description_short") or "").strip()[:280]
    confidence = (parsed.get("confidence") or "medium").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    # SASB category — derived from the industry, not asked of the LLM
    # directly (one less thing for the LLM to get wrong).
    sasb_category = INDUSTRY_TO_SASB_DEFAULT.get(industry, "")

    return CompanyInfo(
        canonical_name=name,
        slug=slug,
        primary_ticker=ticker,
        industry=industry,
        sasb_category=sasb_category,
        framework_region=region,
        headquarter_country=hq_country,
        headquarter_city=hq_city,
        market_cap_tier=cap_tier,
        description_short=description,
        confidence=confidence,
    )


def resolve_company_from_domain(domain: str) -> CompanyInfo | None:
    """Resolve a domain to a canonical company via Opus 4.6.

    Returns CompanyInfo on success. Returns None when:
      - The LLM gateway is not configured (neither OPENROUTER nor OPENAI key set)
      - The LLM call fails / times out
      - The response can't be parsed as the expected JSON shape

    Caller (onboard_v2.py) decides how to surface the failure to the user
    (typically: HTTP 422 with the domain echoed back).
    """
    if not domain or not domain.strip():
        return None

    domain = domain.strip().lower().lstrip("www.")

    try:
        from engine.llm import get_llm_client
    except ImportError:
        logger.warning("llm_company_resolver: engine.llm not importable")
        return None

    try:
        llm = get_llm_client(task_class="reasoning_heavy")
        resp = llm.complete(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Domain: {domain}\n\nReturn the JSON."},
            ],
            temperature=0.1,        # low — we want deterministic resolution
            max_tokens=500,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.warning("llm_company_resolver: LLM call failed for %s: %s",
                       domain, type(exc).__name__)
        return None

    raw = (getattr(resp, "text", "") or "").strip()
    if not raw:
        logger.warning("llm_company_resolver: empty response for %s", domain)
        return None

    # Strip code fences if the LLM emitted any
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("llm_company_resolver: JSON parse failed for %s: %s",
                       domain, exc)
        return None

    try:
        return _validate_response(parsed, domain)
    except Exception as exc:
        logger.warning("llm_company_resolver: validation failed for %s: %s",
                       domain, exc)
        return None


__all__ = [
    "CompanyInfo",
    "resolve_company_from_domain",
    "CANONICAL_INDUSTRIES",
    "CANONICAL_REGIONS",
    "INDUSTRY_TO_SASB_DEFAULT",
]
