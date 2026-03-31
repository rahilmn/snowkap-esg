"""5-Dimension ESG Relevance Scoring (Phase 1).

Scores each article across 5 dimensions (0-2 each, total 0-10):
1. ESG Correlation — direct/indirect ESG theme linkage
2. Financial Impact — revenue/expenses (compliance costs, fines, capex, remediation, carbon tax)/margins/valuation effect
3. Compliance Risk — regulatory/disclosure implications
4. Supply Chain Impact — Tier 1/2/3 supplier effects
5. People Impact — employees/communities/consumers

Articles scoring ≥7 qualify for Home feed + deep insight generation.
Articles scoring 4-6 go to secondary feed.
Articles scoring <4 are rejected.
"""

from dataclasses import dataclass


@dataclass
class RelevanceScore:
    """5-dimension ESG relevance score."""
    esg_correlation: int = 0        # 0-2
    financial_impact: int = 0       # 0-2
    compliance_risk: int = 0        # 0-2
    supply_chain_impact: int = 0    # 0-2
    people_impact: int = 0          # 0-2

    @property
    def total(self) -> float:
        return float(
            self.esg_correlation
            + self.financial_impact
            + self.compliance_risk
            + self.supply_chain_impact
            + self.people_impact
        )

    @property
    def qualified_for_home(self) -> bool:
        # Hard filter: ESG Correlation = 0 → never HOME regardless of total
        if self.esg_correlation == 0:
            return False
        return self.total >= 7

    @property
    def tier(self) -> str:
        """HOME (≥7 AND esg_correlation>0), SECONDARY (4-6), REJECTED (<4 or esg=0+low)."""
        if self.esg_correlation == 0:
            # No ESG nexus → cannot be HOME, cap at SECONDARY
            return "SECONDARY" if self.total >= 4 else "REJECTED"
        if self.total >= 7:
            return "HOME"
        elif self.total >= 4:
            return "SECONDARY"
        return "REJECTED"

    def to_dict(self) -> dict:
        return {
            "esg_correlation": self.esg_correlation,
            "financial_impact": self.financial_impact,
            "compliance_risk": self.compliance_risk,
            "supply_chain_impact": self.supply_chain_impact,
            "people_impact": self.people_impact,
            "total": self.total,
            "tier": self.tier,
        }


def parse_relevance_from_llm(data: dict) -> RelevanceScore | None:
    """Parse relevance scores from LLM extraction response."""
    rel = data.get("relevance")
    if not isinstance(rel, dict):
        return None

    def clamp(v: int | None) -> int:
        if not isinstance(v, (int, float)):
            return 0
        return max(0, min(2, int(v)))

    return RelevanceScore(
        esg_correlation=clamp(rel.get("esg_correlation")),
        financial_impact=clamp(rel.get("financial_impact")),
        compliance_risk=clamp(rel.get("compliance_risk")),
        supply_chain_impact=clamp(rel.get("supply_chain_impact")),
        people_impact=clamp(rel.get("people_impact")),
    )
