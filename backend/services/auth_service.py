"""Auth service — domain resolution, industry classification, magic links.

Per MASTER_BUILD_PLAN Phase 2C:
- Industry auto-classification via LLM (45 SASB categories)
- Auto-generate sustainabilityQuery + generalQuery from domain + industry
"""

import json

import structlog

from backend.core.config import settings
from backend.core import llm

logger = structlog.get_logger()

SASB_CATEGORIES = [
    "Apparel, Accessories & Footwear", "Appliance Manufacturing", "Auto Parts",
    "Automobiles", "Biotechnology & Pharmaceuticals", "Building Products & Furnishings",
    "Casinos & Gaming", "Chemicals", "Coal Operations", "Commercial Banks",
    "Construction Materials", "Containers & Packaging", "Cruise Lines",
    "Drug Retailers", "E-Commerce", "Electric Utilities & Power Generators",
    "Electrical & Electronic Equipment", "Engineering & Construction Services",
    "Food & Beverage", "Food Retailers & Distributors", "Forestry Management",
    "Gas Utilities & Distributors", "Hardware", "Health Care Delivery",
    "Hotels & Lodging", "Household & Personal Products", "Industrial Machinery & Goods",
    "Insurance", "Internet Media & Services", "Investment Banking & Brokerage",
    "Iron & Steel Producers", "Leisure Facilities", "Managed Care",
    "Meat, Poultry & Dairy", "Media & Entertainment", "Metals & Mining",
    "Multiline & Specialty Retailers", "Oil & Gas", "Processed Foods",
    "Professional & Commercial Services", "Real Estate", "Semiconductors",
    "Software & IT Services", "Telecommunication Services", "Tobacco",
    "Toys & Sporting Goods", "Transportation", "Waste Management", "Water Utilities",
]


async def classify_industry(company_name: str, domain: str) -> dict[str, str | None]:
    """Use LLM to classify a company into one of 45 SASB categories.

    Returns: {"industry": str, "sasb_category": str, "sustainability_query": str, "general_query": str}
    """
    if not llm.is_configured():
        logger.warning("llm_not_configured", action="classify_industry")
        # GAP 9: Include E/S terms even in fallback to avoid governance-only bias
        return {
            "industry": None,
            "sasb_category": None,
            "sustainability_query": (
                f'"{company_name}" ESG sustainability emissions climate risk '
                f'workforce diversity social impact governance'
            ),
            "general_query": f'"{company_name}" news',
        }

    prompt = f"""Given the company name "{company_name}" with domain "{domain}", classify it into exactly one of these SASB industry categories:

{chr(10).join(f"- {cat}" for cat in SASB_CATEGORIES)}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "industry": "<general industry name>",
  "sasb_category": "<exact SASB category from list>",
  "sustainability_query": "<Google News search query for this company's ESG/sustainability news>",
  "general_query": "<Google News search query for this company's general business news>"
}}

IMPORTANT for sustainability_query and general_query:
- The queries MUST cover ALL THREE ESG pillars — Environmental, Social, AND Governance.
- For financial sector companies (banks, NBFCs, AMCs, insurance, investment firms):
  Include Environmental terms like "financed emissions", "climate risk disclosure", "green bond",
  "sustainable finance", "green lending", "renewable energy financing".
  Include Social terms like "financial inclusion", "workforce diversity", "social impact",
  "community investment", "responsible lending".
  Do NOT only include Governance terms — balance across E, S, and G.
- For all industries: ensure the sustainability_query includes at least one term from each
  of E (environment/climate/emissions), S (social/workforce/community), and G (governance/compliance)."""

    try:
        text = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        result = json.loads(text)
        logger.info("industry_classified", company=company_name, industry=result.get("industry"))
        return result
    except Exception as e:
        logger.error("industry_classification_failed", error=str(e), company=company_name)
        # GAP 9: Include E/S terms even in error fallback
        return {
            "industry": None,
            "sasb_category": None,
            "sustainability_query": (
                f'"{company_name}" ESG sustainability emissions climate risk '
                f'workforce diversity social impact governance'
            ),
            "general_query": f'"{company_name}" news',
        }
