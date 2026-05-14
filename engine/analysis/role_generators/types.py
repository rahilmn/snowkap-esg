"""Phase 3 §5.2 — RoleDistinctPayload + sub-types.

Locked-in contract that every Stage 11 role generator (CFO / CEO /
Analyst) emits. The shape mirrors the plan verbatim so the future
LLM-prompt versions can drop in without changing downstream
consumers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HeroMetric:
    """The hero number / phrase shown above the fold for this role.

    - CFO: value=₹X Cr, label="P&L exposure", decision_window=ISO date
    - CEO: value=strategic phrase, label="Strategic position", horizon="FY27-29"
    - Analyst: value=framework deadline, label="Disclosure trigger", deadline=ISO date

    A single dataclass for all three so the consumer doesn't branch on type.
    """
    value: str
    label: str
    decision_window: str = ""
    horizon: str = ""
    deadline: str = ""


@dataclass
class RecommendationStub:
    """Lightweight recommendation reference attached to a role payload.

    Full Recommendation objects continue to live in
    `engine.analysis.recommendation_engine`. The role payload carries
    a stub (title + budget + payback) so the consumer can render a
    summary list without having to re-load the full rec set.
    """
    title: str
    type: str = ""
    budget_cr: float | None = None
    payback_months: float | None = None
    framework_section: str = ""


@dataclass
class RoleDistinctPayload:
    """Per-role view emitted by Stage 11. Plan §5.2.

    Three independent generators (CFO / CEO / Analyst) each return one
    of these from the shared EvidencePack. The role-distinct content
    lives here; the canonical block (cascade, frameworks, stakeholders,
    causal chain, comparables, polarity, confidence_bounds, decision
    windows) stays shared in EvidencePack.
    """
    role: str  # "cfo" | "ceo" | "esg-analyst"
    headline: str
    hero_metric: HeroMetric
    role_takeaways: list[str] = field(default_factory=list)
    role_paragraph: str = ""
    recommendations: list[RecommendationStub] = field(default_factory=list)
    visible_panels: list[str] = field(default_factory=list)
    hidden_panels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
