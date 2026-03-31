"""Industry Materiality Weights — SASB-Aligned (Enhancement 6).

Provides SASB-aligned materiality weight mappings for ESG themes by industry.
Each industry maps all 21 themes from the ESG taxonomy to a materiality weight
(0.0 to 1.0), enabling industry-aware relevance scoring.

Weight scale:
    1.0       = Highly material (SASB flags as key issue for the industry)
    0.7 - 0.9 = Material (relevant but not primary)
    0.4 - 0.6 = Moderately material
    0.1 - 0.3 = Low materiality (tangential to the industry)
"""

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# 21-Theme Materiality Weights by Industry (SASB-aligned)
# ---------------------------------------------------------------------------
# Theme names must match the ESG_TAXONOMY keys in esg_theme_tagger.py:
#   Environmental: Energy, Emissions, Water, Biodiversity, Waste & Circularity,
#                  Climate Adaptation, Land Use, Air Quality
#   Social:        Human Capital, Health & Safety, Community Impact,
#                  Supply Chain Labor, Product Safety, Access & Affordability, DEI
#   Governance:    Board & Leadership, Ethics & Compliance, Risk Management,
#                  Transparency & Disclosure, Shareholder Rights, Tax Transparency

_ALL_THEMES = [
    # Environmental
    "Energy",
    "Emissions",
    "Water",
    "Biodiversity",
    "Waste & Circularity",
    "Climate Adaptation",
    "Land Use",
    "Air Quality",
    # Social
    "Human Capital",
    "Health & Safety",
    "Community Impact",
    "Supply Chain Labor",
    "Product Safety",
    "Access & Affordability",
    "DEI",
    # Governance
    "Board & Leadership",
    "Ethics & Compliance",
    "Risk Management",
    "Transparency & Disclosure",
    "Shareholder Rights",
    "Tax Transparency",
]

# ---------------------------------------------------------------------------
# Industry → Theme → Weight mappings
# ---------------------------------------------------------------------------

MATERIALITY_MAP: dict[str, dict[str, float]] = {
    # ── Financials / Banking ──
    "Financials": {
        "Energy": 0.3,
        "Emissions": 0.2,
        "Water": 0.1,
        "Biodiversity": 0.2,
        "Waste & Circularity": 0.1,
        "Climate Adaptation": 0.7,
        "Land Use": 0.1,
        "Air Quality": 0.1,
        "Human Capital": 0.8,
        "Health & Safety": 0.4,
        "Community Impact": 0.6,
        "Supply Chain Labor": 0.3,
        "Product Safety": 0.5,
        "Access & Affordability": 0.7,
        "DEI": 0.8,
        "Board & Leadership": 0.9,
        "Ethics & Compliance": 1.0,
        "Risk Management": 1.0,
        "Transparency & Disclosure": 0.9,
        "Shareholder Rights": 0.8,
        "Tax Transparency": 0.7,
    },
    "Banking": {
        "Energy": 0.2,
        "Emissions": 0.2,
        "Water": 0.1,
        "Biodiversity": 0.2,
        "Waste & Circularity": 0.1,
        "Climate Adaptation": 0.7,
        "Land Use": 0.1,
        "Air Quality": 0.1,
        "Human Capital": 0.8,
        "Health & Safety": 0.3,
        "Community Impact": 0.6,
        "Supply Chain Labor": 0.3,
        "Product Safety": 0.5,
        "Access & Affordability": 0.8,
        "DEI": 0.8,
        "Board & Leadership": 0.9,
        "Ethics & Compliance": 1.0,
        "Risk Management": 1.0,
        "Transparency & Disclosure": 0.9,
        "Shareholder Rights": 0.9,
        "Tax Transparency": 0.8,
    },

    # ── Infrastructure / Power Generation ──
    "Infrastructure": {
        "Energy": 0.9,
        "Emissions": 1.0,
        "Water": 0.7,
        "Biodiversity": 0.7,
        "Waste & Circularity": 0.6,
        "Climate Adaptation": 1.0,
        "Land Use": 0.8,
        "Air Quality": 0.8,
        "Human Capital": 0.6,
        "Health & Safety": 0.9,
        "Community Impact": 0.8,
        "Supply Chain Labor": 0.5,
        "Product Safety": 0.5,
        "Access & Affordability": 0.7,
        "DEI": 0.4,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.9,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },
    "Power Generation": {
        "Energy": 1.0,
        "Emissions": 1.0,
        "Water": 0.8,
        "Biodiversity": 0.6,
        "Waste & Circularity": 0.7,
        "Climate Adaptation": 1.0,
        "Land Use": 0.7,
        "Air Quality": 0.9,
        "Human Capital": 0.6,
        "Health & Safety": 0.9,
        "Community Impact": 0.8,
        "Supply Chain Labor": 0.5,
        "Product Safety": 0.4,
        "Access & Affordability": 0.8,
        "DEI": 0.4,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.7,
        "Risk Management": 0.9,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },

    # ── Consumer Goods / Apparel ──
    "Consumer Goods": {
        "Energy": 0.5,
        "Emissions": 0.6,
        "Water": 0.6,
        "Biodiversity": 0.4,
        "Waste & Circularity": 0.8,
        "Climate Adaptation": 0.5,
        "Land Use": 0.4,
        "Air Quality": 0.4,
        "Human Capital": 0.7,
        "Health & Safety": 0.7,
        "Community Impact": 0.6,
        "Supply Chain Labor": 1.0,
        "Product Safety": 1.0,
        "Access & Affordability": 0.6,
        "DEI": 0.7,
        "Board & Leadership": 0.6,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.7,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },
    "Apparel": {
        "Energy": 0.5,
        "Emissions": 0.6,
        "Water": 0.7,
        "Biodiversity": 0.3,
        "Waste & Circularity": 0.9,
        "Climate Adaptation": 0.4,
        "Land Use": 0.3,
        "Air Quality": 0.3,
        "Human Capital": 0.8,
        "Health & Safety": 0.8,
        "Community Impact": 0.6,
        "Supply Chain Labor": 1.0,
        "Product Safety": 0.9,
        "Access & Affordability": 0.5,
        "DEI": 0.8,
        "Board & Leadership": 0.5,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.6,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.4,
        "Tax Transparency": 0.4,
    },

    # ── Renewable Resources & Alternative Energy ──
    "Renewable Resources & Alternative Energy": {
        "Energy": 1.0,
        "Emissions": 1.0,
        "Water": 0.5,
        "Biodiversity": 0.7,
        "Waste & Circularity": 0.7,
        "Climate Adaptation": 0.9,
        "Land Use": 0.8,
        "Air Quality": 0.5,
        "Human Capital": 0.7,
        "Health & Safety": 0.8,
        "Community Impact": 0.7,
        "Supply Chain Labor": 0.6,
        "Product Safety": 0.6,
        "Access & Affordability": 0.8,
        "DEI": 0.5,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.7,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.9,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },

    # ── Technology ──
    "Technology": {
        "Energy": 0.6,
        "Emissions": 0.3,
        "Water": 0.2,
        "Biodiversity": 0.1,
        "Waste & Circularity": 0.5,
        "Climate Adaptation": 0.3,
        "Land Use": 0.1,
        "Air Quality": 0.1,
        "Human Capital": 1.0,
        "Health & Safety": 0.4,
        "Community Impact": 0.5,
        "Supply Chain Labor": 0.6,
        "Product Safety": 0.9,
        "Access & Affordability": 0.8,
        "DEI": 0.9,
        "Board & Leadership": 0.8,
        "Ethics & Compliance": 0.9,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.7,
        "Tax Transparency": 0.7,
    },

    # ── Additional industries for broader coverage ──

    "Healthcare": {
        "Energy": 0.3,
        "Emissions": 0.3,
        "Water": 0.4,
        "Biodiversity": 0.2,
        "Waste & Circularity": 0.8,
        "Climate Adaptation": 0.4,
        "Land Use": 0.2,
        "Air Quality": 0.3,
        "Human Capital": 0.9,
        "Health & Safety": 1.0,
        "Community Impact": 0.8,
        "Supply Chain Labor": 0.7,
        "Product Safety": 1.0,
        "Access & Affordability": 1.0,
        "DEI": 0.7,
        "Board & Leadership": 0.8,
        "Ethics & Compliance": 1.0,
        "Risk Management": 0.9,
        "Transparency & Disclosure": 0.9,
        "Shareholder Rights": 0.6,
        "Tax Transparency": 0.5,
    },

    "Oil & Gas": {
        "Energy": 1.0,
        "Emissions": 1.0,
        "Water": 0.9,
        "Biodiversity": 0.8,
        "Waste & Circularity": 0.7,
        "Climate Adaptation": 1.0,
        "Land Use": 0.9,
        "Air Quality": 1.0,
        "Human Capital": 0.7,
        "Health & Safety": 1.0,
        "Community Impact": 0.9,
        "Supply Chain Labor": 0.6,
        "Product Safety": 0.7,
        "Access & Affordability": 0.5,
        "DEI": 0.4,
        "Board & Leadership": 0.8,
        "Ethics & Compliance": 0.9,
        "Risk Management": 1.0,
        "Transparency & Disclosure": 0.9,
        "Shareholder Rights": 0.7,
        "Tax Transparency": 0.8,
    },

    "Mining & Metals": {
        "Energy": 0.8,
        "Emissions": 0.9,
        "Water": 1.0,
        "Biodiversity": 1.0,
        "Waste & Circularity": 0.9,
        "Climate Adaptation": 0.8,
        "Land Use": 1.0,
        "Air Quality": 0.9,
        "Human Capital": 0.7,
        "Health & Safety": 1.0,
        "Community Impact": 1.0,
        "Supply Chain Labor": 0.6,
        "Product Safety": 0.5,
        "Access & Affordability": 0.3,
        "DEI": 0.5,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.9,
        "Risk Management": 0.9,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.6,
        "Tax Transparency": 0.7,
    },

    "Real Estate": {
        "Energy": 0.9,
        "Emissions": 0.7,
        "Water": 0.6,
        "Biodiversity": 0.5,
        "Waste & Circularity": 0.6,
        "Climate Adaptation": 0.9,
        "Land Use": 1.0,
        "Air Quality": 0.5,
        "Human Capital": 0.6,
        "Health & Safety": 0.7,
        "Community Impact": 0.8,
        "Supply Chain Labor": 0.4,
        "Product Safety": 0.5,
        "Access & Affordability": 0.8,
        "DEI": 0.5,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.7,
        "Tax Transparency": 0.6,
    },

    "Transportation": {
        "Energy": 0.9,
        "Emissions": 1.0,
        "Water": 0.3,
        "Biodiversity": 0.4,
        "Waste & Circularity": 0.5,
        "Climate Adaptation": 0.8,
        "Land Use": 0.6,
        "Air Quality": 0.9,
        "Human Capital": 0.8,
        "Health & Safety": 1.0,
        "Community Impact": 0.7,
        "Supply Chain Labor": 0.5,
        "Product Safety": 0.8,
        "Access & Affordability": 0.7,
        "DEI": 0.5,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.7,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.7,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },

    "Automobiles": {
        "Energy": 0.8,
        "Emissions": 1.0,
        "Water": 0.5,
        "Biodiversity": 0.3,
        "Waste & Circularity": 0.8,
        "Climate Adaptation": 0.7,
        "Land Use": 0.4,
        "Air Quality": 0.9,
        "Human Capital": 0.8,
        "Health & Safety": 0.9,
        "Community Impact": 0.6,
        "Supply Chain Labor": 0.8,
        "Product Safety": 1.0,
        "Access & Affordability": 0.6,
        "DEI": 0.6,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.6,
        "Tax Transparency": 0.6,
    },

    "Agriculture": {
        "Energy": 0.5,
        "Emissions": 0.8,
        "Water": 1.0,
        "Biodiversity": 1.0,
        "Waste & Circularity": 0.6,
        "Climate Adaptation": 1.0,
        "Land Use": 1.0,
        "Air Quality": 0.5,
        "Human Capital": 0.6,
        "Health & Safety": 0.8,
        "Community Impact": 0.9,
        "Supply Chain Labor": 0.9,
        "Product Safety": 0.8,
        "Access & Affordability": 0.7,
        "DEI": 0.4,
        "Board & Leadership": 0.5,
        "Ethics & Compliance": 0.7,
        "Risk Management": 0.7,
        "Transparency & Disclosure": 0.7,
        "Shareholder Rights": 0.4,
        "Tax Transparency": 0.4,
    },

    "Chemicals": {
        "Energy": 0.8,
        "Emissions": 1.0,
        "Water": 0.9,
        "Biodiversity": 0.7,
        "Waste & Circularity": 1.0,
        "Climate Adaptation": 0.7,
        "Land Use": 0.7,
        "Air Quality": 1.0,
        "Human Capital": 0.6,
        "Health & Safety": 1.0,
        "Community Impact": 0.8,
        "Supply Chain Labor": 0.5,
        "Product Safety": 1.0,
        "Access & Affordability": 0.4,
        "DEI": 0.4,
        "Board & Leadership": 0.7,
        "Ethics & Compliance": 0.8,
        "Risk Management": 0.9,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.5,
        "Tax Transparency": 0.5,
    },

    "Telecommunications": {
        "Energy": 0.6,
        "Emissions": 0.4,
        "Water": 0.1,
        "Biodiversity": 0.2,
        "Waste & Circularity": 0.5,
        "Climate Adaptation": 0.3,
        "Land Use": 0.3,
        "Air Quality": 0.1,
        "Human Capital": 0.9,
        "Health & Safety": 0.4,
        "Community Impact": 0.7,
        "Supply Chain Labor": 0.5,
        "Product Safety": 0.8,
        "Access & Affordability": 1.0,
        "DEI": 0.7,
        "Board & Leadership": 0.8,
        "Ethics & Compliance": 0.9,
        "Risk Management": 0.8,
        "Transparency & Disclosure": 0.8,
        "Shareholder Rights": 0.7,
        "Tax Transparency": 0.6,
    },
}

# ---------------------------------------------------------------------------
# Fuzzy industry name aliases for flexible matching
# ---------------------------------------------------------------------------

_INDUSTRY_ALIASES: dict[str, str] = {
    # Financials cluster
    "financial": "Financials",
    "financial services": "Financials",
    "finance": "Financials",
    "insurance": "Financials",
    "banking": "Banking",
    "bank": "Banking",
    "investment banking": "Banking",
    "commercial banking": "Banking",
    "retail banking": "Banking",
    # Infrastructure cluster
    "infrastructure": "Infrastructure",
    "construction": "Infrastructure",
    "engineering": "Infrastructure",
    "utilities": "Infrastructure",
    "power generation": "Power Generation",
    "electric utilities": "Power Generation",
    "power": "Power Generation",
    "electricity": "Power Generation",
    # Consumer cluster
    "consumer goods": "Consumer Goods",
    "consumer products": "Consumer Goods",
    "fmcg": "Consumer Goods",
    "retail": "Consumer Goods",
    "apparel": "Apparel",
    "fashion": "Apparel",
    "textiles": "Apparel",
    "clothing": "Apparel",
    "footwear": "Apparel",
    # Renewables cluster
    "renewable resources & alternative energy": "Renewable Resources & Alternative Energy",
    "renewable energy": "Renewable Resources & Alternative Energy",
    "renewables": "Renewable Resources & Alternative Energy",
    "solar": "Renewable Resources & Alternative Energy",
    "wind energy": "Renewable Resources & Alternative Energy",
    "clean energy": "Renewable Resources & Alternative Energy",
    "alternative energy": "Renewable Resources & Alternative Energy",
    # Technology cluster
    "technology": "Technology",
    "tech": "Technology",
    "software": "Technology",
    "it services": "Technology",
    "information technology": "Technology",
    "saas": "Technology",
    "hardware": "Technology",
    "semiconductors": "Technology",
    # Healthcare cluster
    "healthcare": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceuticals": "Healthcare",
    "biotech": "Healthcare",
    "medical devices": "Healthcare",
    "life sciences": "Healthcare",
    # Oil & Gas cluster
    "oil & gas": "Oil & Gas",
    "oil and gas": "Oil & Gas",
    "petroleum": "Oil & Gas",
    "upstream oil": "Oil & Gas",
    "downstream oil": "Oil & Gas",
    "fossil fuels": "Oil & Gas",
    # Mining cluster
    "mining & metals": "Mining & Metals",
    "mining": "Mining & Metals",
    "metals": "Mining & Metals",
    "steel": "Mining & Metals",
    "iron & steel": "Mining & Metals",
    # Real Estate cluster
    "real estate": "Real Estate",
    "property": "Real Estate",
    "reit": "Real Estate",
    "commercial real estate": "Real Estate",
    # Transportation cluster
    "transportation": "Transportation",
    "logistics": "Transportation",
    "shipping": "Transportation",
    "airlines": "Transportation",
    "aviation": "Transportation",
    # Automobiles cluster
    "automobiles": "Automobiles",
    "automotive": "Automobiles",
    "auto": "Automobiles",
    "vehicles": "Automobiles",
    "ev": "Automobiles",
    # Agriculture cluster
    "agriculture": "Agriculture",
    "farming": "Agriculture",
    "agribusiness": "Agriculture",
    "food production": "Agriculture",
    "food & beverage": "Agriculture",
    # Chemicals cluster
    "chemicals": "Chemicals",
    "chemical": "Chemicals",
    "specialty chemicals": "Chemicals",
    "petrochemicals": "Chemicals",
    # Telecommunications cluster
    "telecommunications": "Telecommunications",
    "telecom": "Telecommunications",
    "telco": "Telecommunications",
    "mobile services": "Telecommunications",
}

# Pre-build a lowercase lookup for the main map keys
_INDUSTRY_KEY_LOWER: dict[str, str] = {k.lower(): k for k in MATERIALITY_MAP}

# Pre-build a lowercase theme lookup
_THEME_KEY_LOWER: dict[str, str] = {t.lower(): t for t in _ALL_THEMES}

# Default weight when industry or theme is unknown
_DEFAULT_WEIGHT = 0.5


def _resolve_industry(industry: str) -> str | None:
    """Resolve an industry name to a canonical MATERIALITY_MAP key.

    Uses case-insensitive exact match first, then alias lookup, then
    substring matching as a last resort.

    Returns the canonical industry key or None if no match is found.
    """
    if not industry:
        return None

    industry_lower = industry.lower().strip()

    # 1. Direct match (case-insensitive)
    if industry_lower in _INDUSTRY_KEY_LOWER:
        return _INDUSTRY_KEY_LOWER[industry_lower]

    # 2. Alias lookup
    if industry_lower in _INDUSTRY_ALIASES:
        return _INDUSTRY_ALIASES[industry_lower]

    # 3. Word-boundary match — check if any alias appears as a whole word
    # BUG-21: Avoid greedy substring matching that causes false positives
    for alias, canonical in _INDUSTRY_ALIASES.items():
        if industry_lower == alias or f" {alias} " in f" {industry_lower} ":
            return canonical

    return None


def get_materiality_weight(industry: str, theme: str) -> float:
    """Look up the SASB-aligned materiality weight for an industry-theme pair.

    Args:
        industry: The company's industry name (case-insensitive, fuzzy matched).
        theme: The ESG theme name from the 21-theme taxonomy.

    Returns:
        A float between 0.0 and 1.0 representing materiality weight.
        Falls back to 0.5 (moderate) if industry or theme is not found.
    """
    if not industry or not theme:
        return _DEFAULT_WEIGHT

    # Resolve industry to canonical key
    canonical_industry = _resolve_industry(industry)
    if canonical_industry is None:
        logger.debug(
            "materiality_industry_not_found",
            industry=industry,
            fallback_weight=_DEFAULT_WEIGHT,
        )
        return _DEFAULT_WEIGHT

    industry_weights = MATERIALITY_MAP[canonical_industry]

    # Resolve theme (case-insensitive)
    theme_lower = theme.lower().strip()
    canonical_theme = _THEME_KEY_LOWER.get(theme_lower)
    if canonical_theme is None:
        logger.debug(
            "materiality_theme_not_found",
            theme=theme,
            industry=canonical_industry,
            fallback_weight=_DEFAULT_WEIGHT,
        )
        return _DEFAULT_WEIGHT

    return industry_weights.get(canonical_theme, _DEFAULT_WEIGHT)


def apply_materiality_adjustment(base_score: float, industry: str, theme: str) -> float:
    """Apply SASB-aligned materiality adjustment to a base relevance score.

    Adjusts the score based on how material the ESG theme is for the
    company's industry:
        - weight >= 0.8: score unchanged (highly material to this industry)
        - weight 0.4 - 0.79: score * 0.85 (slight reduction, moderately material)
        - weight < 0.4: score * 0.6 (significant reduction, low materiality)

    Args:
        base_score: The original relevance score (0-100 scale).
        industry: The company's industry name.
        theme: The ESG theme from the 21-theme taxonomy.

    Returns:
        Adjusted relevance score as a float.
    """
    weight = get_materiality_weight(industry, theme)

    if weight >= 0.8:
        adjusted = base_score
    elif weight >= 0.4:
        adjusted = base_score * 0.85
    else:
        adjusted = base_score * 0.6

    logger.debug(
        "materiality_adjustment_applied",
        industry=industry,
        theme=theme,
        weight=weight,
        base_score=round(base_score, 2),
        adjusted_score=round(adjusted, 2),
    )

    return round(adjusted, 2)
