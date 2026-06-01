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
from dataclasses import dataclass, field
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


# Phase 46.A — fallback signals when the LLM omits / malforms them.
# Industry × region anchors so even a low-confidence resolve still
# produces non-empty personalization signals for the rec engine.
_CANONICAL_READER_ROLES = (
    "CFO", "CEO", "Head of ESG", "Risk Officer", "Head of IR",
)

_DEFAULT_ROLE_BY_INDUSTRY = {
    "Financials/Banking": "CFO",
    "Asset Management": "Head of IR",
    "Insurance": "Risk Officer",
    "Power/Energy": "Head of ESG",
    "Renewable Energy": "Head of ESG",
    "Oil & Gas": "Head of ESG",
    "Steel": "Head of ESG",
    "Metals & Mining": "Head of ESG",
    "Automotive": "CFO",
    "Information Technology": "CEO",
    "Pharmaceuticals": "Risk Officer",
    "Chemicals": "Head of ESG",
    "Consumer/Beverage": "Head of ESG",
    "FMCG": "Head of ESG",
    "Footwear & Accessories": "Head of ESG",
    "Apparel Manufacturing": "Head of ESG",
    "Luxury Goods": "CEO",
    "Household & Personal Products": "Head of ESG",
    "Industrials/Conglomerate": "CFO",
    "Telecommunications": "CFO",
    "Real Estate": "CFO",
    "Aerospace & Defense": "Risk Officer",
    "Other": "CFO",
}

_GENERIC_PAINPOINTS_BY_INDUSTRY = {
    "Financials/Banking": [
        "Scope 3 financed-emissions disclosure (PCAF methodology)",
        "Climate-related credit risk + transition-finance exposure",
        "Cybersecurity governance + data-breach disclosure",
        "Regulatory stress test outcomes (RBI / Fed / ECB)",
        "ESG-linked lending share + green bond issuance",
    ],
    "Power/Energy": [
        "Coal-to-renewable transition capex commitments",
        "Air-quality + water-use compliance under industry-specific rules",
        "Stranded-asset risk on legacy fossil capacity",
        "Climate adaptation for thermal plants (heat stress, water)",
        "Just-transition commitments for displaced workforce",
    ],
    "Information Technology": [
        "Data-centre Scope 2 emissions + PPA renewable share",
        "Customer data privacy + cross-border data flow regulation",
        "AI governance + responsible-use policy",
        "DEI representation + pay-equity disclosure",
        "Cybersecurity incident reporting cadence",
    ],
    "Automotive": [
        "EV transition + battery supply-chain due diligence",
        "Tier-2/3 supplier human-rights audits (cobalt, lithium)",
        "Tailpipe + lifecycle emissions disclosure",
        "Connected-vehicle data privacy",
        "Recall + product-liability provisioning",
    ],
}

_GENERIC_KPIS_BY_INDUSTRY = {
    "Financials/Banking": [
        "Cost of capital + green bond spread",
        "ESG-linked lending share (% of portfolio)",
        "Financed emissions intensity (tCO2e per $M lent)",
        "Common Equity Tier 1 ratio",
    ],
    "Power/Energy": [
        "Renewable capacity share (% of total)",
        "Scope 1+2 emissions intensity (tCO2e/MWh)",
        "Plant load factor + heat rate",
        "Water consumption per MWh",
    ],
    "Information Technology": [
        "Data centre PUE (Power Usage Effectiveness)",
        "Renewable PPA coverage (% of grid load)",
        "Customer NPS + churn rate",
        "Revenue per employee + R&D % of revenue",
    ],
    "Automotive": [
        "Scope 1+2+3 emissions intensity (tCO2e per vehicle)",
        "EV share of total sales volume",
        "Supplier audit coverage (% of tier-1 spend)",
        "Recall provision / revenue ratio",
    ],
}


def _default_painpoints_for(industry: str, region: str) -> list[str]:
    """Return a non-empty list of industry-anchored ESG painpoints.

    Used when the LLM resolver returned no painpoints or returned them
    in a malformed shape. Keeps the recommendation engine's painpoint
    scorer fed with non-zero signals.
    """
    base = _GENERIC_PAINPOINTS_BY_INDUSTRY.get(industry) or [
        "Sector-relevant ESG materiality disclosure",
        "Climate-related financial risk under TCFD / ISSB",
        "Supply-chain due diligence (human rights + environment)",
        "Stakeholder engagement + governance transparency",
        "Regulatory + reputational risk monitoring",
    ]
    # Region-specific top-up so an Indian company gets BRSR-flavoured
    # painpoints and an EU company gets CSRD-flavoured ones.
    regional_top_up = {
        "INDIA": "BRSR Principle-wise compliance + SEBI ESG disclosure",
        "EU": "CSRD / ESRS reporting obligations + CBAM exposure",
        "US": "SEC climate disclosure rule + California SB-253/261 reporting",
        "UK": "FCA TCFD-aligned disclosures + Modern Slavery Act statement",
        "APAC": "Local exchange ESG disclosure rules (HKEX / SGX / ASX)",
    }.get(region)
    if regional_top_up and regional_top_up not in base:
        base = list(base) + [regional_top_up]
    return base[:7]


def _default_kpis_for(industry: str, region: str) -> list[str]:
    """Return a non-empty list of industry-anchored KPIs."""
    base = _GENERIC_KPIS_BY_INDUSTRY.get(industry) or [
        "Revenue growth + EBITDA margin",
        "Cost of capital + interest coverage",
        "ESG rating (MSCI / Sustainalytics / DJSI)",
        "Carbon emissions intensity (Scope 1+2 per unit revenue)",
        "Employee engagement + voluntary turnover",
    ]
    return base[:5]


def _default_reader_role_for(industry: str) -> str:
    """Pick a default reader role from the canonical 5."""
    return _DEFAULT_ROLE_BY_INDUSTRY.get(industry, "CFO")


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompanyInfo:
    """Resolved company profile. All fields populated unless explicitly
    marked optional.

    Phase 46.A — extended to carry inferred personalization signals
    (painpoints, KPIs, default reader role) so the recommendation engine
    and criticality scorer have explicit per-company anchors. Pre-46
    those signals were inferred weakly from industry alone; now the LLM
    resolver produces them upfront so every downstream stage scores
    against the same per-tenant context.
    """
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
    # Phase 46.A — inferred personalization signals
    inferred_painpoints: list[str] = field(default_factory=list)  # 5-7 ESG concerns
    inferred_kpis: list[str] = field(default_factory=list)        # 3-5 KPIs read by leadership
    default_reader_role: str = "CFO"  # CFO | CEO | Head of ESG | Risk Officer | Head of IR

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
            "inferred_painpoints": list(self.inferred_painpoints),
            "inferred_kpis": list(self.inferred_kpis),
            "default_reader_role": self.default_reader_role,
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
  "confidence": "high | medium | low",

  "inferred_painpoints": [
    "5-7 SPECIFIC ESG concerns this company's leadership tracks today",
    "Each phrased as a concrete worry, NOT a generic theme",
    "e.g. 'Scope 3 supply-chain emissions disclosure' not 'climate change'",
    "e.g. 'CBAM exposure on EU steel exports' not 'trade policy'",
    "Use named regulations and frameworks where relevant"
  ],
  "inferred_kpis": [
    "3-5 KPIs the CFO/CEO/Board actually report on quarterly",
    "Each must be measurable, e.g. 'Cost of capital (green bond spread vs G-Sec)'",
    "Mix financial + ESG, e.g. 'MSCI ESG rating', 'Scope 1+2 emissions intensity'"
  ],
  "default_reader_role": "CFO | CEO | Head of ESG | Risk Officer | Head of IR"
}

Painpoint + KPI guidance:
- Painpoints must be SPECIFIC to this company's industry + region + market cap.
  An Indian large-cap bank's painpoints are NOT the same as a US mid-cap retailer's.
- KPIs must be ones a real CFO would have on their dashboard. Don't invent ratios.
- default_reader_role: pick the role most likely to read this product daily for
  this company. Banks/financials → CFO. Tech/SaaS → CEO. Heavy industry → Head of ESG.
  Asset management → Head of IR.

CRITICAL — DO NOT MENTION external rating-bureau names in painpoints or KPIs:
  FORBIDDEN: MSCI ESG rating, DJSI, CRISIL ESG, Sustainalytics rating,
             ISS QualityScore, S&P Global ESG, Refinitiv ESG, Moody's ESG.
  REASON: The product user (CFO / CEO) does NOT want their daily brief
  flavoured by third-party bureau scores. They want concrete operational
  signals (emissions intensity, capex %, audit-coverage %), not rebadged
  rating-agency outputs.
  CORRECT: "Scope 1+2 emissions intensity (tCO2e/MWh)"
  WRONG:   "MSCI ESG rating trajectory"
  CORRECT: "Cost of capital + green bond spread vs sovereign"
  WRONG:   "DJSI inclusion + Sustainalytics severity tier"

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
  "confidence": "high",
  "inferred_painpoints": [
    "Refining sector Scope 1 + 2 emissions intensity under SEBI BRSR Principle 6",
    "Petrochemical CBAM exposure on EU exports (2026 mandatory reporting)",
    "Jio data privacy + cybersecurity disclosure under DPDP Act 2023",
    "Renewable energy transition capex (RIL's 100 GW target by 2030)",
    "Reliance Retail supplier code of conduct + forced-labor audits",
    "Climate-related financial disclosures under RBI's TCFD-aligned framework"
  ],
  "inferred_kpis": [
    "Scope 1+2+3 emissions intensity (tCO2e per tonne of crude processed)",
    "Cost of capital + green bond spread vs sovereign benchmark",
    "Renewable capacity build-out (% of 100 GW target achieved)",
    "Net debt / EBITDA + capex-to-revenue ratio",
    "Process safety incident rate (refinery + petrochem combined)"
  ],
  "default_reader_role": "CFO"
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
  "confidence": "high",
  "inferred_painpoints": [
    "Tier-2 + Tier-3 supplier forced-labor risk (Xinjiang cotton exposure)",
    "SEC climate disclosure rule compliance (Final Rule March 2024)",
    "Scope 3 Category 1 (purchased goods) emissions disclosure",
    "California Climate Disclosure Acts SB-253 + SB-261 reporting",
    "Sustainable packaging mandates under California SB-54"
  ],
  "inferred_kpis": [
    "Tier-2 supplier audit coverage (% of spend)",
    "Scope 3 emissions intensity (tCO2e per garment shipped)",
    "Recycled-content material share (% by weight)",
    "DEI executive representation + pay-equity ratio",
    "Net promoter score + customer-acquisition cost"
  ],
  "default_reader_role": "Head of ESG"
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
  "confidence": "high",
  "inferred_painpoints": [
    "Hazardous chemical disclosures under GHS + REACH (EU export markets)",
    "Soda ash plant Scope 1 emissions (process + combustion)",
    "Water stress in Mithapur (Gujarat) operations under BRSR Principle 6",
    "Plant safety + Process Safety Management (PSM) audit cadence",
    "Circular economy raw-material sourcing (recycled glass for soda ash)"
  ],
  "inferred_kpis": [
    "Process safety incident rate (PSER per 200,000 hours)",
    "Water consumption per tonne of product (m3/tonne)",
    "Scope 1+2 emissions intensity (tCO2e/tonne soda ash)",
    "Green capex as % of total capex (renewable energy + circular)",
    "EBITDA margin + Tata Trust dividend payout ratio"
  ],
  "default_reader_role": "Head of ESG"
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

    # Phase 46.A — sanitize personalization signals. Each field is a
    # safety net: if the LLM omitted or malformed them, we fall to a
    # generic industry-flavoured default so the rec engine + scorer
    # always have anchors to work with.
    #
    # Phase 46.J — explicitly strip rating-bureau names. User memo
    # ("I do not want those scorings like MSCI ESG rating, CRISIL score
    # etc.") forbids these bureau labels from any user-facing surface.
    # The system prompt forbids them too but Opus 4.6 occasionally
    # echoes them from training data; this regex catches whatever slips.
    _BUREAU_RE = re.compile(
        r"(MSCI(\s+ESG)?(\s+rating)?|DJSI(\s+(World|Emerging\s+Markets|Inclusion))?|"
        r"Sustainalytics(\s+(risk|severity|rating|tier))?|CRISIL(\s+ESG)?(\s+score)?|"
        r"ISS\s+QualityScore|S&P\s+Global\s+ESG|Refinitiv(\s+ESG)?|Moody'?s?\s+ESG)",
        re.IGNORECASE,
    )

    def _scrub_bureau(text: str) -> str:
        # Drop the bureau mention + any trailing "+ X" continuation
        out = _BUREAU_RE.sub("", text)
        # Tidy double-spaces / dangling separators
        out = re.sub(r"\s*\+\s*\+\s*", " + ", out)
        out = re.sub(r"\(\s*\+\s*", "(", out)
        out = re.sub(r"\s*\+\s*\)", ")", out)
        out = re.sub(r"\(\s*\)", "", out)
        out = re.sub(r"\s+", " ", out).strip(" +,-")
        return out

    raw_painpoints = parsed.get("inferred_painpoints") or []
    if not isinstance(raw_painpoints, list):
        raw_painpoints = []
    inferred_painpoints: list[str] = []
    for item in raw_painpoints:
        if isinstance(item, str) and item.strip():
            scrubbed = _scrub_bureau(item.strip())[:240]
            # If scrubbing left almost nothing, drop the painpoint entirely
            if scrubbed and len(scrubbed) >= 15:
                inferred_painpoints.append(scrubbed)
        if len(inferred_painpoints) >= 7:
            break
    if not inferred_painpoints:
        inferred_painpoints = _default_painpoints_for(industry, region)

    raw_kpis = parsed.get("inferred_kpis") or []
    if not isinstance(raw_kpis, list):
        raw_kpis = []
    inferred_kpis: list[str] = []
    for item in raw_kpis:
        if isinstance(item, str) and item.strip():
            scrubbed = _scrub_bureau(item.strip())[:200]
            if scrubbed and len(scrubbed) >= 10:
                inferred_kpis.append(scrubbed)
        if len(inferred_kpis) >= 5:
            break
    if not inferred_kpis:
        inferred_kpis = _default_kpis_for(industry, region)

    default_reader_role = (parsed.get("default_reader_role") or "").strip()
    if default_reader_role not in _CANONICAL_READER_ROLES:
        default_reader_role = _default_reader_role_for(industry)

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
        inferred_painpoints=inferred_painpoints,
        inferred_kpis=inferred_kpis,
        default_reader_role=default_reader_role,
    )


def resolve_company_from_domain(
    domain: str, name_hint: str | None = None,
) -> CompanyInfo | None:
    """Resolve a domain to a canonical company via Opus 4.6.

    `name_hint` (Phase 48) — when the caller already knows the company name
    (e.g. a curated launch list), pass it so the LLM doesn't mis-read the
    domain. Without it, the resolver mis-resolved `waaree.com` →
    "Aaree Technologies Private" (dropped the W). The hint is advisory: the
    LLM still fills industry / ticker / painpoints / KPIs, but anchors the
    identity to the known name.

    Returns CompanyInfo on success. Returns None when:
      - The LLM gateway is not configured (neither OPENROUTER nor OPENAI key set)
      - The LLM call fails / times out
      - The response can't be parsed as the expected JSON shape
    """
    if not domain or not domain.strip():
        return None

    domain = domain.strip().lower().lstrip("www.")

    try:
        from engine.llm import get_llm_client
    except ImportError:
        logger.warning("llm_company_resolver: engine.llm not importable")
        return None

    user_msg = f"Domain: {domain}\n\n"
    if name_hint and name_hint.strip():
        user_msg += (
            f"The company at this domain is known to be: {name_hint.strip()}. "
            "Use this exact identity; resolve its industry, ticker, frameworks, "
            "painpoints and KPIs accordingly.\n\n"
        )
    user_msg += "Return the JSON."

    try:
        llm = get_llm_client(task_class="reasoning_heavy")
        resp = llm.complete(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,        # low — we want deterministic resolution
            # Phase 46.K: Pre-Phase-46-A the response was ~120 tokens
            # (core fields only). Adding inferred_painpoints (5-7 items
            # × ~30 tokens), inferred_kpis (3-5 × ~25 tokens) and the
            # default_reader_role pushed real responses to ~400-700
            # tokens. The previous 500-token cap was truncating Opus
            # 4.6's JSON mid-array → JSONDecodeError → resolver returns
            # None → onboard 422. Bumped to 2000 for headroom.
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        logger.exception(
            "llm_company_resolver: LLM call failed for %s: %s: %s",
            domain, type(exc).__name__, exc,
        )
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
        # Phase 46.K — log the truncation evidence so we can see WHY
        # parsing failed. Most common cause is response truncation at
        # max_tokens (Opus 4.6 stops mid-array). Show the last 200
        # chars of the raw response — if it ends mid-bracket, that's
        # the smoking gun.
        logger.warning(
            "llm_company_resolver: JSON parse failed for %s: %s "
            "(raw response len=%d, last 200 chars: %r)",
            domain, exc, len(raw), raw[-200:],
        )
        return None

    try:
        return _validate_response(parsed, domain)
    except Exception as exc:
        logger.exception(
            "llm_company_resolver: validation failed for %s: %s: %s",
            domain, type(exc).__name__, exc,
        )
        return None


__all__ = [
    "CompanyInfo",
    "resolve_company_from_domain",
    "CANONICAL_INDUSTRIES",
    "CANONICAL_REGIONS",
    "INDUSTRY_TO_SASB_DEFAULT",
]
