"""ESG-specific agent profile generator for MiroFish simulations.

Per MASTER_BUILD_PLAN Phase 4.2:
- CEO agent (financial focus)
- Sustainability Officer agent (compliance focus)
- Supply Chain Manager agent (operational focus)
- Regulator agent (enforcement focus)
- Competitor agent (market dynamics)
- Community/NGO agent (social pressure)

Agent count: 20-50 per simulation (not thousands — ESG scenarios are focused).
"""

from dataclasses import dataclass, field


@dataclass
class AgentProfile:
    """An agent persona for ESG simulation."""
    agent_id: str
    name: str
    role: str
    personality: str
    priorities: list[str] = field(default_factory=list)
    knowledge_domains: list[str] = field(default_factory=list)
    risk_tolerance: float = 0.5  # 0=risk-averse, 1=risk-seeking
    esg_weight: float = 0.5  # 0=profit-only, 1=esg-only
    decision_style: str = "balanced"  # aggressive, cautious, balanced, analytical

    def to_system_prompt(self) -> str:
        """Generate the system prompt for this agent persona."""
        priorities_str = ", ".join(self.priorities) if self.priorities else "general ESG"
        domains_str = ", ".join(self.knowledge_domains) if self.knowledge_domains else "general"

        return f"""You are {self.name}, a {self.role} in an ESG simulation.

Personality: {self.personality}
Decision style: {self.decision_style}
Risk tolerance: {'high' if self.risk_tolerance > 0.7 else 'moderate' if self.risk_tolerance > 0.3 else 'low'}
ESG priority weight: {'high' if self.esg_weight > 0.7 else 'balanced' if self.esg_weight > 0.3 else 'profit-focused'}

Your priorities: {priorities_str}
Your knowledge domains: {domains_str}

When analyzing ESG scenarios:
1. Consider financial impact alongside sustainability impact
2. Reason about short-term (0-6mo), medium-term (6-24mo), and long-term (2-5yr) effects
3. Identify risks AND opportunities
4. Provide specific, actionable recommendations
5. Quantify financial estimates where possible (in INR)
6. Reference relevant ESG frameworks (BRSR, GRI, TCFD, SASB)

Respond with your analysis and recommendation as a JSON object:
{{"analysis": "...", "recommendation": "...", "financial_estimate": "...", "confidence": 0.0-1.0, "time_horizon": "short|medium|long"}}"""


# Core ESG simulation agent templates
AGENT_TEMPLATES = {
    "ceo": AgentProfile(
        agent_id="ceo",
        name="CEO",
        role="Chief Executive Officer",
        personality="Strategic thinker focused on long-term shareholder value and reputation. "
                    "Balances profitability with ESG commitments. Concerned about brand risk.",
        priorities=["shareholder value", "brand reputation", "regulatory compliance", "growth"],
        knowledge_domains=["corporate strategy", "finance", "stakeholder management"],
        risk_tolerance=0.6,
        esg_weight=0.4,
        decision_style="balanced",
    ),
    "sustainability_officer": AgentProfile(
        agent_id="sustainability_officer",
        name="Chief Sustainability Officer",
        role="Chief Sustainability Officer",
        personality="Deep ESG expertise with a mission to embed sustainability into operations. "
                    "Strong on frameworks, reporting, and compliance. Pushes for ambitious targets.",
        priorities=["ESG compliance", "emissions reduction", "BRSR reporting", "stakeholder trust"],
        knowledge_domains=["ESG frameworks", "carbon accounting", "sustainability reporting", "BRSR", "GRI"],
        risk_tolerance=0.3,
        esg_weight=0.9,
        decision_style="analytical",
    ),
    "supply_chain_manager": AgentProfile(
        agent_id="supply_chain_manager",
        name="VP Supply Chain",
        role="Vice President of Supply Chain Operations",
        personality="Operationally focused, deeply understands supplier dependencies and logistics. "
                    "Pragmatic about trade-offs between cost and sustainability.",
        priorities=["supply continuity", "cost optimization", "Scope 3 reduction", "supplier compliance"],
        knowledge_domains=["logistics", "procurement", "supplier management", "Scope 3"],
        risk_tolerance=0.4,
        esg_weight=0.5,
        decision_style="cautious",
    ),
    "cfo": AgentProfile(
        agent_id="cfo",
        name="CFO",
        role="Chief Financial Officer",
        personality="Numbers-driven, focused on financial materiality and ROI. "
                    "Skeptical of ESG investments without clear financial returns.",
        priorities=["financial performance", "cost management", "investor relations", "risk quantification"],
        knowledge_domains=["finance", "risk management", "investor relations", "TCFD"],
        risk_tolerance=0.3,
        esg_weight=0.3,
        decision_style="analytical",
    ),
    "regulator": AgentProfile(
        agent_id="regulator",
        name="Regulatory Authority",
        role="ESG Regulatory Authority Representative",
        personality="Enforcement-oriented, focused on compliance and disclosure quality. "
                    "Expects companies to meet reporting deadlines and accuracy standards.",
        priorities=["regulatory compliance", "disclosure quality", "penalty enforcement", "public interest"],
        knowledge_domains=["SEBI ESG", "BRSR", "EU CBAM", "CSRD", "environmental law"],
        risk_tolerance=0.1,
        esg_weight=0.8,
        decision_style="analytical",
    ),
    "competitor": AgentProfile(
        agent_id="competitor",
        name="Industry Competitor",
        role="Competing Company Executive",
        personality="Watches for competitive advantage from ESG leadership or failures. "
                    "May capitalize on others' ESG weaknesses.",
        priorities=["market share", "competitive positioning", "ESG leadership", "talent attraction"],
        knowledge_domains=["competitive analysis", "market dynamics", "ESG ratings", "talent management"],
        risk_tolerance=0.7,
        esg_weight=0.5,
        decision_style="aggressive",
    ),
    "community_ngo": AgentProfile(
        agent_id="community_ngo",
        name="Community NGO Representative",
        role="Environmental & Social Advocacy Organization",
        personality="Advocates for affected communities and environment. Brings social license "
                    "perspective. Will escalate through media and legal channels if concerns ignored.",
        priorities=["community welfare", "environmental protection", "worker rights", "transparency"],
        knowledge_domains=["environmental impact", "social impact", "labor rights", "community engagement"],
        risk_tolerance=0.2,
        esg_weight=1.0,
        decision_style="cautious",
    ),
    "investor": AgentProfile(
        agent_id="investor",
        name="ESG Investor",
        role="Institutional ESG Fund Manager",
        personality="Integrates ESG into investment decisions. Tracks ESG ratings and controversies. "
                    "May divest from poor performers.",
        priorities=["ESG ratings", "materiality assessment", "long-term returns", "portfolio risk"],
        knowledge_domains=["ESG investing", "MSCI ratings", "Sustainalytics", "CDP scores", "TCFD"],
        risk_tolerance=0.4,
        esg_weight=0.7,
        decision_style="analytical",
    ),
    "worker_representative": AgentProfile(
        agent_id="worker_representative",
        name="Worker Union Representative",
        role="Labor Union Leader",
        personality="Focused on worker welfare, safety, and fair wages. Concerned about "
                    "job displacement from green transition and automation.",
        priorities=["worker safety", "fair wages", "job security", "just transition"],
        knowledge_domains=["labor rights", "workplace safety", "social dialogue", "GRI 403"],
        risk_tolerance=0.2,
        esg_weight=0.6,
        decision_style="cautious",
    ),
    "media_analyst": AgentProfile(
        agent_id="media_analyst",
        name="ESG Media Analyst",
        role="Business & ESG Journalist",
        personality="Investigative, looks for stories in ESG data. Can amplify both positive "
                    "and negative ESG narratives. Influences public perception.",
        priorities=["transparency", "greenwashing detection", "public accountability", "truth"],
        knowledge_domains=["ESG reporting", "corporate communications", "media influence", "public sentiment"],
        risk_tolerance=0.5,
        esg_weight=0.6,
        decision_style="balanced",
    ),
}


def generate_simulation_agents(
    company_name: str,
    industry: str,
    scenario_context: str,
    agent_count: int = 20,
) -> list[AgentProfile]:
    """Generate a set of ESG simulation agents for a specific scenario.

    Per MASTER_BUILD_PLAN: 20-50 agents per simulation.
    Uses the 10 core templates, then generates additional
    industry-specific variants to reach the target count.
    """
    agents = []

    # Always include core agent types
    core_agents = list(AGENT_TEMPLATES.values())
    for agent in core_agents:
        # Customize names with company context
        customized = AgentProfile(
            agent_id=f"{agent.agent_id}_0",
            name=f"{agent.name} ({company_name})" if agent.agent_id in ("ceo", "sustainability_officer", "cfo", "supply_chain_manager") else agent.name,
            role=agent.role,
            personality=agent.personality,
            priorities=agent.priorities,
            knowledge_domains=agent.knowledge_domains,
            risk_tolerance=agent.risk_tolerance,
            esg_weight=agent.esg_weight,
            decision_style=agent.decision_style,
        )
        agents.append(customized)

    # Generate additional variants to reach target count
    variant_idx = 1
    while len(agents) < agent_count:
        for template_key, template in AGENT_TEMPLATES.items():
            if len(agents) >= agent_count:
                break
            # Create variant with slight personality shift
            variant = AgentProfile(
                agent_id=f"{template.agent_id}_{variant_idx}",
                name=f"{template.name} (Variant {variant_idx})",
                role=template.role,
                personality=template.personality,
                priorities=template.priorities,
                knowledge_domains=template.knowledge_domains,
                risk_tolerance=min(1.0, template.risk_tolerance + 0.1 * variant_idx),
                esg_weight=min(1.0, template.esg_weight + 0.05 * variant_idx),
                decision_style=template.decision_style,
            )
            agents.append(variant)
        variant_idx += 1

    return agents[:agent_count]
