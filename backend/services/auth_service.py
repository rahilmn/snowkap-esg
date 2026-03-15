"""Auth service — domain resolution, industry classification, magic links.

Per MASTER_BUILD_PLAN Phase 2C:
- Industry auto-classification via Claude (45 SASB categories)
- Auto-generate sustainabilityQuery + generalQuery from domain + industry
"""

import structlog
from anthropic import AsyncAnthropic

from backend.core.config import settings

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
    """Use Claude to classify a company into one of 45 SASB categories.

    Returns: {"industry": str, "sasb_category": str, "sustainability_query": str, "general_query": str}
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("anthropic_api_key_missing", action="classify_industry")
        return {
            "industry": None,
            "sasb_category": None,
            "sustainability_query": f'"{company_name}" ESG sustainability',
            "general_query": f'"{company_name}" news',
        }

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    prompt = f"""Given the company name "{company_name}" with domain "{domain}", classify it into exactly one of these SASB industry categories:

{chr(10).join(f"- {cat}" for cat in SASB_CATEGORIES)}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "industry": "<general industry name>",
  "sasb_category": "<exact SASB category from list>",
  "sustainability_query": "<Google News search query for this company's ESG/sustainability news>",
  "general_query": "<Google News search query for this company's general business news>"
}}"""

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        result = json.loads(response.content[0].text)
        logger.info("industry_classified", company=company_name, industry=result.get("industry"))
        return result
    except Exception as e:
        logger.error("industry_classification_failed", error=str(e), company=company_name)
        return {
            "industry": None,
            "sasb_category": None,
            "sustainability_query": f'"{company_name}" ESG sustainability',
            "general_query": f'"{company_name}" news',
        }
