"""Framework RAG Knowledge Bases — Module 4 (v2.0).

Retrieval-Augmented Generation knowledge bases for 13 ESG/sustainability
reporting frameworks.  This is predominantly a **knowledge-base** module:
the bulk of the logic lives in structured dicts that map framework sections
to retrieval triggers, metrics, and compliance implications.

The LLM (via ``backend.core.llm``) is invoked only as a fallback when
rule-based keyword/theme matching does not yield sufficient provisions.

Frameworks covered:
 1. TCFD          2. ISSB (IFRS S1/S2)  3. CSRD/ESRS       4. EU Taxonomy
 5. GRI           6. SASB               7. SFDR             8. GHG Protocol
 9. SBTi         10. TNFD              11. CDP             12. SEC Climate Rules
13. BRSR
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core import llm

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Region-based framework relevance boosts
# ---------------------------------------------------------------------------

REGION_FRAMEWORK_BOOST: dict[str, dict[str, float]] = {
    # Keys normalised to uppercase — matches company.headquarter_region values (INDIA, EU, US, UK, APAC, OTHER)
    "INDIA": {"BRSR": 0.6, "SEBI": 0.6, "GRI": 0.1, "CDP": 0.1, "TCFD": 0.1},
    "APAC": {"BRSR": 0.4, "GRI": 0.1, "CDP": 0.1, "TCFD": 0.1},
    "EU": {"CSRD_ESRS": 0.6, "ESRS": 0.6, "EU_TAXONOMY": 0.6, "SFDR": 0.4, "GRI": 0.1, "TCFD": 0.1},
    "US": {"SEC_CLIMATE": 0.6, "SEC": 0.6, "SASB": 0.4, "GRI": 0.1, "TCFD": 0.1},
    "UK": {"TCFD": 0.4, "GRI": 0.2, "SASB": 0.1, "CDP": 0.1},
    # Legacy aliases (case-insensitive lookup below handles these)
    "Asia-Pacific": {"BRSR": 0.4, "GRI": 0.1, "CDP": 0.1, "TCFD": 0.1},
    "India": {"BRSR": 0.6, "SEBI": 0.6, "GRI": 0.1, "CDP": 0.1, "TCFD": 0.1},
    "Europe": {"CSRD_ESRS": 0.6, "ESRS": 0.6, "EU_TAXONOMY": 0.6, "SFDR": 0.4, "GRI": 0.1, "TCFD": 0.1},
    "North America": {"SEC_CLIMATE": 0.6, "SEC": 0.6, "SASB": 0.4, "GRI": 0.1, "TCFD": 0.1},
}

# Frameworks that get PENALIZED when shown to wrong region
REGION_FRAMEWORK_PENALTY: dict[str, dict[str, float]] = {
    "INDIA": {"SEC_CLIMATE": -0.2, "SEC": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15, "SFDR": -0.15},
    "APAC": {"SEC_CLIMATE": -0.2, "SEC": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15, "SFDR": -0.15},
    "EU": {"BRSR": -0.2, "SEBI": -0.2, "SEC_CLIMATE": -0.15},
    "US": {"BRSR": -0.2, "SEBI": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15},
    "UK": {"BRSR": -0.2, "SEBI": -0.2, "CSRD_ESRS": -0.1},
    # Legacy aliases
    "Asia-Pacific": {"SEC_CLIMATE": -0.2, "SEC": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15, "SFDR": -0.15},
    "India": {"SEC_CLIMATE": -0.2, "SEC": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15, "SFDR": -0.15},
    "Europe": {"BRSR": -0.2, "SEBI": -0.2, "SEC_CLIMATE": -0.15},
    "North America": {"BRSR": -0.2, "SEBI": -0.2, "CSRD_ESRS": -0.15, "EU_TAXONOMY": -0.15},
}

# Global frameworks always get a small boost
GLOBAL_FRAMEWORKS: dict[str, float] = {
    "TCFD": 0.1, "GRI": 0.1, "CDP": 0.1, "ISSB": 0.1,
    "GHG_PROTOCOL": 0.05, "SBTi": 0.05, "TNFD": 0.05,
}

# Framework → profitability consequence mapping
FRAMEWORK_PROFITABILITY: dict[str, str] = {
    "BRSR": "Non-disclosure → SEBI scrutiny → potential trading restrictions → liquidity risk + ESG rating downgrade → cost of capital +20-40bps",
    "CSRD_ESRS": "Non-compliance → EU market access restriction → revenue loss from EU operations + fines up to 2% of global turnover",
    "TCFD": "Non-disclosure → institutional investor exclusion → FII outflow → P/E compression 5-10%",
    "GRI": "Incomplete reporting → ESG fund screening exclusion → reduced institutional ownership",
    "SASB": "Non-alignment → poor MSCI/Sustainalytics rating → index exclusion → passive fund outflow",
    "CDP": "Non-response → climate leadership exclusion → lost procurement contracts from CDP-mandating buyers",
    "SEC_CLIMATE": "Non-compliance → SEC enforcement action → litigation risk + investor lawsuits",
    "SFDR": "Non-alignment → EU fund managers cannot invest → capital access restriction",
    "EU_TAXONOMY": "Non-alignment → green bond ineligibility → higher borrowing costs",
    "ISSB": "Non-alignment → global investor benchmarking exclusion → reduced cross-border capital access",
    "GHG_PROTOCOL": "Non-adoption → emissions data not credible → carbon credit rejection + supply chain exclusion",
    "SBTi": "No validated target → net-zero credibility gap → greenwashing accusation risk",
    "TNFD": "Non-disclosure → nature-related risk opacity → biodiversity-sensitive investor exclusion",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FrameworkMatch:
    """A matched framework with specific section-level citations."""

    framework_id: str
    framework_name: str
    triggered_sections: list[str] = field(default_factory=list)
    triggered_questions: list[str] = field(default_factory=list)  # question-level (e.g. Q14, Q15)
    compliance_implications: list[str] = field(default_factory=list)
    cross_industry_metrics: list[str] = field(default_factory=list)  # TCFD cross-industry
    relevance_score: float = 0.0  # 0-1 confidence of the match
    alignment_notes: list[str] = field(default_factory=list)
    profitability_link: str = ""  # profitability consequence chain
    is_mandatory: bool = False  # whether framework is mandatory for the company

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework_id": self.framework_id,
            "framework_name": self.framework_name,
            "triggered_sections": self.triggered_sections,
            "triggered_questions": self.triggered_questions,
            "compliance_implications": self.compliance_implications,
            "cross_industry_metrics": self.cross_industry_metrics,
            "relevance_score": self.relevance_score,
            "alignment_notes": self.alignment_notes,
            "profitability_link": self.profitability_link,
            "is_mandatory": self.is_mandatory,
        }


# ---------------------------------------------------------------------------
# FRAMEWORK KNOWLEDGE BASES — 13 frameworks
# ---------------------------------------------------------------------------
# Each framework entry has:
#   name            — display name
#   provisions      — dict of section_code → {title, description, metrics, triggers}
#   retrieval_triggers — list of keyword/theme patterns that activate this framework
#   cross_references — other frameworks that share coverage

FrameworkKB = dict[str, Any]

FRAMEWORK_KNOWLEDGE: dict[str, FrameworkKB] = {

    # -----------------------------------------------------------------------
    # 1. TCFD — Task Force on Climate-related Financial Disclosures
    # -----------------------------------------------------------------------
    "TCFD": {
        "name": "Task Force on Climate-related Financial Disclosures",
        "provisions": {
            "TCFD:Governance": {
                "title": "Governance",
                "description": "Board oversight and management role in climate-related risks/opportunities.",
                "metrics": [
                    "Board climate competency",
                    "Management climate committees",
                    "Climate risk escalation process",
                ],
                "triggers": ["board", "governance", "oversight", "director", "committee"],
            },
            "TCFD:Strategy": {
                "title": "Strategy",
                "description": "Climate-related risks/opportunities and their impact on business, strategy, and financial planning.",
                "metrics": [
                    "Scenario analysis (1.5C, 2C, 4C)",
                    "Transition risks identified",
                    "Physical risks identified",
                    "Climate opportunities",
                    "Strategic resilience assessment",
                ],
                "triggers": ["strategy", "scenario", "transition risk", "physical risk",
                             "climate opportunity", "resilience", "stranded asset"],
            },
            "TCFD:RiskManagement": {
                "title": "Risk Management",
                "description": "Processes for identifying, assessing, and managing climate-related risks.",
                "metrics": [
                    "Climate risk identification process",
                    "Climate risk assessment methodology",
                    "Integration with enterprise risk management",
                ],
                "triggers": ["risk management", "risk assessment", "risk identification",
                             "enterprise risk", "climate risk"],
            },
            "TCFD:Metrics": {
                "title": "Metrics and Targets",
                "description": "Metrics and targets to assess and manage climate-related risks/opportunities.",
                "metrics": [
                    "GHG emissions (Scope 1, 2, 3)",
                    "Climate-related targets",
                    "Progress against targets",
                    "Internal carbon price",
                    "Climate-related remuneration",
                ],
                "triggers": ["emissions", "scope 1", "scope 2", "scope 3", "carbon",
                             "ghg", "target", "net zero", "carbon price"],
            },
        },
        "cross_industry_metrics": [
            "GHG Emissions (absolute Scope 1, Scope 2, Scope 3)",
            "Transition Risks — policy/legal, technology, market, reputation",
            "Physical Risks — acute (cyclones, floods) and chronic (sea-level, heat stress)",
            "Climate-Related Opportunities — resource efficiency, energy source, products/services, markets, resilience",
            "Capital Deployment — R&D in climate-related products/low-carbon tech",
            "Internal Carbon Prices",
            "Climate-related remuneration as % of executive pay",
        ],
        "retrieval_triggers": [
            "climate", "tcfd", "carbon", "emissions", "ghg", "net zero",
            "scenario analysis", "transition risk", "physical risk",
            "stranded asset", "decarbonisation", "decarbonization",
        ],
        "cross_references": ["ISSB", "CSRD_ESRS", "CDP", "GHG_PROTOCOL"],
    },

    # -----------------------------------------------------------------------
    # 2. ISSB (IFRS S1 & S2)
    # -----------------------------------------------------------------------
    "ISSB": {
        "name": "ISSB — IFRS S1 (General) & S2 (Climate)",
        "provisions": {
            "IFRS_S1:General": {
                "title": "IFRS S1 — General Requirements for Sustainability-related Financial Disclosures",
                "description": "Materiality-based sustainability disclosures for investor decision-making.",
                "metrics": [
                    "Sustainability-related risks and opportunities",
                    "Governance of sustainability risks",
                    "Strategy for sustainability matters",
                    "Risk management processes",
                    "Metrics and targets",
                ],
                "triggers": ["ifrs s1", "issb", "sustainability disclosure",
                             "investor decision", "financial materiality"],
            },
            "IFRS_S2:Climate": {
                "title": "IFRS S2 — Climate-related Disclosures",
                "description": "Climate-specific disclosures building on TCFD pillars; Scope 1/2/3 mandatory.",
                "metrics": [
                    "Scope 1 GHG emissions (mandatory)",
                    "Scope 2 GHG emissions (mandatory)",
                    "Scope 3 GHG emissions (mandatory, phased)",
                    "Climate-related transition plan",
                    "Carbon offsets/credits disclosure",
                    "Internal carbon price",
                    "Climate-related targets (absolute + intensity)",
                ],
                "triggers": ["ifrs s2", "climate disclosure", "scope 3 mandatory",
                             "transition plan", "carbon offset", "climate target"],
            },
            "IFRS_S2:IndustryMetrics": {
                "title": "IFRS S2 — Industry-based Metrics",
                "description": "SASB-derived industry-specific climate metrics (Appendix B).",
                "metrics": [
                    "Industry-specific GHG metrics",
                    "Energy management metrics",
                    "Physical risk exposure by industry",
                ],
                "triggers": ["industry metrics", "sasb industry", "sector climate"],
            },
        },
        "retrieval_triggers": [
            "issb", "ifrs s1", "ifrs s2", "sustainability standard",
            "investor disclosure", "financial materiality", "climate disclosure",
        ],
        "cross_references": ["TCFD", "SASB", "GHG_PROTOCOL"],
    },

    # -----------------------------------------------------------------------
    # 3. CSRD / ESRS
    # -----------------------------------------------------------------------
    "CSRD_ESRS": {
        "name": "CSRD — European Sustainability Reporting Standards (ESRS)",
        "provisions": {
            "ESRS:E1": {
                "title": "E1 — Climate Change",
                "description": "GHG emissions, energy consumption, climate adaptation/mitigation policies.",
                "metrics": [
                    "Scope 1/2/3 GHG emissions",
                    "Energy consumption (total, renewable %)",
                    "GHG intensity per revenue",
                    "Climate transition plan",
                    "Carbon removal/offset strategy",
                ],
                "triggers": ["climate change", "emissions", "energy", "carbon",
                             "ghg", "decarbonisation"],
            },
            "ESRS:E2": {
                "title": "E2 — Pollution",
                "description": "Air, water, soil pollution; substances of concern.",
                "metrics": [
                    "Pollutant emissions (air/water/soil)",
                    "Substances of concern (SVHC)",
                    "Pollution prevention measures",
                ],
                "triggers": ["pollution", "pollutant", "contamination", "toxic",
                             "svhc", "air quality"],
            },
            "ESRS:E3": {
                "title": "E3 — Water and Marine Resources",
                "description": "Water consumption, discharge, marine ecosystem impact.",
                "metrics": [
                    "Water withdrawal by source",
                    "Water consumption",
                    "Water discharge quality",
                    "Marine resource impact",
                ],
                "triggers": ["water", "marine", "ocean", "aquatic", "water stress",
                             "water scarcity"],
            },
            "ESRS:E4": {
                "title": "E4 — Biodiversity and Ecosystems",
                "description": "Impact on biodiversity, land use, ecosystem services.",
                "metrics": [
                    "Sites near sensitive areas",
                    "Land use change",
                    "Ecosystem degradation",
                    "Species impact assessment",
                ],
                "triggers": ["biodiversity", "ecosystem", "species", "habitat",
                             "deforestation", "land use"],
            },
            "ESRS:E5": {
                "title": "E5 — Resource Use and Circular Economy",
                "description": "Material inflows/outflows, waste, circular economy practices.",
                "metrics": [
                    "Resource inflows (virgin vs recycled)",
                    "Waste generation by type",
                    "Recycling rate",
                    "Circular economy revenue share",
                ],
                "triggers": ["waste", "circular economy", "recycling", "resource use",
                             "material", "packaging"],
            },
            "ESRS:S1": {
                "title": "S1 — Own Workforce",
                "description": "Working conditions, equal treatment, health & safety of own employees.",
                "metrics": [
                    "Gender pay gap",
                    "Training hours per employee",
                    "Work-related injuries/fatalities",
                    "Living wage coverage",
                    "Collective bargaining coverage",
                ],
                "triggers": ["employee", "workforce", "worker", "labor", "labour",
                             "safety", "health", "diversity", "inclusion", "wage",
                             "working condition"],
            },
            "ESRS:S2": {
                "title": "S2 — Workers in the Value Chain",
                "description": "Working conditions of supply chain/value chain workers.",
                "metrics": [
                    "Supply chain worker rights assessment",
                    "Forced labor/child labor screening",
                    "Value chain due diligence",
                ],
                "triggers": ["supply chain", "value chain", "supplier", "vendor",
                             "forced labor", "child labor"],
            },
            "ESRS:S3": {
                "title": "S3 — Affected Communities",
                "description": "Impacts on local/indigenous communities.",
                "metrics": [
                    "Community engagement processes",
                    "FPIC (Free Prior Informed Consent)",
                    "Land rights disputes",
                    "Community investment",
                ],
                "triggers": ["community", "indigenous", "local community",
                             "displacement", "resettlement", "social license"],
            },
            "ESRS:S4": {
                "title": "S4 — Consumers and End-users",
                "description": "Product safety, data privacy, responsible marketing.",
                "metrics": [
                    "Product recall rate",
                    "Data privacy incidents",
                    "Responsible marketing compliance",
                    "Consumer health & safety incidents",
                ],
                "triggers": ["consumer", "customer", "product safety", "data privacy",
                             "privacy", "marketing"],
            },
            "ESRS:G1": {
                "title": "G1 — Business Conduct",
                "description": "Corruption, lobbying, political engagement, whistleblowing, payment practices.",
                "metrics": [
                    "Anti-corruption training coverage",
                    "Confirmed corruption incidents",
                    "Lobbying expenditure",
                    "Whistleblowing cases",
                    "Payment practices (avg days to pay suppliers)",
                ],
                "triggers": ["corruption", "bribery", "ethics", "whistleblower",
                             "lobbying", "political", "governance", "compliance"],
            },
            "ESRS:DoubleMateriality": {
                "title": "Double Materiality Assessment",
                "description": "Impact materiality (inside-out) + financial materiality (outside-in).",
                "metrics": [
                    "Impact materiality assessment",
                    "Financial materiality assessment",
                    "Stakeholder engagement for materiality",
                ],
                "triggers": ["double materiality", "materiality assessment",
                             "impact materiality", "financial materiality"],
            },
        },
        "retrieval_triggers": [
            "csrd", "esrs", "european sustainability", "double materiality",
            "eu reporting", "european regulation", "nfrd",
        ],
        "cross_references": ["TCFD", "GRI", "ISSB", "EU_TAXONOMY"],
    },

    # -----------------------------------------------------------------------
    # 4. EU Taxonomy
    # -----------------------------------------------------------------------
    "EU_TAXONOMY": {
        "name": "EU Taxonomy for Sustainable Activities",
        "provisions": {
            "EUT:CCM": {
                "title": "Climate Change Mitigation",
                "description": "Substantial contribution to reducing GHG emissions or enhancing carbon sinks.",
                "metrics": [
                    "Taxonomy-eligible revenue %",
                    "Taxonomy-aligned revenue %",
                    "CapEx alignment %",
                    "OpEx alignment %",
                ],
                "triggers": ["climate mitigation", "taxonomy eligible", "taxonomy aligned",
                             "low carbon", "carbon sink"],
            },
            "EUT:CCA": {
                "title": "Climate Change Adaptation",
                "description": "Reducing vulnerability to physical climate risks.",
                "metrics": [
                    "Adaptation solutions revenue",
                    "Physical risk reduction measures",
                ],
                "triggers": ["climate adaptation", "resilience", "physical risk reduction"],
            },
            "EUT:WMR": {
                "title": "Sustainable Use and Protection of Water and Marine Resources",
                "description": "Water efficiency, pollution prevention in water bodies.",
                "metrics": ["Water use efficiency", "Marine ecosystem protection"],
                "triggers": ["water protection", "marine resources", "water efficiency"],
            },
            "EUT:CE": {
                "title": "Transition to a Circular Economy",
                "description": "Material recovery, waste minimisation, circular product design.",
                "metrics": ["Circular design %", "Waste recovery rate"],
                "triggers": ["circular economy", "waste reduction", "material recovery"],
            },
            "EUT:PP": {
                "title": "Pollution Prevention and Control",
                "description": "Preventing/reducing pollution to air, water, soil.",
                "metrics": ["Pollution reduction targets", "Remediation expenditure"],
                "triggers": ["pollution prevention", "remediation", "contamination control"],
            },
            "EUT:BIO": {
                "title": "Protection and Restoration of Biodiversity and Ecosystems",
                "description": "Conservation, restoration, sustainable land management.",
                "metrics": ["Biodiversity offset", "Restoration area (ha)"],
                "triggers": ["biodiversity restoration", "ecosystem protection", "conservation"],
            },
            "EUT:DNSH": {
                "title": "Do No Significant Harm (DNSH) Criteria",
                "description": "Activity must not significantly harm any of the other 5 environmental objectives.",
                "metrics": [
                    "DNSH screening per objective",
                    "Minimum safeguards compliance (OECD Guidelines, UNGPs, ILO)",
                ],
                "triggers": ["dnsh", "do no significant harm", "minimum safeguards"],
            },
        },
        "retrieval_triggers": [
            "eu taxonomy", "taxonomy regulation", "taxonomy eligible",
            "taxonomy aligned", "dnsh", "green activity", "sustainable finance",
        ],
        "cross_references": ["CSRD_ESRS", "SFDR"],
    },

    # -----------------------------------------------------------------------
    # 5. GRI — Global Reporting Initiative
    # -----------------------------------------------------------------------
    "GRI": {
        "name": "Global Reporting Initiative Standards",
        "provisions": {
            # Universal Standards
            "GRI:1": {
                "title": "GRI 1 — Foundation 2021",
                "description": "Fundamental principles for sustainability reporting.",
                "metrics": ["Reporting principles applied", "Report boundary"],
                "triggers": ["gri foundation", "reporting principles"],
            },
            "GRI:2": {
                "title": "GRI 2 — General Disclosures 2021",
                "description": "Organizational profile, governance, strategy, stakeholder engagement.",
                "metrics": [
                    "Governance structure",
                    "Stakeholder engagement process",
                    "Highest governance body composition",
                ],
                "triggers": ["general disclosure", "organizational profile",
                             "stakeholder engagement"],
            },
            "GRI:3": {
                "title": "GRI 3 — Material Topics 2021",
                "description": "Process to determine material topics and manage their impacts.",
                "metrics": ["Material topics list", "Topic boundary"],
                "triggers": ["material topic", "materiality"],
            },
            # Economic (200 series)
            "GRI:201": {
                "title": "GRI 201 — Economic Performance",
                "description": "Direct economic value generated and distributed.",
                "metrics": ["Revenue", "Operating costs", "Employee compensation",
                            "Community investment", "Economic value retained"],
                "triggers": ["economic performance", "revenue distribution",
                             "economic value"],
            },
            "GRI:205": {
                "title": "GRI 205 — Anti-corruption",
                "description": "Corruption risk assessment, training, confirmed incidents.",
                "metrics": ["Corruption risk assessments", "Anti-corruption training %",
                            "Confirmed corruption incidents"],
                "triggers": ["corruption", "bribery", "anti-corruption"],
            },
            "GRI:207": {
                "title": "GRI 207 — Tax",
                "description": "Tax strategy, governance, country-by-country reporting.",
                "metrics": ["Tax paid by jurisdiction", "Tax governance framework"],
                "triggers": ["tax", "tax transparency", "country-by-country"],
            },
            # Environmental (300 series)
            "GRI:302": {
                "title": "GRI 302 — Energy",
                "description": "Energy consumption, intensity, reduction initiatives.",
                "metrics": ["Total energy consumption (GJ)", "Energy intensity",
                            "Renewable energy %", "Energy reduction achieved"],
                "triggers": ["energy consumption", "energy intensity", "renewable energy"],
            },
            "GRI:303": {
                "title": "GRI 303 — Water and Effluents",
                "description": "Water withdrawal, consumption, discharge, water-stressed areas.",
                "metrics": ["Water withdrawal by source", "Water recycled %",
                            "Water discharge quality", "Operations in water-stressed areas"],
                "triggers": ["water", "effluent", "water withdrawal", "water stress"],
            },
            "GRI:304": {
                "title": "GRI 304 — Biodiversity",
                "description": "Operations in/near protected areas, habitat impacts.",
                "metrics": ["Sites near protected areas", "Habitat restored",
                            "IUCN Red List species affected"],
                "triggers": ["biodiversity", "protected area", "habitat", "species"],
            },
            "GRI:305": {
                "title": "GRI 305 — Emissions",
                "description": "Scope 1/2/3 GHG emissions, intensity, reduction.",
                "metrics": ["Scope 1 (tCO2e)", "Scope 2 (tCO2e)", "Scope 3 (tCO2e)",
                            "GHG intensity", "Emissions reduction achieved"],
                "triggers": ["emissions", "ghg", "scope 1", "scope 2", "scope 3",
                             "carbon", "greenhouse gas"],
            },
            "GRI:306": {
                "title": "GRI 306 — Waste",
                "description": "Waste generation, diversion, disposal.",
                "metrics": ["Total waste (tonnes)", "Hazardous waste",
                            "Waste diverted from disposal %"],
                "triggers": ["waste", "hazardous waste", "waste disposal",
                             "waste diversion"],
            },
            "GRI:308": {
                "title": "GRI 308 — Supplier Environmental Assessment",
                "description": "Environmental screening of new and existing suppliers.",
                "metrics": ["New suppliers screened %",
                            "Suppliers with significant environmental impacts"],
                "triggers": ["supplier environmental", "supplier screening",
                             "supply chain environment"],
            },
            # Social (400 series)
            "GRI:401": {
                "title": "GRI 401 — Employment",
                "description": "New hires, turnover, benefits.",
                "metrics": ["New hire rate", "Turnover rate",
                            "Parental leave return rate"],
                "triggers": ["employment", "hiring", "turnover", "attrition",
                             "parental leave"],
            },
            "GRI:403": {
                "title": "GRI 403 — Occupational Health and Safety",
                "description": "Work-related injuries, fatalities, OHS management.",
                "metrics": ["LTIFR", "Fatalities", "TRIR",
                            "OHS management system coverage"],
                "triggers": ["safety", "health", "injury", "fatality", "occupational",
                             "ltifr", "trir"],
            },
            "GRI:405": {
                "title": "GRI 405 — Diversity and Equal Opportunity",
                "description": "Diversity of governance bodies and employees.",
                "metrics": ["Board diversity (gender/age)", "Gender pay ratio",
                            "Employee diversity breakdown"],
                "triggers": ["diversity", "equal opportunity", "gender",
                             "inclusion", "pay gap"],
            },
            "GRI:413": {
                "title": "GRI 413 — Local Communities",
                "description": "Community engagement, impact assessments, development programs.",
                "metrics": ["Community engagement programs",
                            "Operations with community impact assessments",
                            "CSR spend"],
                "triggers": ["community", "csr", "social impact", "local community"],
            },
            "GRI:414": {
                "title": "GRI 414 — Supplier Social Assessment",
                "description": "Social screening of suppliers.",
                "metrics": ["New suppliers screened on social criteria %",
                            "Suppliers with significant social impacts"],
                "triggers": ["supplier social", "supply chain labor",
                             "supplier assessment"],
            },
            "GRI:418": {
                "title": "GRI 418 — Customer Privacy",
                "description": "Complaints regarding data privacy breaches.",
                "metrics": ["Substantiated privacy complaints",
                            "Data breaches", "Privacy impact assessments"],
                "triggers": ["privacy", "data breach", "personal data", "gdpr"],
            },
        },
        "retrieval_triggers": [
            "gri", "global reporting initiative", "sustainability report",
            "gri standards",
        ],
        "cross_references": ["CSRD_ESRS", "BRSR"],
    },

    # -----------------------------------------------------------------------
    # 6. SASB — Sustainability Accounting Standards Board
    # -----------------------------------------------------------------------
    "SASB": {
        "name": "SASB Standards (now part of ISSB)",
        "provisions": {
            "SASB:Sector": {
                "title": "77 Industry-specific Standards across 11 Sectors",
                "description": "Industry-specific disclosure topics and accounting metrics.",
                "metrics": [
                    "Industry-specific ESG metrics",
                    "Financially material sustainability topics",
                ],
                "triggers": ["sasb", "industry standard", "sector specific"],
            },
            "SASB:Sectors": {
                "title": "11 SICS Sectors",
                "description": "Consumer Goods, Extractives & Minerals Processing, Financials, Food & Beverage, Health Care, Infrastructure, Renewable Resources & Alternative Energy, Resource Transformation, Services, Technology & Communications, Transportation.",
                "metrics": ["Sector classification", "Industry materiality map"],
                "triggers": ["sics", "sasb sector", "industry classification"],
            },
            "SASB:GHGEmissions": {
                "title": "GHG Emissions (cross-sector)",
                "description": "Scope 1/2 emissions, emissions management.",
                "metrics": ["Scope 1 GHG (tCO2e)", "Scope 2 GHG (tCO2e)",
                            "Emissions reduction strategy"],
                "triggers": ["emissions", "ghg", "carbon"],
            },
            "SASB:EnergyMgmt": {
                "title": "Energy Management (cross-sector)",
                "description": "Total energy consumed, renewable %.",
                "metrics": ["Total energy (GJ)", "Grid electricity %",
                            "Renewable energy %"],
                "triggers": ["energy management", "energy consumption"],
            },
            "SASB:HumanCapital": {
                "title": "Human Capital (cross-sector)",
                "description": "Employee health/safety, diversity, labour practices.",
                "metrics": ["TRIR", "Diversity metrics", "Fair labour practices"],
                "triggers": ["human capital", "employee", "workforce", "labour"],
            },
        },
        "retrieval_triggers": [
            "sasb", "industry standard", "accounting standard",
            "financially material", "sics",
        ],
        "cross_references": ["ISSB", "GRI"],
    },

    # -----------------------------------------------------------------------
    # 7. SFDR — Sustainable Finance Disclosure Regulation
    # -----------------------------------------------------------------------
    "SFDR": {
        "name": "Sustainable Finance Disclosure Regulation (EU)",
        "provisions": {
            "SFDR:Article6": {
                "title": "Article 6 — Sustainability Risk Integration",
                "description": "All financial products must disclose how sustainability risks are integrated.",
                "metrics": [
                    "Sustainability risk policy",
                    "Risk integration in investment decisions",
                ],
                "triggers": ["article 6", "sustainability risk integration",
                             "financial product"],
            },
            "SFDR:Article8": {
                "title": "Article 8 — Environmental/Social Characteristics",
                "description": "Products promoting E/S characteristics (light green).",
                "metrics": [
                    "E/S characteristics promoted",
                    "Proportion of sustainable investments",
                    "PAI consideration",
                ],
                "triggers": ["article 8", "light green", "esg promotion",
                             "environmental characteristics"],
            },
            "SFDR:Article9": {
                "title": "Article 9 — Sustainable Investment Objective",
                "description": "Products with sustainable investment as objective (dark green).",
                "metrics": [
                    "Sustainable investment objective",
                    "DNSH assessment",
                    "EU Taxonomy alignment %",
                ],
                "triggers": ["article 9", "dark green", "sustainable investment",
                             "impact fund"],
            },
            "SFDR:PAI": {
                "title": "Principal Adverse Impact (PAI) Indicators",
                "description": "18 mandatory PAI indicators + additional opt-in indicators.",
                "metrics": [
                    "GHG emissions (Scope 1/2/3 of investees)",
                    "Carbon footprint of portfolio",
                    "GHG intensity of investees",
                    "Fossil fuel exposure %",
                    "Non-renewable energy share",
                    "Energy consumption intensity",
                    "Biodiversity-sensitive area activities",
                    "Water emissions",
                    "Hazardous waste ratio",
                    "UNGC/OECD violations",
                    "Gender pay gap (investees)",
                    "Board gender diversity (investees)",
                    "Controversial weapons exposure",
                    "Social violations in investees",
                ],
                "triggers": ["pai", "principal adverse impact", "adverse impact",
                             "esg indicator", "mandatory indicator"],
            },
        },
        "retrieval_triggers": [
            "sfdr", "sustainable finance disclosure", "article 8", "article 9",
            "pai indicator", "esg fund", "green fund", "sustainable investment",
        ],
        "cross_references": ["EU_TAXONOMY", "CSRD_ESRS"],
    },

    # -----------------------------------------------------------------------
    # 8. GHG Protocol
    # -----------------------------------------------------------------------
    "GHG_PROTOCOL": {
        "name": "GHG Protocol — Corporate Standard & Scope 3 Standard",
        "provisions": {
            "GHGP:Scope1": {
                "title": "Scope 1 — Direct Emissions",
                "description": "GHG emissions from owned/controlled sources.",
                "metrics": [
                    "Direct GHG emissions (tCO2e)",
                    "Stationary combustion",
                    "Mobile combustion",
                    "Process emissions",
                    "Fugitive emissions",
                ],
                "triggers": ["scope 1", "direct emissions", "combustion",
                             "fugitive emissions", "process emissions"],
            },
            "GHGP:Scope2": {
                "title": "Scope 2 — Indirect Energy Emissions",
                "description": "Emissions from purchased electricity, heat, steam, cooling.",
                "metrics": [
                    "Location-based Scope 2 (tCO2e)",
                    "Market-based Scope 2 (tCO2e)",
                    "Purchased electricity (MWh)",
                    "Renewable energy certificates (RECs)",
                ],
                "triggers": ["scope 2", "indirect emissions", "purchased electricity",
                             "market-based", "location-based", "rec", "green power"],
            },
            "GHGP:Scope3": {
                "title": "Scope 3 — Value Chain Emissions (15 categories)",
                "description": "All indirect emissions across the value chain.",
                "metrics": [
                    "Cat 1: Purchased goods & services",
                    "Cat 2: Capital goods",
                    "Cat 3: Fuel & energy-related activities",
                    "Cat 4: Upstream transportation & distribution",
                    "Cat 5: Waste generated in operations",
                    "Cat 6: Business travel",
                    "Cat 7: Employee commuting",
                    "Cat 8: Upstream leased assets",
                    "Cat 9: Downstream transportation & distribution",
                    "Cat 10: Processing of sold products",
                    "Cat 11: Use of sold products",
                    "Cat 12: End-of-life treatment of sold products",
                    "Cat 13: Downstream leased assets",
                    "Cat 14: Franchises",
                    "Cat 15: Investments",
                ],
                "triggers": ["scope 3", "value chain emissions", "supply chain emissions",
                             "upstream", "downstream", "purchased goods",
                             "business travel", "employee commuting"],
            },
        },
        "retrieval_triggers": [
            "ghg protocol", "scope 1", "scope 2", "scope 3",
            "carbon accounting", "emissions inventory", "carbon footprint",
        ],
        "cross_references": ["TCFD", "ISSB", "SBTi", "BRSR"],
    },

    # -----------------------------------------------------------------------
    # 9. SBTi — Science Based Targets initiative
    # -----------------------------------------------------------------------
    "SBTi": {
        "name": "Science Based Targets initiative",
        "provisions": {
            "SBTi:NearTerm": {
                "title": "Near-term Targets (5-10 years)",
                "description": "Emissions reduction targets aligned with 1.5C pathway.",
                "metrics": [
                    "Scope 1+2 reduction target (absolute or intensity)",
                    "Scope 3 target (if >40% of total emissions)",
                    "Base year and target year",
                    "Pathway method (absolute contraction / sectoral decarbonization)",
                ],
                "triggers": ["science based target", "sbti", "1.5 degree",
                             "near term target", "emissions reduction target"],
            },
            "SBTi:NetZero": {
                "title": "SBTi Net-Zero Standard",
                "description": "Long-term targets to reach net-zero by 2050.",
                "metrics": [
                    "Net-zero target year (no later than 2050)",
                    "Residual emissions (max 10% of base year)",
                    "Neutralization of residual emissions",
                    "Beyond value chain mitigation (BVCM)",
                ],
                "triggers": ["net zero", "net-zero", "2050 target", "residual emissions",
                             "bvcm", "neutralization"],
            },
            "SBTi:FLAG": {
                "title": "FLAG (Forest, Land and Agriculture) Guidance",
                "description": "Sector-specific for land-intensive sectors.",
                "metrics": [
                    "FLAG emissions target",
                    "No-deforestation commitment",
                    "Land sector emissions (separate from energy/industrial)",
                ],
                "triggers": ["flag", "forest", "land use", "agriculture",
                             "deforestation", "land sector"],
            },
        },
        "retrieval_triggers": [
            "sbti", "science based target", "net zero", "1.5 degree",
            "paris alignment", "emissions reduction",
        ],
        "cross_references": ["GHG_PROTOCOL", "TCFD", "CDP"],
    },

    # -----------------------------------------------------------------------
    # 10. TNFD — Taskforce on Nature-related Financial Disclosures
    # -----------------------------------------------------------------------
    "TNFD": {
        "name": "Taskforce on Nature-related Financial Disclosures",
        "provisions": {
            "TNFD:LEAP": {
                "title": "LEAP Approach (Locate-Evaluate-Assess-Prepare)",
                "description": "Systematic process for nature-related risk/opportunity assessment.",
                "metrics": [
                    "Locate: interface with nature (dependencies/impacts)",
                    "Evaluate: dependencies and impacts on nature",
                    "Assess: material nature-related risks and opportunities",
                    "Prepare: respond and report",
                ],
                "triggers": ["leap", "nature risk", "nature-related", "tnfd",
                             "nature dependency"],
            },
            "TNFD:Governance": {
                "title": "TNFD Governance",
                "description": "Board/management oversight of nature-related issues.",
                "metrics": ["Nature governance structure", "Nature expertise on board"],
                "triggers": ["nature governance", "biodiversity governance"],
            },
            "TNFD:Strategy": {
                "title": "TNFD Strategy",
                "description": "Nature-related risks/opportunities in strategy and financial planning.",
                "metrics": [
                    "Nature-related dependencies",
                    "Nature-related impacts",
                    "Ecosystem service valuation",
                ],
                "triggers": ["nature strategy", "ecosystem service",
                             "nature dependency", "natural capital"],
            },
            "TNFD:Metrics": {
                "title": "TNFD Metrics & Targets",
                "description": "Core and additional metrics on nature dependencies/impacts.",
                "metrics": [
                    "Land/freshwater/ocean area (ha)",
                    "Extent of land use change",
                    "Pollution/contamination to nature",
                    "Invasive species introduction",
                    "State of species (IUCN)",
                    "Ecosystem condition",
                ],
                "triggers": ["nature metrics", "biodiversity metrics",
                             "species", "land use change"],
            },
        },
        "retrieval_triggers": [
            "tnfd", "nature-related", "biodiversity", "ecosystem",
            "natural capital", "nature risk", "leap approach",
        ],
        "cross_references": ["TCFD", "CSRD_ESRS", "GRI"],
    },

    # -----------------------------------------------------------------------
    # 11. CDP — Carbon Disclosure Project
    # -----------------------------------------------------------------------
    "CDP": {
        "name": "CDP (Carbon Disclosure Project)",
        "provisions": {
            "CDP:Climate": {
                "title": "CDP Climate Change Questionnaire",
                "description": "Annual climate disclosure — governance, risks, emissions, targets.",
                "metrics": [
                    "CDP Climate Score (A to D-)",
                    "Scope 1/2/3 GHG emissions",
                    "Climate-related risks (physical + transition)",
                    "Climate targets and progress",
                    "Carbon pricing mechanisms",
                    "Renewable energy procurement",
                ],
                "triggers": ["cdp climate", "cdp score", "carbon disclosure",
                             "climate questionnaire"],
            },
            "CDP:Forests": {
                "title": "CDP Forests Questionnaire",
                "description": "Deforestation risk commodities — palm oil, soy, timber, cattle, rubber, cocoa, coffee.",
                "metrics": [
                    "CDP Forests Score",
                    "Deforestation-free commitments",
                    "Commodity traceability %",
                    "Certification coverage (RSPO, FSC, etc.)",
                ],
                "triggers": ["cdp forest", "deforestation", "palm oil", "timber",
                             "commodity risk"],
            },
            "CDP:Water": {
                "title": "CDP Water Security Questionnaire",
                "description": "Water risk, governance, accounting, targets.",
                "metrics": [
                    "CDP Water Score",
                    "Water withdrawal (ML)",
                    "Water-stressed site operations",
                    "Water-related targets",
                ],
                "triggers": ["cdp water", "water security", "water risk",
                             "water disclosure"],
            },
            "CDP:Scoring": {
                "title": "CDP Scoring Methodology (A to D-)",
                "description": "4-tier scoring: Leadership (A/A-), Management (B/B-), Awareness (C/C-), Disclosure (D/D-).",
                "metrics": [
                    "Disclosure score (D/D-)",
                    "Awareness score (C/C-)",
                    "Management score (B/B-)",
                    "Leadership score (A/A-)",
                ],
                "triggers": ["cdp score", "cdp rating", "cdp grade",
                             "a-list", "leadership band"],
            },
        },
        "retrieval_triggers": [
            "cdp", "carbon disclosure project", "cdp score", "cdp rating",
            "climate disclosure", "forest disclosure",
        ],
        "cross_references": ["TCFD", "GHG_PROTOCOL", "SBTi"],
    },

    # -----------------------------------------------------------------------
    # 12. SEC Climate Rules
    # -----------------------------------------------------------------------
    "SEC_CLIMATE": {
        "name": "SEC Climate-Related Disclosure Rules (US)",
        "provisions": {
            "SEC:SK1500": {
                "title": "Reg S-K Item 1500 — Governance",
                "description": "Board and management oversight of climate-related risks.",
                "metrics": [
                    "Board climate oversight description",
                    "Management role in climate risk assessment",
                ],
                "triggers": ["sec governance", "item 1500", "board climate oversight"],
            },
            "SEC:SK1502": {
                "title": "Reg S-K Item 1502 — Strategy, Business Model, Outlook",
                "description": "Climate-related risks materially impacting strategy.",
                "metrics": [
                    "Material climate risks identified",
                    "Impact on business strategy",
                    "Transition plan (if any)",
                    "Scenario analysis (if used)",
                ],
                "triggers": ["sec strategy", "item 1502", "material climate risk"],
            },
            "SEC:SK1504": {
                "title": "Reg S-K Item 1504 — Targets and Goals",
                "description": "Climate-related targets, transition plans, carbon offsets.",
                "metrics": [
                    "Climate targets disclosed",
                    "Carbon offset/REC usage",
                    "Material expenditures for climate targets",
                ],
                "triggers": ["sec targets", "item 1504", "climate target disclosure"],
            },
            "SEC:SX": {
                "title": "Reg S-X — Financial Statement Disclosures",
                "description": "Climate-related financial impacts in financial statements.",
                "metrics": [
                    "Severe weather costs (>1% of line item)",
                    "Carbon offset/REC expenses",
                    "Climate-related asset impairments",
                    "Transition activity expenditures",
                ],
                "triggers": ["sec financial", "reg s-x", "financial statement",
                             "climate cost", "asset impairment"],
            },
            "SEC:Scope1_2": {
                "title": "Scope 1 & 2 GHG Emissions",
                "description": "Mandatory Scope 1 & 2 disclosure (phased: LAFs 2026, AFs 2028, SRCs 2028).",
                "metrics": [
                    "Scope 1 GHG emissions (tCO2e)",
                    "Scope 2 GHG emissions (tCO2e)",
                    "GHG emissions attestation (limited → reasonable assurance)",
                ],
                "triggers": ["sec emissions", "sec scope 1", "sec scope 2",
                             "attestation", "assurance"],
            },
        },
        "retrieval_triggers": [
            "sec climate", "sec rule", "regulation s-k", "regulation s-x",
            "sec disclosure", "us climate regulation", "sec filing",
        ],
        "cross_references": ["TCFD", "ISSB", "GHG_PROTOCOL"],
    },

    # -----------------------------------------------------------------------
    # 13. BRSR — Business Responsibility and Sustainability Reporting (India)
    # -----------------------------------------------------------------------
    # NOTE: Most detailed knowledge base — India-focused platform priority.
    "BRSR": {
        "name": "BRSR — Business Responsibility and Sustainability Reporting (SEBI India)",
        "provisions": {
            # --------------- Section A: General Disclosures ---------------
            "BRSR:SectionA": {
                "title": "Section A — General Disclosures",
                "description": "Company overview, products/services, operations, employees, CSR, governance.",
                "metrics": [
                    "CIN, registered office, financial year",
                    "Products/services as % of turnover",
                    "Operations — plants, offices, markets",
                    "Employee/worker demographics (permanent, non-permanent, differently-abled)",
                    "Participation/inclusion/representation of women",
                    "Turnover rate (permanent employees/workers)",
                    "CSR details (applicability, turnover, net worth)",
                    "Transparency and disclosure compliances",
                    "Holding, subsidiary, associate companies (incl. ESG performance)",
                ],
                "triggers": ["general disclosure", "company overview", "cin",
                             "operations", "employee demographics"],
            },
            # --------------- Section B: Management & Process ---------------
            "BRSR:SectionB": {
                "title": "Section B — Management and Process Disclosures",
                "description": "Policy and management process disclosures for each NGRBC principle.",
                "metrics": [
                    "Policy availability for each principle (P1-P9)",
                    "Policy approval by board/senior management",
                    "Policy web-link / translated for stakeholders",
                    "Governance structure per principle (director/committee responsible)",
                    "Grievance redressal mechanism per stakeholder group",
                    "Performance against NVG / NGRBC policies",
                ],
                "triggers": ["management process", "policy disclosure", "ngrbc",
                             "governance structure", "grievance redressal"],
            },
            # --------------- Section C: Principle-wise Performance ---------------
            # Principle 1: Ethics, Transparency, Accountability
            "BRSR:P1": {
                "title": "Principle 1 — Ethics, Transparency and Accountability",
                "description": "NGRBC Principle 1 — ethical conduct, anti-corruption, responsible advocacy.",
                "questions": ["Q1 (Ethics/bribery policy coverage)", "Q2 (Conflicts of interest cases)",
                              "Q3 (Training on ethics — employees/workers %)", "Q4 (Complaints on ethics/corruption)",
                              "Q5 (Fines/penalties from regulators on P1 matters)"],
                "metrics": [
                    "Training on ethics/integrity/conflicts of interest (employees/workers %)",
                    "Complaints on ethics/bribery/corruption and their status",
                    "Disciplinary actions against directors/KMPs for ethics violations",
                    "Conflicts of interest cases and resolution",
                    "Anti-corruption/bribery policy coverage",
                    "Fines/penalties from regulators/courts on P1 matters",
                    "Details of appeal/preferring of penalty",
                    "NGRBC principle affirmation status",
                ],
                "triggers": ["ethics", "transparency", "accountability", "corruption",
                             "bribery", "principle 1", "p1", "integrity"],
            },
            # Principle 2: Sustainable Products / Lifecycle
            "BRSR:P2": {
                "title": "Principle 2 — Products Lifecycle Sustainability",
                "description": "NGRBC Principle 2 — sustainable goods/services across lifecycle.",
                "questions": ["Q6 (R&D/CapEx on environmental/social product improvements)",
                              "Q7 (Sustainable sourcing procedures — % of inputs)",
                              "Q8 (Recycled/reused input material as % of total)",
                              "Q9 (Extended Producer Responsibility — plastic, e-waste, battery)",
                              "Q10 (Life Cycle Assessments conducted)"],
                "metrics": [
                    "R&D and CapEx on improving environmental/social impacts of products",
                    "Procedures for sustainable sourcing (% of inputs sustainably sourced)",
                    "Product reclamation/recycled inputs as % of total",
                    "Extended Producer Responsibility (EPR) — plastic waste, e-waste, used oil, battery",
                    "Life Cycle Assessment (LCA) for products",
                    "Recycled/reused input material as % of total material",
                    "Products designed for reuse/recyclability",
                ],
                "triggers": ["product lifecycle", "sustainable sourcing", "epr",
                             "extended producer responsibility", "reclamation",
                             "lifecycle assessment", "principle 2", "p2"],
            },
            # Principle 3: Employee Wellbeing
            "BRSR:P3": {
                "title": "Principle 3 — Employee Wellbeing",
                "description": "NGRBC Principle 3 — promote wellbeing of all employees and workers.",
                "questions": ["Q11 (PF/gratuity/ESI/maternity coverage — permanent/non-permanent %)",
                              "Q12 (Benefits to contract/temporary workers)",
                              "Q13 (Accessibility for differently-abled workers)",
                              "Q14 (Parental leave return-to-work and retention rate, gender-wise)",
                              "Q15 (Grievance redressal — harassment, discrimination, child labour, wages)",
                              "Q16 (Trade union/association membership %)",
                              "Q17 (Training on health & safety, skill upgradation — gender/category)",
                              "Q18 (Safety incidents — LTIFR, fatalities, injuries, employees/workers separate)",
                              "Q19 (Assessments on health & safety practices and working conditions)"],
                "metrics": [
                    "Employees/workers covered by PF, gratuity, ESI, maternity (% permanent/non-permanent)",
                    "Benefits to contract/temporary workers",
                    "Accessibility of workplaces for differently-abled",
                    "Return to work and retention rate after parental leave (gender-wise)",
                    "Grievance redressal — sexual harassment, discrimination, child labour, wages",
                    "Membership of employees/workers in trade unions/associations",
                    "Training on health & safety, skill upgradation (gender, category)",
                    "Safety incidents — LTIFR, fatalities, injuries (employees/workers separate)",
                    "Rehabilitation of injured workers and affected families",
                    "Transition assistance programs",
                    "Assessments on health & safety practices, working conditions",
                    "Minimum wages compliance",
                ],
                "triggers": ["employee wellbeing", "worker safety", "health safety",
                             "ltifr", "maternity", "gratuity", "trade union",
                             "grievance", "sexual harassment", "principle 3", "p3",
                             "minimum wages"],
            },
            # Principle 4: Stakeholder Engagement
            "BRSR:P4": {
                "title": "Principle 4 — Stakeholder Engagement",
                "description": "NGRBC Principle 4 — responsive to all stakeholders.",
                "questions": ["Q20 (Identified material stakeholder groups)",
                              "Q21 (Channels and frequency of engagement per group)",
                              "Q22 (Key concerns identified per stakeholder group)",
                              "Q23 (Vulnerable/marginalised stakeholder engagement)",
                              "Q24 (Special initiatives for marginalised groups)"],
                "metrics": [
                    "Identified material stakeholder groups",
                    "Channels/frequency of engagement per group",
                    "Key concerns identified per stakeholder group",
                    "Vulnerable/marginalised stakeholder engagement",
                    "Special initiatives for marginalised groups",
                ],
                "triggers": ["stakeholder engagement", "stakeholder group",
                             "marginalised", "vulnerable", "principle 4", "p4"],
            },
            # Principle 5: Human Rights
            "BRSR:P5": {
                "title": "Principle 5 — Human Rights",
                "description": "NGRBC Principle 5 — respect and promote human rights.",
                "questions": ["Q25 (Training on human rights — employees/workers %)",
                              "Q26 (Minimum wages paid — permanent/non-permanent, gender-wise %)",
                              "Q27 (Median/mean remuneration male vs female employees)",
                              "Q28 (Gross wages paid to women as % of total wages)",
                              "Q29 (Complaints on harassment, discrimination, child/forced labour, wages)",
                              "Q30 (Human rights due diligence and value chain assessment)"],
                "metrics": [
                    "Training on human rights (employees/workers % and scope)",
                    "Minimum wages paid to all permanent/non-permanent (gender-wise %)",
                    "Median/mean remuneration of male vs female employees",
                    "Gross wages paid to women as % of total wages",
                    "Complaints on sexual harassment, discrimination, child labour, "
                    "forced/involuntary labour, wages (filed/pending/resolved)",
                    "Human rights due diligence mechanisms",
                    "Assessment of subsidiaries/value chain on human rights",
                    "Mechanisms to prevent adverse consequences on stakeholders in supply/value chain",
                    "Corrective actions taken against business partners for human rights violations",
                ],
                "triggers": ["human rights", "child labour", "forced labour",
                             "discrimination", "equal remuneration", "gender pay",
                             "principle 5", "p5"],
            },
            # Principle 6: Environment
            "BRSR:P6": {
                "title": "Principle 6 — Environment",
                "description": "NGRBC Principle 6 — protect and restore the environment.",
                "questions": [
                    "Q31 (Total energy consumption — renewable & non-renewable, GJ)",
                    "Q32 (Energy intensity per rupee of turnover)",
                    "Q33 (PAT scheme compliance status)",
                    "Q34 (Water withdrawal by source — surface, ground, third-party, seawater)",
                    "Q35 (Total water consumption and intensity per turnover)",
                    "Q36 (Water discharged — destination and treatment level)",
                    "Q37 (Zero Liquid Discharge status)",
                    "Q38 (Scope 1 & 2 GHG emissions in tCO2e and intensity)",
                    "Q39 (Scope 1 & 2 emission details per BRSR Core / LODR 2023)",
                    "Q40 (Air emissions — NOx, SOx, PM, VOC, HAP)",
                    "Q41 (Total waste generated — hazardous and non-hazardous, MT)",
                    "Q42 (Waste recycled, reused, recovered, incinerated, landfilled)",
                    "Q43 (Waste intensity per rupee of turnover)",
                    "Q44 (Waste management practices and disposal methods)",
                    "Q45 (Ecologically sensitive areas — operations near protected zones)",
                    "Q46 (Environmental Impact Assessments conducted)",
                    "Q47 (Environmental compliance — notices, fines, penalties)",
                    "Q48 (Scope 3 emissions — upstream/downstream per BRSR Core)"],
                "metrics": [
                    # Energy
                    "Total energy consumption (GJ) — renewable & non-renewable",
                    "Energy intensity per rupee of turnover",
                    "PAT (Perform Achieve Trade) scheme compliance",
                    # Water
                    "Water withdrawal by source (surface, ground, third-party, seawater, other)",
                    "Total volume of water consumption, intensity per turnover",
                    "Water discharged (destination, treatment level)",
                    "Zero Liquid Discharge (ZLD) status",
                    # Emissions
                    "Scope 1 & 2 GHG emissions (tCO2e) and intensity",
                    "Scope 1 & 2 emission details per BRSR Core",
                    "Air emissions (NOx, SOx, PM, POP, VOC, HAP)",
                    # Waste
                    "Total waste generated (hazardous & non-hazardous) in MT",
                    "Waste recycled, reused, recovered, incinerated, landfilled",
                    "Waste intensity per turnover",
                    "Waste management practices",
                    # Biodiversity
                    "Ecologically sensitive areas — operations/offices near protected zones",
                    "Environmental impact assessments",
                    "EIA notification compliance",
                    # Compliance
                    "Environmental compliance — show-cause/corrective notices, fines, penalties",
                    "Pollution Control Board consent status",
                    # BRSR Core additions
                    "Scope 3 emissions (upstream/downstream) per SEBI BRSR Core (LODR 2023)",
                ],
                "triggers": ["environment", "emissions", "energy", "water", "waste",
                             "biodiversity", "ghg", "scope 1", "scope 2", "air quality",
                             "zld", "principle 6", "p6", "pollution"],
            },
            # Principle 7: Public Policy
            "BRSR:P7": {
                "title": "Principle 7 — Responsible Policy Advocacy",
                "description": "NGRBC Principle 7 — responsible and transparent policy influence.",
                "questions": ["Q49 (Membership in trade/industry chambers/associations)",
                              "Q50 (Policy advocacy positions on ESG topics)",
                              "Q51 (Anti-competitive conduct cases and corrective actions)"],
                "metrics": [
                    "Membership in trade/industry chambers/associations",
                    "Policy advocacy positions on ESG topics",
                    "Anti-competitive conduct cases and corrective actions",
                ],
                "triggers": ["policy advocacy", "trade association", "lobbying",
                             "anti-competitive", "principle 7", "p7"],
            },
            # Principle 8: Inclusive Growth
            "BRSR:P8": {
                "title": "Principle 8 — Inclusive Growth and Equitable Development",
                "description": "NGRBC Principle 8 — promote inclusive growth, equitable development.",
                "questions": ["Q52 (Social Impact Assessments conducted)",
                              "Q53 (Community rehabilitation and resettlement programs)",
                              "Q54 (CSR projects — beneficiaries and spend by geography)",
                              "Q55 (Input material sourced from MSMEs/small producers %)",
                              "Q56 (Procurement from MSMEs %)",
                              "Q57 (Job creation in smaller towns — Tier 2/3)",
                              "Q58 (Preferential procurement from disadvantaged groups)",
                              "Q59 (CSR spend as % of PAT — Companies Act Section 135)",
                              "Q60 (Direct community investments and outcomes)"],
                "metrics": [
                    "Social Impact Assessments (SIA) conducted",
                    "Community rehabilitation and resettlement programs",
                    "CSR projects — beneficiaries, spend by geography",
                    "Input material sourced from MSMEs/small producers (%)",
                    "Procurement from MSMEs (%)",
                    "Job creation in smaller towns (Tier 2/3)",
                    "Preferential procurement from disadvantaged / vulnerable groups",
                    "CSR spend as % of PAT (Companies Act Section 135)",
                ],
                "triggers": ["inclusive growth", "equitable development", "csr",
                             "community development", "msme", "social impact",
                             "rehabilitation", "principle 8", "p8"],
            },
            # Principle 9: Consumer Responsibility
            "BRSR:P9": {
                "title": "Principle 9 — Consumer / Customer Value",
                "description": "NGRBC Principle 9 — engage with and provide value to consumers responsibly.",
                "questions": ["Q61 (Consumer complaints — data privacy, advertising, quality, unfair trade)",
                              "Q62 (Product recalls — voluntary and forced)",
                              "Q63 (Cybersecurity and data privacy policy)",
                              "Q64 (Information on products for responsible consumption)",
                              "Q65 (Mechanisms for safe, informed sustainable product choices)",
                              "Q66 (Consumer satisfaction survey results)"],
                "metrics": [
                    "Consumer complaints on data privacy, advertising, delivery, quality, unfair trade",
                    "Product recalls — voluntary and forced",
                    "Cybersecurity and data privacy policy",
                    "Information on products/services to enable responsible consumption",
                    "Mechanisms for safe, informed choices on sustainable products",
                    "Product/service safety incidents",
                    "Consumer satisfaction survey results",
                ],
                "triggers": ["consumer", "customer", "product safety", "data privacy",
                             "product recall", "cybersecurity", "principle 9", "p9",
                             "consumer complaint"],
            },
            # --------------- BRSR Core ---------------
            "BRSR:Core": {
                "title": "BRSR Core — Mandatory Assured Metrics (SEBI LODR 2023)",
                "description": (
                    "Subset of BRSR metrics for mandatory reasonable assurance, effective FY2023-24 "
                    "for top-1000 listed companies. SEBI Circular SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122 "
                    "(Jul 12, 2023) and SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/0000000086 (Jun 2023)."
                ),
                "metrics": [
                    # Environment (BRSR Core)
                    "Scope 1 & 2 GHG emissions (tCO2e) — independently assured",
                    "Scope 3 GHG emissions (8 mandatory upstream + 7 downstream categories)",
                    "Energy consumption (total, renewable %, intensity)",
                    "Water withdrawal, consumption, intensity — by source",
                    "Water discharged — with treatment level breakdown",
                    "Waste generated (hazardous/non-hazardous), recovered, disposed",
                    # Social (BRSR Core)
                    "Gender diversity — BoD, KMP, employees, workers",
                    "Median remuneration — male vs female (by category)",
                    "Gross wages paid to women as % of total wages",
                    "Complaints on POSH, discrimination, child labour, forced labour",
                    "LTIFR, fatality count — employees vs workers",
                    "Health insurance/accident cover — employees vs workers",
                    "Training hours/spend — gender-wise",
                    "Fair wage / minimum wage coverage (% of employees/workers)",
                    # Governance (BRSR Core)
                    "Anti-corruption/bribery complaints and status",
                    "Input sourced from MSMEs/small producers — % of total purchases",
                    "Job creation in smaller towns (Tier 2/3) — % of new hires",
                ],
                "triggers": ["brsr core", "sebi circular", "assured metrics",
                             "mandatory assurance", "lodr", "brsr assurance"],
            },
            # --------------- Value Chain Extension ---------------
            "BRSR:ValueChain": {
                "title": "BRSR Value Chain Extension (SEBI Phase-in)",
                "description": (
                    "SEBI mandates top-250 listed companies to disclose BRSR Core metrics for "
                    "their value chain (top upstream/downstream partners) from FY2024-25 onward. "
                    "Ref: SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122."
                ),
                "metrics": [
                    "Value chain partner identification (top suppliers, customers by spend/revenue)",
                    "Scope 3 emissions of value chain partners",
                    "Value chain worker conditions (POSH, child labour, forced labour)",
                    "Value chain environmental metrics (energy, water, waste)",
                    "Value chain BRSR Core assurance status",
                    "Phase-in timeline: top-250 FY2024-25, top-500 FY2025-26, top-1000 FY2026-27",
                ],
                "triggers": ["value chain", "supply chain disclosure", "sebi value chain",
                             "upstream partner", "downstream partner",
                             "value chain assurance"],
            },
            # --------------- SEBI Circulars (key references) ---------------
            "BRSR:SEBIRef": {
                "title": "SEBI Regulatory References",
                "description": "Key SEBI circulars and LODR amendments governing BRSR.",
                "metrics": [
                    "SEBI Circular CIR/CFD/CMD/10/2015 — original BRR format",
                    "SEBI Circular SEBI/HO/CFD/CMD-2/P/CIR/2021/562 (May 10, 2021) — BRSR format",
                    "LODR Regulation 34(2)(f) — mandatory BRSR for top-1000 from FY2022-23",
                    "SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122 (Jul 12, 2023) — BRSR Core + value chain",
                    "SEBI Circular on ESG Rating Providers (Jul 2023) — standardized ESG rating regime",
                    "SEBI Consultation Paper on BRSR Core assured metrics (Dec 2022)",
                    "Companies Act 2013 Section 135 — CSR applicability thresholds",
                    "NGRBC (National Guidelines on Responsible Business Conduct) 2019 — 9 principles",
                ],
                "triggers": ["sebi circular", "lodr", "sebi regulation", "brr",
                             "ngrbc guideline", "companies act", "section 135"],
            },
        },
        "retrieval_triggers": [
            "brsr", "sebi", "ngrbc", "business responsibility", "sustainability report india",
            "lodr", "indian regulation", "section 135", "csr india", "brsr core",
            "value chain disclosure", "ngrbc principle",
        ],
        "cross_references": ["GRI", "GHG_PROTOCOL", "TCFD", "CSRD_ESRS"],
    },
}


# ---------------------------------------------------------------------------
# Cross-framework alignment map
# ---------------------------------------------------------------------------
# Where frameworks overlap on the same topic — used to enrich FrameworkMatch.alignment_notes.

CROSS_FRAMEWORK_ALIGNMENT: dict[str, list[dict[str, str]]] = {
    "ghg_emissions": [
        {"frameworks": "TCFD:Metrics ↔ ISSB:IFRS_S2 ↔ CSRD:ESRS:E1 ↔ GHG_PROTOCOL ↔ BRSR:P6 ↔ CDP:Climate ↔ SEC:Scope1_2",
         "note": "All frameworks require Scope 1/2 GHG disclosure; ISSB and BRSR Core now mandate Scope 3."},
    ],
    "climate_governance": [
        {"frameworks": "TCFD:Governance ↔ ISSB:IFRS_S1 ↔ CSRD:ESRS:G1 ↔ BRSR:P1 ↔ SEC:SK1500",
         "note": "Board-level climate oversight required across TCFD, ISSB, CSRD, BRSR (P1), and SEC rules."},
    ],
    "climate_scenario_analysis": [
        {"frameworks": "TCFD:Strategy ↔ ISSB:IFRS_S2 ↔ SEC:SK1502",
         "note": "Scenario analysis (1.5C/2C) encouraged by TCFD, required by ISSB S2 if used, optional under SEC."},
    ],
    "biodiversity": [
        {"frameworks": "CSRD:ESRS:E4 ↔ TNFD:LEAP ↔ GRI:304 ↔ BRSR:P6 ↔ CDP:Forests",
         "note": "Nature/biodiversity disclosures converging — TNFD LEAP aligns with ESRS E4; BRSR requires EIA disclosure."},
    ],
    "water": [
        {"frameworks": "CSRD:ESRS:E3 ↔ GRI:303 ↔ CDP:Water ↔ BRSR:P6",
         "note": "Water withdrawal, consumption, discharge metrics overlap across ESRS E3, GRI 303, CDP Water, and BRSR P6."},
    ],
    "waste_circular_economy": [
        {"frameworks": "CSRD:ESRS:E5 ↔ GRI:306 ↔ BRSR:P2 ↔ BRSR:P6 ↔ EU_TAXONOMY:CE",
         "note": "Waste/circular economy covered by ESRS E5, GRI 306, BRSR P2 (lifecycle) + P6 (waste), EU Taxonomy CE."},
    ],
    "human_rights_supply_chain": [
        {"frameworks": "CSRD:ESRS:S2 ↔ GRI:414 ↔ BRSR:P5 ↔ SFDR:PAI",
         "note": "Supply chain human rights due diligence required by ESRS S2, GRI 414, BRSR P5, and SFDR PAI."},
    ],
    "worker_safety": [
        {"frameworks": "CSRD:ESRS:S1 ↔ GRI:403 ↔ BRSR:P3 ↔ SASB:HumanCapital",
         "note": "LTIFR, fatalities, OHS management overlap in ESRS S1, GRI 403, BRSR P3, SASB Human Capital."},
    ],
    "anti_corruption": [
        {"frameworks": "CSRD:ESRS:G1 ↔ GRI:205 ↔ BRSR:P1",
         "note": "Anti-corruption policy, training, and incidents required by ESRS G1, GRI 205, BRSR P1."},
    ],
    "diversity_inclusion": [
        {"frameworks": "CSRD:ESRS:S1 ↔ GRI:405 ↔ BRSR:P5 ↔ SFDR:PAI",
         "note": "Gender diversity on board, gender pay gap disclosed under ESRS S1, GRI 405, BRSR P5, SFDR PAI."},
    ],
    "community_impact": [
        {"frameworks": "CSRD:ESRS:S3 ↔ GRI:413 ↔ BRSR:P8",
         "note": "Community engagement, CSR, SIA required by ESRS S3, GRI 413, BRSR P8 (inclusive growth)."},
    ],
    "data_privacy": [
        {"frameworks": "CSRD:ESRS:S4 ↔ GRI:418 ↔ BRSR:P9",
         "note": "Consumer data privacy / cybersecurity disclosures in ESRS S4, GRI 418, BRSR P9."},
    ],
    "taxonomy_alignment": [
        {"frameworks": "EU_TAXONOMY ↔ CSRD:ESRS ↔ SFDR",
         "note": "EU Taxonomy alignment % feeds into CSRD/ESRS disclosures and SFDR Article 8/9 reporting."},
    ],
    "science_based_targets": [
        {"frameworks": "SBTi ↔ TCFD:Metrics ↔ CDP:Climate ↔ ISSB:IFRS_S2",
         "note": "SBTi-validated targets referenced in TCFD metrics, CDP questionnaire, and ISSB S2 transition plan."},
    ],
    "brsr_gri_mapping": [
        {"frameworks": "BRSR:P1→GRI:205, BRSR:P2→GRI:301, BRSR:P3→GRI:401/403, BRSR:P5→GRI:405/414, "
                        "BRSR:P6→GRI:302/303/304/305/306, BRSR:P8→GRI:413, BRSR:P9→GRI:418",
         "note": "SEBI's BRSR principles map closely to GRI Topic Standards — companies reporting both can share data."},
    ],
}


# ---------------------------------------------------------------------------
# Theme → Framework mapping (connects Module 3 ESG themes to frameworks)
# ---------------------------------------------------------------------------

_THEME_FRAMEWORK_MAP: dict[str, list[str]] = {
    # Environmental themes
    "climate_change": ["TCFD", "ISSB", "CSRD_ESRS", "GHG_PROTOCOL", "SBTi", "CDP", "SEC_CLIMATE", "BRSR"],
    "ghg_emissions": ["TCFD", "ISSB", "CSRD_ESRS", "GHG_PROTOCOL", "CDP", "SEC_CLIMATE", "BRSR", "SASB"],
    "energy": ["CSRD_ESRS", "GRI", "BRSR", "SASB", "CDP"],
    "water": ["CSRD_ESRS", "GRI", "CDP", "BRSR"],
    "biodiversity": ["CSRD_ESRS", "TNFD", "GRI", "CDP", "BRSR"],
    "waste": ["CSRD_ESRS", "GRI", "BRSR", "EU_TAXONOMY"],
    "circular_economy": ["CSRD_ESRS", "EU_TAXONOMY", "BRSR", "GRI"],
    "pollution": ["CSRD_ESRS", "GRI", "BRSR"],
    "deforestation": ["CDP", "TNFD", "BRSR"],
    "renewable_energy": ["TCFD", "CSRD_ESRS", "GRI", "BRSR", "SBTi"],

    # Social themes
    "worker_safety": ["CSRD_ESRS", "GRI", "BRSR", "SASB"],
    "diversity_inclusion": ["CSRD_ESRS", "GRI", "BRSR", "SFDR"],
    "human_rights": ["CSRD_ESRS", "GRI", "BRSR"],
    "supply_chain_labor": ["CSRD_ESRS", "GRI", "BRSR", "SFDR"],
    "community": ["CSRD_ESRS", "GRI", "BRSR"],
    "consumer_privacy": ["CSRD_ESRS", "GRI", "BRSR"],
    "product_safety": ["CSRD_ESRS", "GRI", "BRSR"],
    "employee_wellbeing": ["CSRD_ESRS", "GRI", "BRSR", "SASB"],
    "living_wage": ["CSRD_ESRS", "GRI", "BRSR"],

    # Governance themes
    "corruption": ["CSRD_ESRS", "GRI", "BRSR"],
    "governance": ["TCFD", "ISSB", "CSRD_ESRS", "BRSR", "SEC_CLIMATE"],
    "lobbying": ["CSRD_ESRS", "GRI", "BRSR"],
    "tax_transparency": ["GRI", "BRSR"],

    # Finance / ESG investing themes
    "sustainable_finance": ["SFDR", "EU_TAXONOMY"],
    "esg_rating": ["CDP", "BRSR"],
    "green_bond": ["EU_TAXONOMY", "SFDR"],

    # Regulation / compliance themes
    "sebi_regulation": ["BRSR"],
    "eu_regulation": ["CSRD_ESRS", "EU_TAXONOMY", "SFDR"],
    "sec_regulation": ["SEC_CLIMATE"],
}


# ---------------------------------------------------------------------------
# Core retrieval functions
# ---------------------------------------------------------------------------

def get_framework_provisions(
    framework_id: str,
    theme: str | None = None,
) -> list[dict[str, Any]]:
    """Return specific provisions for a framework, optionally filtered by theme triggers.

    Args:
        framework_id: Key into FRAMEWORK_KNOWLEDGE (e.g. "BRSR", "TCFD").
        theme: Optional ESG theme to filter provisions by trigger relevance.

    Returns:
        List of provision dicts with keys: section_code, title, description, metrics, triggers.
    """
    kb = FRAMEWORK_KNOWLEDGE.get(framework_id)
    if not kb:
        logger.warning("framework_not_found", framework_id=framework_id)
        return []

    provisions = kb["provisions"]
    results: list[dict[str, Any]] = []

    for section_code, prov in provisions.items():
        # If theme is provided, only include provisions whose triggers match
        if theme:
            theme_lower = theme.lower().replace("_", " ")
            triggers = prov.get("triggers", [])
            if not any(t in theme_lower or theme_lower in t for t in triggers):
                continue

        results.append({
            "section_code": section_code,
            "title": prov["title"],
            "description": prov["description"],
            "questions": prov.get("questions", []),
            "metrics": prov.get("metrics", []),
            "triggers": prov.get("triggers", []),
        })

    return results


def _match_frameworks_by_triggers(
    text: str,
    esg_themes: list[str] | None = None,
    company_region: str | None = None,
) -> list[FrameworkMatch]:
    """Rule-based framework matching using retrieval triggers and theme map.

    Args:
        text: Combined article title + content to scan for trigger keywords.
        esg_themes: ESG theme tags from Module 3 (e.g. ["climate_change", "worker_safety"]).

    Returns:
        List of FrameworkMatch objects sorted by relevance_score descending.
    """
    text_lower = text.lower()
    scores: dict[str, float] = {}
    triggered_sections: dict[str, list[str]] = {}
    triggered_questions: dict[str, list[str]] = {}
    cross_metrics: dict[str, list[str]] = {}

    # Step 1: Match from ESG themes (highest signal)
    for theme in (esg_themes or []):
        theme_key = theme.lower().replace(" ", "_")
        framework_ids = _THEME_FRAMEWORK_MAP.get(theme_key, [])
        for fid in framework_ids:
            scores[fid] = scores.get(fid, 0.0) + 0.3

    # Step 2: Match from retrieval triggers in text
    for fid, kb in FRAMEWORK_KNOWLEDGE.items():
        for trigger in kb.get("retrieval_triggers", []):
            if trigger in text_lower:
                scores[fid] = scores.get(fid, 0.0) + 0.15

        # Step 3: Match individual provision triggers; collect question-level citations
        for section_code, prov in kb["provisions"].items():
            for trigger in prov.get("triggers", []):
                if trigger in text_lower:
                    scores[fid] = scores.get(fid, 0.0) + 0.05
                    triggered_sections.setdefault(fid, [])
                    if section_code not in triggered_sections[fid]:
                        triggered_sections[fid].append(section_code)
                    # Collect question-level citations for this provision (BRSR + others)
                    for q in prov.get("questions", []):
                        triggered_questions.setdefault(fid, [])
                        if q not in triggered_questions[fid]:
                            triggered_questions[fid].append(q)
                    break  # one trigger match is enough to cite questions

        # Capture cross-industry metrics for TCFD
        if fid == "TCFD" and fid in scores:
            cross_metrics[fid] = kb.get("cross_industry_metrics", [])

    # Apply region-based boosts, penalties, and global framework boosts
    for fid in list(scores.keys()):
        if company_region:
            # Regional boost
            regional_boosts = REGION_FRAMEWORK_BOOST.get(company_region, {})
            for fw_id, boost in regional_boosts.items():
                if fw_id.upper() in fid.upper() or fid.upper() in fw_id.upper():
                    scores[fid] += boost
            # Regional penalty (wrong-region frameworks)
            regional_penalties = REGION_FRAMEWORK_PENALTY.get(company_region, {})
            for fw_id, penalty in regional_penalties.items():
                if fw_id.upper() in fid.upper() or fid.upper() in fw_id.upper():
                    scores[fid] += penalty  # penalty is negative
        # Always add global boost
        for fw_id, boost in GLOBAL_FRAMEWORKS.items():
            if fw_id.upper() in fid.upper():
                scores[fid] += boost
        # Clamp to [0, 1]
        scores[fid] = max(0.0, min(scores[fid], 1.0))

    # Build FrameworkMatch objects
    matches: list[FrameworkMatch] = []
    for fid, score in scores.items():
        if score < 0.05:
            continue

        kb = FRAMEWORK_KNOWLEDGE[fid]
        capped_score = min(score, 1.0)

        # Compliance implications from matched sections
        implications: list[str] = []
        for sec in triggered_sections.get(fid, []):
            prov = kb["provisions"].get(sec, {})
            if prov.get("description"):
                implications.append(f"{sec}: {prov['description']}")

        # Cross-framework alignment notes
        alignment: list[str] = []
        for _topic, alignments in CROSS_FRAMEWORK_ALIGNMENT.items():
            for entry in alignments:
                if fid in entry["frameworks"] or kb["name"] in entry["frameworks"]:
                    alignment.append(entry["note"])

        # Profitability link from static mapping
        prof_link = ""
        for prof_key, prof_val in FRAMEWORK_PROFITABILITY.items():
            if prof_key.upper() in fid.upper() or fid.upper() in prof_key.upper():
                prof_link = prof_val
                break

        matches.append(FrameworkMatch(
            framework_id=fid,
            framework_name=kb["name"],
            triggered_sections=triggered_sections.get(fid, []),
            triggered_questions=triggered_questions.get(fid, []),
            compliance_implications=implications,
            cross_industry_metrics=cross_metrics.get(fid, []),
            relevance_score=capped_score,
            alignment_notes=alignment,
            profitability_link=prof_link,
        ))

    matches.sort(key=lambda m: m.relevance_score, reverse=True)
    # Drop truly irrelevant matches (below 0.2) before returning — aligns with frontend LOW_THRESHOLD
    return [m for m in matches if m.relevance_score >= 0.2]


async def _llm_fallback_matching(
    article_content: str,
    esg_themes: list[str] | None = None,
) -> list[FrameworkMatch]:
    """LLM-assisted framework matching when rule-based matching is insufficient.

    Only invoked when rule-based matching returns fewer than 2 frameworks.
    Uses the FAST_MODEL to keep costs low.
    """
    if not llm.is_configured():
        logger.warning("llm_not_configured_for_framework_rag")
        return []

    framework_list = "\n".join(
        f"- {fid}: {kb['name']}" for fid, kb in FRAMEWORK_KNOWLEDGE.items()
    )

    prompt = f"""Given this ESG-related article content and the list of sustainability reporting frameworks,
identify which frameworks are applicable. For each applicable framework, list the specific
section codes that are triggered.

ESG Themes detected: {', '.join(esg_themes or ['none'])}

Article content (first 6000 chars):
{article_content[:6000]}

Available frameworks:
{framework_list}

Respond in JSON format:
{{
  "matches": [
    {{
      "framework_id": "BRSR",
      "sections": ["BRSR:P6", "BRSR:Core"],
      "rationale": "Article discusses emissions which triggers P6 and BRSR Core metrics"
    }}
  ]
}}

Return only the JSON, no other text."""

    try:
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            system="You are an ESG regulatory expert. Match article content to applicable frameworks with specific section citations.",
            model=llm.FAST_MODEL,
            max_tokens=1500,
            temperature=0.1,
        )

        # Parse response
        response_text = response.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        parsed = json.loads(response_text)

        matches: list[FrameworkMatch] = []
        for m in parsed.get("matches", []):
            fid = m.get("framework_id", "")
            kb = FRAMEWORK_KNOWLEDGE.get(fid)
            if not kb:
                continue

            sections = m.get("sections", [])
            implications: list[str] = []
            for sec in sections:
                prov = kb["provisions"].get(sec, {})
                if prov.get("description"):
                    implications.append(f"{sec}: {prov['description']}")

            matches.append(FrameworkMatch(
                framework_id=fid,
                framework_name=kb["name"],
                triggered_sections=sections,
                compliance_implications=implications,
                cross_industry_metrics=kb.get("cross_industry_metrics", []) if fid == "TCFD" else [],
                relevance_score=0.5,  # moderate confidence from LLM
                alignment_notes=[],
            ))

        logger.info("llm_framework_matching_complete", match_count=len(matches))
        return matches

    except Exception as e:
        logger.warning("llm_framework_matching_failed", error=str(e))
        return []


async def retrieve_applicable_frameworks(
    esg_themes: list[str] | None = None,
    article_content: str = "",
    article_title: str = "",
    use_llm_fallback: bool = True,
    company_region: str | None = None,
    company_market_cap: str | None = None,
) -> list[FrameworkMatch]:
    """Main entry point — retrieve applicable frameworks for an article.

    Combines Module 3 ESG theme tags with article text to identify which of
    the 13 framework knowledge bases apply, returning specific section-level
    citations and compliance implications.

    Args:
        esg_themes: ESG theme tags from Module 3 theme classifier.
        article_content: Full or extracted article text.
        article_title: Article headline.
        use_llm_fallback: Whether to invoke LLM when rule-based matching
            returns fewer than 2 matches. Default True.
        company_region: Company headquarter region for region-based framework
            boosting (e.g. "INDIA", "EU", "US", "UK", "APAC").
        company_market_cap: Cap category string (e.g. "Large Cap", "Mid Cap")
            used for mandatory framework detection.

    Returns:
        List of FrameworkMatch objects, sorted by relevance_score descending.
    """
    combined_text = f"{article_title} {article_content}"

    logger.info(
        "framework_rag_retrieve",
        themes=esg_themes,
        title_len=len(article_title),
        content_len=len(article_content),
        company_region=company_region,
    )

    # Step 1: Rule-based matching (with regional boost)
    matches = _match_frameworks_by_triggers(combined_text, esg_themes, company_region=company_region)

    logger.info(
        "framework_rag_rule_matches",
        count=len(matches),
        frameworks=[m.framework_id for m in matches],
    )

    # Step 2: LLM fallback if rule-based is insufficient
    if len(matches) < 2 and use_llm_fallback and article_content:
        llm_matches = await _llm_fallback_matching(article_content, esg_themes)

        # Merge LLM matches with existing (deduplicate by framework_id)
        existing_ids = {m.framework_id for m in matches}
        for lm in llm_matches:
            if lm.framework_id not in existing_ids:
                matches.append(lm)
            else:
                # Merge sections from LLM into existing match
                existing = next(m for m in matches if m.framework_id == lm.framework_id)
                for sec in lm.triggered_sections:
                    if sec not in existing.triggered_sections:
                        existing.triggered_sections.append(sec)

        matches.sort(key=lambda m: m.relevance_score, reverse=True)
        logger.info(
            "framework_rag_after_llm_fallback",
            count=len(matches),
            frameworks=[m.framework_id for m in matches],
        )

    # Step 2b: Apply mandatory framework detection using company region + market cap
    if company_region or company_market_cap:
        try:
            from backend.services.mandatory_frameworks import is_framework_mandatory
            for match in matches:
                match.is_mandatory = is_framework_mandatory(
                    match.framework_id, company_region, company_market_cap
                )
        except Exception:
            pass  # mandatory detection is best-effort

    # Step 3: Enrich with cross-framework alignment notes
    matched_ids = {m.framework_id for m in matches}
    for match in matches:
        for _topic, alignments in CROSS_FRAMEWORK_ALIGNMENT.items():
            for entry in alignments:
                # Add alignment note if at least 2 matched frameworks appear in the alignment group
                frameworks_in_alignment = entry["frameworks"]
                overlap_count = sum(
                    1 for mid in matched_ids
                    if mid in frameworks_in_alignment
                    or FRAMEWORK_KNOWLEDGE.get(mid, {}).get("name", "") in frameworks_in_alignment
                )
                if overlap_count >= 2 and entry["note"] not in match.alignment_notes:
                    match.alignment_notes.append(entry["note"])

    return matches


def get_all_framework_ids() -> list[str]:
    """Return all 13 framework IDs."""
    return list(FRAMEWORK_KNOWLEDGE.keys())


def get_framework_summary(framework_id: str) -> dict[str, Any] | None:
    """Return a summary of a framework (name, provision count, cross-references)."""
    kb = FRAMEWORK_KNOWLEDGE.get(framework_id)
    if not kb:
        return None
    return {
        "framework_id": framework_id,
        "name": kb["name"],
        "provision_count": len(kb["provisions"]),
        "section_codes": list(kb["provisions"].keys()),
        "retrieval_triggers": kb.get("retrieval_triggers", []),
        "cross_references": kb.get("cross_references", []),
    }


def serialize_matches(matches: list[FrameworkMatch]) -> list[dict[str, Any]]:
    """Serialize a list of FrameworkMatch to JSON-safe dicts (for Article.framework_matches)."""
    return [m.to_dict() for m in matches]
