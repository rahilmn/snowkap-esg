"""ESG Theme Tagging & Metatag Taxonomy (Module 3 — Snowkap ESG v2.0).

Assigns structured ESG themes to articles using a 21-theme taxonomy across
3 pillars (Environmental, Social, Governance). Each article receives 1 primary
theme + up to 3 secondary themes, each with sub-metric tags.

Tags are additive: a factory water spill affecting workers gets
Primary: Water; Secondary: Health & Safety, Community Impact, Ethics & Compliance.
"""

import json
from dataclasses import dataclass, field

import structlog

from backend.core import llm

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Full 21-Theme Taxonomy
# ---------------------------------------------------------------------------

ESG_TAXONOMY: dict[str, dict[str, list[str]]] = {
    "Environmental": {
        "Energy": [
            "renewable_energy_use",
            "energy_intensity",
            "fossil_fuel_dependency",
            "energy_efficiency_programs",
            "power_purchase_agreements",
        ],
        "Emissions": [
            "scope_1_direct",
            "scope_2_indirect",
            "scope_3_value_chain",
            "ghg_reduction_targets",
            "carbon_offset_credits",
            "methane_emissions",
        ],
        "Water": [
            "water_withdrawal",
            "water_consumption",
            "wastewater_discharge",
            "water_stress_regions",
            "water_recycling_rate",
        ],
        "Biodiversity": [
            "habitat_protection",
            "species_impact",
            "deforestation",
            "ecosystem_restoration",
            "biodiversity_offsets",
        ],
        "Waste & Circularity": [
            "hazardous_waste",
            "non_hazardous_waste",
            "recycling_rate",
            "circular_economy_initiatives",
            "plastic_reduction",
            "e_waste_management",
        ],
        "Climate Adaptation": [
            "physical_risk_exposure",
            "transition_risk",
            "climate_scenario_analysis",
            "resilience_planning",
            "stranded_asset_risk",
        ],
        "Land Use": [
            "land_degradation",
            "soil_contamination",
            "mining_impact",
            "agricultural_practices",
            "urban_development_impact",
        ],
        "Air Quality": [
            "particulate_matter",
            "volatile_organic_compounds",
            "nox_sox_emissions",
            "indoor_air_quality",
            "ozone_depleting_substances",
        ],
    },
    "Social": {
        "Human Capital": [
            "employee_turnover",
            "training_development",
            "fair_wages",
            "workforce_engagement",
            "talent_retention",
        ],
        "Health & Safety": [
            "workplace_injuries",
            "fatality_rate",
            "occupational_disease",
            "safety_training_hours",
            "mental_health_programs",
        ],
        "Community Impact": [
            "local_employment",
            "community_investment",
            "displacement_resettlement",
            "indigenous_rights",
            "social_license_to_operate",
        ],
        "Supply Chain Labor": [
            "child_labor_risk",
            "forced_labor_risk",
            "living_wage_compliance",
            "supplier_audits",
            "modern_slavery_due_diligence",
        ],
        "Product Safety": [
            "product_recalls",
            "consumer_health_risk",
            "quality_management",
            "labeling_compliance",
            "data_privacy_breaches",
        ],
        "Access & Affordability": [
            "essential_service_access",
            "pricing_fairness",
            "underserved_populations",
            "digital_divide",
            "healthcare_access",
        ],
        "DEI": [
            "gender_pay_gap",
            "board_diversity",
            "workforce_diversity",
            "inclusion_programs",
            "disability_accommodation",
        ],
    },
    "Governance": {
        "Board & Leadership": [
            "board_independence",
            "board_diversity_governance",
            "ceo_pay_ratio",
            "succession_planning",
            "executive_compensation",
        ],
        "Ethics & Compliance": [
            "anti_corruption",
            "anti_bribery",
            "whistleblower_protection",
            "regulatory_fines",
            "code_of_conduct_violations",
        ],
        "Risk Management": [
            "enterprise_risk_framework",
            "cybersecurity_governance",
            "business_continuity",
            "insurance_coverage",
            "emerging_risk_identification",
        ],
        "Transparency & Disclosure": [
            "esg_reporting_quality",
            "third_party_assurance",
            "materiality_assessment",
            "stakeholder_engagement",
            "integrated_reporting",
        ],
        "Shareholder Rights": [
            "voting_rights",
            "proxy_access",
            "related_party_transactions",
            "takeover_defenses",
            "minority_shareholder_protection",
        ],
        "Tax Transparency": [
            "country_by_country_reporting",
            "tax_havens",
            "effective_tax_rate",
            "tax_strategy_disclosure",
            "transfer_pricing",
        ],
    },
}

# Flat lookup: theme_name → pillar
THEME_TO_PILLAR: dict[str, str] = {}
for _pillar, _themes in ESG_TAXONOMY.items():
    for _theme in _themes:
        THEME_TO_PILLAR[_theme] = _pillar

# All valid theme names
ALL_THEMES: set[str] = set(THEME_TO_PILLAR.keys())

# All valid sub-metrics per theme
THEME_SUB_METRICS: dict[str, set[str]] = {
    theme: set(subs)
    for pillar_themes in ESG_TAXONOMY.values()
    for theme, subs in pillar_themes.items()
}


# ---------------------------------------------------------------------------
# Result Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ESGThemeTags:
    """Structured ESG theme assignment for an article."""

    primary_theme: str
    primary_pillar: str
    primary_sub_metrics: list[str] = field(default_factory=list)
    secondary_themes: list[dict[str, str | list[str]]] = field(default_factory=list)
    confidence: float = 0.0
    method: str = "llm"  # "llm" or "keyword_fallback"

    def to_dict(self) -> dict:
        return {
            "primary_theme": self.primary_theme,
            "primary_pillar": self.primary_pillar,
            "primary_sub_metrics": self.primary_sub_metrics,
            "secondary_themes": self.secondary_themes,
            "confidence": self.confidence,
            "method": self.method,
        }


# ---------------------------------------------------------------------------
# Keyword → Theme Mapping (rule-based fallback)
# ---------------------------------------------------------------------------

_KEYWORD_THEME_MAP: dict[str, str] = {
    # Energy
    "renewable": "Energy", "solar": "Energy", "wind energy": "Energy",
    "fossil fuel": "Energy", "energy efficiency": "Energy", "power plant": "Energy",
    "coal": "Energy", "natural gas": "Energy", "electricity": "Energy",
    # Emissions
    "emission": "Emissions", "carbon": "Emissions", "greenhouse gas": "Emissions",
    "ghg": "Emissions", "scope 1": "Emissions", "scope 2": "Emissions",
    "scope 3": "Emissions", "net zero": "Emissions", "carbon neutral": "Emissions",
    "methane": "Emissions", "co2": "Emissions",
    # Water
    "water": "Water", "wastewater": "Water", "effluent": "Water",
    "water stress": "Water", "aquifer": "Water", "water pollution": "Water",
    # Biodiversity
    "biodiversity": "Biodiversity", "deforestation": "Biodiversity",
    "habitat": "Biodiversity", "species": "Biodiversity", "ecosystem": "Biodiversity",
    "wildlife": "Biodiversity", "forest": "Biodiversity",
    # Waste & Circularity
    "waste": "Waste & Circularity", "recycling": "Waste & Circularity",
    "circular economy": "Waste & Circularity", "plastic": "Waste & Circularity",
    "hazardous waste": "Waste & Circularity", "e-waste": "Waste & Circularity",
    "landfill": "Waste & Circularity",
    # Climate Adaptation
    "climate risk": "Climate Adaptation", "climate adaptation": "Climate Adaptation",
    "physical risk": "Climate Adaptation", "transition risk": "Climate Adaptation",
    "stranded asset": "Climate Adaptation", "climate scenario": "Climate Adaptation",
    "flood": "Climate Adaptation", "drought": "Climate Adaptation",
    # Land Use
    "land use": "Land Use", "soil": "Land Use", "mining": "Land Use",
    "contamination": "Land Use", "land degradation": "Land Use",
    # Air Quality
    "air quality": "Air Quality", "particulate": "Air Quality",
    "air pollution": "Air Quality", "smog": "Air Quality", "ozone": "Air Quality",
    # Human Capital
    "employee": "Human Capital", "workforce": "Human Capital", "talent": "Human Capital",
    "training": "Human Capital", "turnover": "Human Capital", "layoff": "Human Capital",
    "retrenchment": "Human Capital", "hiring": "Human Capital",
    # Health & Safety
    "workplace safety": "Health & Safety", "injury": "Health & Safety",
    "fatality": "Health & Safety", "occupational": "Health & Safety",
    "accident": "Health & Safety", "worker death": "Health & Safety",
    "mental health": "Health & Safety",
    # Community Impact
    "community": "Community Impact", "displacement": "Community Impact",
    "indigenous": "Community Impact", "local impact": "Community Impact",
    "social license": "Community Impact", "resettlement": "Community Impact",
    # Supply Chain Labor
    "child labor": "Supply Chain Labor", "forced labor": "Supply Chain Labor",
    "modern slavery": "Supply Chain Labor", "supplier audit": "Supply Chain Labor",
    "living wage": "Supply Chain Labor", "sweatshop": "Supply Chain Labor",
    # Product Safety
    "product recall": "Product Safety", "consumer safety": "Product Safety",
    "data breach": "Product Safety", "data privacy": "Product Safety",
    "quality defect": "Product Safety",
    # Access & Affordability
    "affordability": "Access & Affordability", "access to": "Access & Affordability",
    "underserved": "Access & Affordability", "digital divide": "Access & Affordability",
    # DEI
    "diversity": "DEI", "inclusion": "DEI", "gender pay": "DEI",
    "discrimination": "DEI", "equity": "DEI", "equal opportunity": "DEI",
    # Board & Leadership
    "board of directors": "Board & Leadership", "ceo compensation": "Board & Leadership",
    "executive pay": "Board & Leadership", "board independence": "Board & Leadership",
    "succession": "Board & Leadership",
    # Ethics & Compliance
    "corruption": "Ethics & Compliance", "bribery": "Ethics & Compliance",
    "whistleblower": "Ethics & Compliance", "regulatory fine": "Ethics & Compliance",
    "fraud": "Ethics & Compliance", "misconduct": "Ethics & Compliance",
    "compliance violation": "Ethics & Compliance", "penalty": "Ethics & Compliance",
    # Risk Management
    "cybersecurity": "Risk Management", "cyber attack": "Risk Management",
    "business continuity": "Risk Management", "risk management": "Risk Management",
    "enterprise risk": "Risk Management",
    # Transparency & Disclosure
    "esg reporting": "Transparency & Disclosure", "disclosure": "Transparency & Disclosure",
    "transparency": "Transparency & Disclosure", "assurance": "Transparency & Disclosure",
    "materiality": "Transparency & Disclosure",
    # Shareholder Rights
    "shareholder": "Shareholder Rights", "proxy": "Shareholder Rights",
    "voting rights": "Shareholder Rights", "takeover": "Shareholder Rights",
    "activist investor": "Shareholder Rights",
    # Tax Transparency
    "tax evasion": "Tax Transparency", "tax haven": "Tax Transparency",
    "transfer pricing": "Tax Transparency", "tax transparency": "Tax Transparency",
    "tax avoidance": "Tax Transparency",
}

# Pillar hint from existing esg_pillar field
_PILLAR_LETTER_MAP: dict[str, str] = {
    "E": "Environmental",
    "S": "Social",
    "G": "Governance",
}


# ---------------------------------------------------------------------------
# Rule-based fallback
# ---------------------------------------------------------------------------

def infer_themes_from_keywords(
    title: str,
    content: str,
    esg_pillar: str | None = None,
) -> ESGThemeTags | None:
    """Infer ESG themes from keyword matching when LLM is unavailable.

    Scans title and content for known keywords, tallies theme hits,
    and returns the top match as primary + up to 3 secondary themes.
    """
    text = f"{title} {content}".lower()
    theme_scores: dict[str, int] = {}

    for keyword, theme in _KEYWORD_THEME_MAP.items():
        count = text.count(keyword.lower())
        if count > 0:
            theme_scores[theme] = theme_scores.get(theme, 0) + count

    if not theme_scores:
        # If esg_pillar hint exists, pick a default theme from that pillar
        if esg_pillar:
            primary_pillar_name = _PILLAR_LETTER_MAP.get(
                esg_pillar.split("|")[0].strip(), ""
            )
            if primary_pillar_name and primary_pillar_name in ESG_TAXONOMY:
                default_theme = next(iter(ESG_TAXONOMY[primary_pillar_name]))
                return ESGThemeTags(
                    primary_theme=default_theme,
                    primary_pillar=primary_pillar_name,
                    primary_sub_metrics=[],
                    secondary_themes=[],
                    confidence=0.1,
                    method="keyword_fallback",
                )
        return None

    ranked = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)

    primary_theme = ranked[0][0]
    primary_pillar = THEME_TO_PILLAR.get(primary_theme, "Environmental")

    secondary_themes = []
    for theme, _score in ranked[1:4]:
        pillar = THEME_TO_PILLAR.get(theme, "")
        secondary_themes.append({
            "theme": theme,
            "pillar": pillar,
            "sub_metrics": [],
        })

    logger.info(
        "themes_inferred_from_keywords",
        primary=primary_theme,
        secondary_count=len(secondary_themes),
        total_keywords_matched=sum(theme_scores.values()),
    )

    return ESGThemeTags(
        primary_theme=primary_theme,
        primary_pillar=primary_pillar,
        primary_sub_metrics=[],
        secondary_themes=secondary_themes,
        confidence=min(0.6, ranked[0][1] * 0.1),
        method="keyword_fallback",
    )


# ---------------------------------------------------------------------------
# LLM-based tagging
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an ESG taxonomy classifier. You assign ESG themes to news articles
using the following 21-theme taxonomy organized in 3 pillars:

ENVIRONMENTAL (8 themes):
- Energy: renewable_energy_use, energy_intensity, fossil_fuel_dependency, energy_efficiency_programs, power_purchase_agreements
- Emissions: scope_1_direct, scope_2_indirect, scope_3_value_chain, ghg_reduction_targets, carbon_offset_credits, methane_emissions
- Water: water_withdrawal, water_consumption, wastewater_discharge, water_stress_regions, water_recycling_rate
- Biodiversity: habitat_protection, species_impact, deforestation, ecosystem_restoration, biodiversity_offsets
- Waste & Circularity: hazardous_waste, non_hazardous_waste, recycling_rate, circular_economy_initiatives, plastic_reduction, e_waste_management
- Climate Adaptation: physical_risk_exposure, transition_risk, climate_scenario_analysis, resilience_planning, stranded_asset_risk
- Land Use: land_degradation, soil_contamination, mining_impact, agricultural_practices, urban_development_impact
- Air Quality: particulate_matter, volatile_organic_compounds, nox_sox_emissions, indoor_air_quality, ozone_depleting_substances

SOCIAL (7 themes):
- Human Capital: employee_turnover, training_development, fair_wages, workforce_engagement, talent_retention
- Health & Safety: workplace_injuries, fatality_rate, occupational_disease, safety_training_hours, mental_health_programs
- Community Impact: local_employment, community_investment, displacement_resettlement, indigenous_rights, social_license_to_operate
- Supply Chain Labor: child_labor_risk, forced_labor_risk, living_wage_compliance, supplier_audits, modern_slavery_due_diligence
- Product Safety: product_recalls, consumer_health_risk, quality_management, labeling_compliance, data_privacy_breaches
- Access & Affordability: essential_service_access, pricing_fairness, underserved_populations, digital_divide, healthcare_access
- DEI: gender_pay_gap, board_diversity, workforce_diversity, inclusion_programs, disability_accommodation

GOVERNANCE (6 themes):
- Board & Leadership: board_independence, board_diversity_governance, ceo_pay_ratio, succession_planning, executive_compensation
- Ethics & Compliance: anti_corruption, anti_bribery, whistleblower_protection, regulatory_fines, code_of_conduct_violations
- Risk Management: enterprise_risk_framework, cybersecurity_governance, business_continuity, insurance_coverage, emerging_risk_identification
- Transparency & Disclosure: esg_reporting_quality, third_party_assurance, materiality_assessment, stakeholder_engagement, integrated_reporting
- Shareholder Rights: voting_rights, proxy_access, related_party_transactions, takeover_defenses, minority_shareholder_protection
- Tax Transparency: country_by_country_reporting, tax_havens, effective_tax_rate, tax_strategy_disclosure, transfer_pricing

RULES:
1. Every article gets exactly 1 primary theme
2. Up to 3 secondary themes (only if clearly relevant — do not force secondary themes)
3. Each theme assignment MUST include specific sub-metric tags from the lists above
4. Tags are additive across pillars (a factory water spill affecting workers gets Primary: Water; Secondary: Health & Safety, Community Impact, Ethics & Compliance)
5. Return ONLY valid JSON — no markdown, no explanation"""

_USER_PROMPT_TEMPLATE = """
═══════════════════════════════════════════════════════════════════
MANDATORY RULE — FINANCIAL SECTOR CLASSIFICATION
═══════════════════════════════════════════════════════════════════
For financial sector companies (banks, NBFCs, AMCs, insurance, fintech),
you MUST classify by the SUBJECT MATTER of the article, NOT the company's
own governance structure. Banks are NOT automatically "Governance".

Ask yourself: "What is this article ABOUT?" — then pick the theme that
matches the TOPIC, not the industry of the company.

REQUIRED EXAMPLES — memorize these patterns:
  • Green bond issuance for renewable projects → Energy (Environmental)
  • Climate risk disclosure pause by RBI → Climate Adaptation (Environmental)
  • Financed emissions or carbon footprint of loan portfolio → Emissions (Environmental)
  • ESG rating/scoring announcement → Transparency & Disclosure (Governance)
  • Financial inclusion or microfinance → Community Impact (Social)
  • Employee diversity or labor practices at a bank → DEI (Social) or Human Capital (Social)
  • Fraud, bribery, corruption at a bank → Ethics & Compliance (Governance)
  • Board changes, leadership appointments → Board & Leadership (Governance)
  • A bank's annual ESG report quality → Transparency & Disclosure (Governance)
  • Green lending / sustainable finance strategy → Energy (Environmental)
  • Water/pollution risk in financed projects → Water (Environmental)

WRONG (do NOT do this):
  ✗ "Bank issues green bond" → Transparency & Disclosure  (WRONG — it is about Energy)
  ✗ "RBI pauses climate disclosure norms" → Transparency & Disclosure  (WRONG — it is about Climate Adaptation)
  ✗ "Bank launches microfinance scheme" → Transparency & Disclosure  (WRONG — it is about Community Impact)
  ✗ "Bank employees face layoffs" → Transparency & Disclosure  (WRONG — it is about Human Capital)

Only use "Transparency & Disclosure" when the article is genuinely about
reporting quality, ESG disclosures, materiality assessments, or assurance.
═══════════════════════════════════════════════════════════════════

Now classify this article:

Title: {title}
Content: {content}

Context:
- Tracked company industry: {company_industry}
- ESG Pillar: {esg_pillar}
- Topics: {topics}

Return JSON in this exact format:
{{
  "primary_theme": "<theme name exactly as listed>",
  "primary_pillar": "<Environmental|Social|Governance>",
  "primary_sub_metrics": ["<sub_metric_tag_1>", "<sub_metric_tag_2>"],
  "secondary_themes": [
    {{
      "theme": "<theme name>",
      "pillar": "<Environmental|Social|Governance>",
      "sub_metrics": ["<sub_metric_tag_1>"]
    }}
  ],
  "confidence": <0.0-1.0>
}}"""


def _validate_theme(theme: str) -> str | None:
    """Return the theme name if valid, else None."""
    if theme in ALL_THEMES:
        return theme
    # Try case-insensitive match
    for valid_theme in ALL_THEMES:
        if valid_theme.lower() == theme.lower():
            return valid_theme
    return None


def _validate_sub_metrics(theme: str, sub_metrics: list) -> list[str]:
    """Filter sub-metrics to only valid ones for the given theme."""
    valid = THEME_SUB_METRICS.get(theme, set())
    return [sm for sm in sub_metrics if isinstance(sm, str) and sm in valid]


async def tag_esg_themes(
    title: str,
    content: str,
    esg_pillar: str | None = None,
    topics: list[str] | None = None,
    company_industry: str | None = None,
) -> ESGThemeTags | None:
    """Assign ESG themes to an article using LLM classification.

    Args:
        title: Article headline.
        content: Article body text (truncated to 2000 chars internally).
        esg_pillar: Existing pillar classification (e.g. "E", "S", "G", "E|S").
        topics: Existing topic tags from earlier pipeline stages.

    Returns:
        ESGThemeTags with primary + secondary themes, or None on failure.
        Falls back to keyword-based inference if LLM is unavailable.
    """
    if not title and not content:
        logger.warning("theme_tagging_skipped", reason="empty_input")
        return None

    # Fall back to keywords if LLM not configured
    if not llm.is_configured():
        logger.info("theme_tagging_keyword_fallback", reason="llm_not_configured")
        return infer_themes_from_keywords(title, content, esg_pillar)

    # Truncate content for prompt efficiency
    truncated_content = content[:2000] if content else ""
    topics_str = ", ".join(topics[:10]) if topics else "none"

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        title=title,
        content=truncated_content,
        esg_pillar=esg_pillar or "unknown",
        topics=topics_str,
        company_industry=company_industry or "not specified",
    )

    try:
        raw = await llm.chat(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=500,
            model="gpt-4o",
        )
        raw = raw.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)

        if not isinstance(data, dict) or "primary_theme" not in data:
            logger.warning("theme_tagging_invalid_response", raw=raw[:200])
            return infer_themes_from_keywords(title, content, esg_pillar)

        # Validate primary theme
        primary = _validate_theme(data.get("primary_theme", ""))
        if not primary:
            logger.warning(
                "theme_tagging_invalid_primary",
                received=data.get("primary_theme"),
            )
            return infer_themes_from_keywords(title, content, esg_pillar)

        primary_pillar = THEME_TO_PILLAR.get(primary, "Environmental")
        primary_sub_metrics = _validate_sub_metrics(
            primary, data.get("primary_sub_metrics", [])
        )

        # Validate secondary themes
        secondary_themes: list[dict[str, str | list[str]]] = []
        for sec in data.get("secondary_themes", [])[:3]:
            if not isinstance(sec, dict):
                continue
            sec_theme = _validate_theme(sec.get("theme", ""))
            if not sec_theme or sec_theme == primary:
                continue
            sec_pillar = THEME_TO_PILLAR.get(sec_theme, "")
            sec_sub_metrics = _validate_sub_metrics(
                sec_theme, sec.get("sub_metrics", [])
            )
            secondary_themes.append({
                "theme": sec_theme,
                "pillar": sec_pillar,
                "sub_metrics": sec_sub_metrics,
            })

        confidence = float(data.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))

        result = ESGThemeTags(
            primary_theme=primary,
            primary_pillar=primary_pillar,
            primary_sub_metrics=primary_sub_metrics,
            secondary_themes=secondary_themes,
            confidence=confidence,
            method="llm",
        )

        logger.info(
            "esg_themes_tagged",
            article=title[:60],
            primary=primary,
            primary_pillar=primary_pillar,
            secondary_count=len(secondary_themes),
            confidence=confidence,
        )

        return result

    except json.JSONDecodeError as e:
        logger.error("theme_tagging_json_error", error=str(e), raw=raw[:200])
        return infer_themes_from_keywords(title, content, esg_pillar)
    except Exception as e:
        logger.error("theme_tagging_failed", error=str(e))
        return infer_themes_from_keywords(title, content, esg_pillar)
