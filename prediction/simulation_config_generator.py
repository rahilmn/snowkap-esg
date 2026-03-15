"""Simulation configuration generator — builds sim config from tenant + news context.

Per MASTER_BUILD_PLAN Phase 4:
- Config from tenant settings + news context
- Tenant config JSONB → sim params
"""

from dataclasses import dataclass, field

from prediction.config import mirofish_settings


@dataclass
class SimulationConfig:
    """Configuration for a MiroFish prediction simulation."""
    # Simulation parameters
    agent_count: int = 20
    rounds: int = 10
    timeout_seconds: int = 300

    # Context
    company_name: str = ""
    company_industry: str = ""
    tenant_id: str = ""

    # News event
    article_title: str = ""
    article_summary: str = ""
    article_entities: list[str] = field(default_factory=list)

    # Causal chain context
    causal_chain_path: list[str] = field(default_factory=list)
    causal_chain_hops: int = 0
    impact_score: float = 0.0
    relationship_type: str = "directOperational"

    # ESG context
    esg_pillar: str | None = None  # E, S, G
    frameworks: list[str] = field(default_factory=list)
    material_issues: list[str] = field(default_factory=list)

    # Financial context
    estimated_financial_exposure: float | None = None
    currency: str = "INR"

    # Supply chain context
    affected_suppliers: list[str] = field(default_factory=list)
    affected_commodities: list[str] = field(default_factory=list)
    scope3_categories: list[str] = field(default_factory=list)

    # Geographic context
    affected_locations: list[str] = field(default_factory=list)
    affected_facilities: list[str] = field(default_factory=list)
    climate_risk_zones: list[str] = field(default_factory=list)

    def to_scenario_prompt(self) -> str:
        """Generate the scenario description for agents to reason about."""
        parts = [
            f"## ESG Scenario for {self.company_name} ({self.company_industry})",
            "",
            f"### News Event",
            f"**{self.article_title}**",
            f"{self.article_summary}" if self.article_summary else "",
            "",
        ]

        if self.causal_chain_path:
            chain_str = " → ".join(self.causal_chain_path)
            parts.append(f"### Causal Chain ({self.causal_chain_hops} hops, {self.relationship_type})")
            parts.append(f"{chain_str}")
            parts.append(f"Impact score: {self.impact_score:.2f}")
            parts.append("")

        if self.esg_pillar:
            pillar_full = {"E": "Environmental", "S": "Social", "G": "Governance"}.get(self.esg_pillar, self.esg_pillar)
            parts.append(f"### ESG Classification: {pillar_full}")

        if self.frameworks:
            parts.append(f"Relevant frameworks: {', '.join(self.frameworks)}")
        if self.material_issues:
            parts.append(f"Material issues: {', '.join(self.material_issues)}")
        parts.append("")

        if self.estimated_financial_exposure:
            parts.append(f"### Financial Exposure")
            parts.append(f"Estimated: ₹{self.estimated_financial_exposure:,.0f}")
            parts.append("")

        if self.affected_suppliers:
            parts.append(f"### Supply Chain Impact")
            parts.append(f"Affected suppliers: {', '.join(self.affected_suppliers)}")
            if self.affected_commodities:
                parts.append(f"Affected commodities: {', '.join(self.affected_commodities)}")
            if self.scope3_categories:
                parts.append(f"Scope 3 categories: {', '.join(self.scope3_categories)}")
            parts.append("")

        if self.affected_locations:
            parts.append(f"### Geographic Impact")
            parts.append(f"Locations: {', '.join(self.affected_locations)}")
            if self.affected_facilities:
                parts.append(f"Facilities: {', '.join(self.affected_facilities)}")
            if self.climate_risk_zones:
                parts.append(f"Climate risk zones: {', '.join(self.climate_risk_zones)}")
            parts.append("")

        parts.extend([
            "### Questions for Analysis",
            "1. What is the short-term (0-6 months) impact on this company?",
            "2. What is the medium-term (6-24 months) impact?",
            "3. What are the financial implications (quantify in INR)?",
            "4. What ESG reporting obligations are triggered?",
            "5. What mitigation actions should the company take?",
            "6. Are there any opportunities hidden in this scenario?",
        ])

        return "\n".join(parts)


def generate_config(
    company_data: dict,
    article_data: dict,
    causal_chain_data: dict | None = None,
    tenant_config: dict | None = None,
) -> SimulationConfig:
    """Generate a SimulationConfig from company, article, and causal chain data.

    Per MASTER_BUILD_PLAN: tenant config JSONB → sim params.
    """
    # Base config from MiroFish settings
    agent_count = mirofish_settings.DEFAULT_AGENT_COUNT
    rounds = mirofish_settings.DEFAULT_ROUNDS
    timeout = mirofish_settings.SIMULATION_TIMEOUT_SECONDS

    # Override from tenant config if available
    if tenant_config:
        mf_config = tenant_config.get("mirofish_config", {})
        agent_count = mf_config.get("agent_count", agent_count)
        rounds = mf_config.get("rounds", rounds)
        timeout = mf_config.get("timeout_seconds", timeout)

    # Scale based on impact severity
    impact_score = 0.0
    if causal_chain_data:
        impact_score = causal_chain_data.get("impact_score", 0)
        # Higher impact = more agents + more rounds
        if impact_score > 80:
            agent_count = min(agent_count + 15, mirofish_settings.MAX_AGENT_COUNT)
            rounds = min(rounds + 10, mirofish_settings.MAX_ROUNDS)
        elif impact_score > 60:
            agent_count = min(agent_count + 5, mirofish_settings.MAX_AGENT_COUNT)
            rounds = min(rounds + 5, mirofish_settings.MAX_ROUNDS)

    config = SimulationConfig(
        agent_count=agent_count,
        rounds=rounds,
        timeout_seconds=timeout,
        company_name=company_data.get("name", ""),
        company_industry=company_data.get("industry", ""),
        tenant_id=company_data.get("tenant_id", ""),
        article_title=article_data.get("title", ""),
        article_summary=article_data.get("summary", ""),
        article_entities=article_data.get("entities", []),
        esg_pillar=article_data.get("esg_pillar"),
        estimated_financial_exposure=article_data.get("financial_exposure"),
    )

    if causal_chain_data:
        config.causal_chain_path = causal_chain_data.get("chain_path", [])
        config.causal_chain_hops = causal_chain_data.get("hops", 0)
        config.impact_score = causal_chain_data.get("impact_score", 0)
        config.relationship_type = causal_chain_data.get("relationship_type", "directOperational")
        config.frameworks = causal_chain_data.get("framework_alignment", [])

    return config
