"""Ontology generator — uses SNOWKAP's sustainability.ttl base ontology.

Per MASTER_BUILD_PLAN Phase 4:
- Shared OWL classes + ESG domain
- Generates simulation-specific ontology extensions
"""

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# ESG dimension definitions for simulation reasoning
ESG_DIMENSIONS = {
    "environmental": {
        "label": "Environmental (E)",
        "topics": [
            "greenhouse_gas_emissions",
            "energy_management",
            "water_management",
            "waste_management",
            "biodiversity",
            "air_quality",
            "ecological_impacts",
        ],
        "frameworks": {
            "BRSR": "Principle 6 (Environment)",
            "GRI": "GRI 300 series",
            "TCFD": "Metrics & Targets",
            "CDP": "Climate Change, Water, Forests",
            "SASB": "Environment dimension",
        },
    },
    "social": {
        "label": "Social (S)",
        "topics": [
            "labor_practices",
            "worker_health_safety",
            "community_relations",
            "human_rights",
            "supply_chain_labor",
            "product_safety",
            "data_privacy",
            "diversity_inclusion",
        ],
        "frameworks": {
            "BRSR": "Principles 3, 5 (Employees, Human Rights)",
            "GRI": "GRI 400 series",
            "SASB": "Social Capital, Human Capital",
        },
    },
    "governance": {
        "label": "Governance (G)",
        "topics": [
            "board_composition",
            "executive_compensation",
            "business_ethics",
            "anti_corruption",
            "risk_management",
            "regulatory_compliance",
            "transparency",
        ],
        "frameworks": {
            "BRSR": "Principles 1, 9 (Ethics, Accountability)",
            "GRI": "GRI 200 series",
            "TCFD": "Governance",
        },
    },
}

# Scenario archetypes for common ESG events
SCENARIO_ARCHETYPES = {
    "commodity_price_shock": {
        "description": "Sudden change in commodity prices (oil, steel, LPG, etc.)",
        "typical_hops": 2,
        "affected_dimensions": ["environmental", "social"],
        "key_questions": [
            "How does this propagate through the supply chain?",
            "What workforce segments are affected?",
            "What Scope 3 categories are impacted?",
            "What cost mitigation options exist?",
        ],
    },
    "regulatory_change": {
        "description": "New ESG regulation or policy change",
        "typical_hops": 1,
        "affected_dimensions": ["governance", "environmental"],
        "key_questions": [
            "What compliance deadlines are triggered?",
            "What disclosure requirements change?",
            "What is the compliance cost estimate?",
            "What competitive implications exist?",
        ],
    },
    "climate_event": {
        "description": "Natural disaster, extreme weather, or climate impact",
        "typical_hops": 0,
        "affected_dimensions": ["environmental", "social"],
        "key_questions": [
            "Which facilities are directly affected?",
            "What is the operational disruption timeline?",
            "What insurance/recovery costs are expected?",
            "What climate adaptation investments are needed?",
        ],
    },
    "supply_chain_disruption": {
        "description": "Supply chain failure, geopolitical disruption, or logistics crisis",
        "typical_hops": 1,
        "affected_dimensions": ["environmental", "social", "governance"],
        "key_questions": [
            "Which suppliers are affected?",
            "What alternative sourcing options exist?",
            "What is the production impact timeline?",
            "What Scope 3 reporting implications exist?",
        ],
    },
    "social_controversy": {
        "description": "Labor dispute, community conflict, or ESG scandal",
        "typical_hops": 0,
        "affected_dimensions": ["social", "governance"],
        "key_questions": [
            "What is the reputational damage estimate?",
            "What stakeholder groups are affected?",
            "What remediation steps are required?",
            "What ESG rating impact is expected?",
        ],
    },
    "technology_disruption": {
        "description": "New technology, energy transition, or industry transformation",
        "typical_hops": 1,
        "affected_dimensions": ["environmental", "governance"],
        "key_questions": [
            "What stranded asset risk exists?",
            "What transition investment is needed?",
            "What competitive advantage opportunities exist?",
            "What workforce reskilling is required?",
        ],
    },
}


def classify_scenario_archetype(
    article_title: str,
    esg_pillar: str | None,
    relationship_type: str | None,
    entities: list[str] | None = None,
) -> str:
    """Classify a news event into a scenario archetype for simulation tuning."""
    title_lower = article_title.lower()
    entities_lower = [e.lower() for e in (entities or [])]
    all_text = title_lower + " ".join(entities_lower)

    # Keyword-based classification
    if any(w in all_text for w in ["price", "cost", "surge", "commodity", "oil", "steel", "lpg"]):
        return "commodity_price_shock"
    if any(w in all_text for w in ["regulation", "policy", "law", "compliance", "sebi", "cbam", "brsr"]):
        return "regulatory_change"
    if any(w in all_text for w in ["flood", "cyclone", "drought", "earthquake", "climate", "weather", "disaster"]):
        return "climate_event"
    if any(w in all_text for w in ["supply chain", "supplier", "logistics", "disruption", "shortage"]):
        return "supply_chain_disruption"
    if any(w in all_text for w in ["strike", "protest", "scandal", "controversy", "labor", "safety incident"]):
        return "social_controversy"
    if any(w in all_text for w in ["technology", "ai", "automation", "renewable", "ev", "transition"]):
        return "technology_disruption"

    # Fallback based on ESG pillar
    if esg_pillar == "E":
        return "climate_event"
    if esg_pillar == "S":
        return "social_controversy"
    if esg_pillar == "G":
        return "regulatory_change"

    return "commodity_price_shock"  # Default


def get_archetype_context(archetype: str) -> dict:
    """Get the scenario archetype context for simulation configuration."""
    return SCENARIO_ARCHETYPES.get(archetype, SCENARIO_ARCHETYPES["commodity_price_shock"])
