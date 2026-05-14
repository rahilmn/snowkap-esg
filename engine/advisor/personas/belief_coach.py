"""Belief Coach — surfaces pending BeliefProposals from R1-R6.

Fires when an L7 belief-revision rule queues a proposal that needs
human review (`high` confidence proposals auto-apply; `moderate` and
`low` route here).
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor.engine import AdvisorHint
from engine.advisor.events import AdvisorEvent, BeliefRevisionEvent


_REVIEWABLE_CONFIDENCES = frozenset({"moderate", "low"})


@dataclass
class BeliefCoach:
    name: str = "belief_coach"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        if not isinstance(event, BeliefRevisionEvent):
            return []
        confidence = str(event.payload.get("confidence") or "").lower()
        if confidence not in _REVIEWABLE_CONFIDENCES:
            return []
        belief_name = str(event.payload.get("belief_name") or "unknown_belief")
        rule_id = str(event.payload.get("rule_id") or "R?")
        new_value = event.payload.get("new_value")
        tenant = event.tenant or "unknown"
        severity = "high" if confidence == "low" else "moderate"
        return [AdvisorHint(
            coach=self.name,
            kind="belief_proposal_pending",
            severity=severity,
            headline=f"{tenant}: belief proposal awaiting review",
            body=(
                f"Rule {rule_id} proposes {belief_name}={new_value!r} "
                f"with {confidence} confidence. Review in the advisor queue."
            ),
            dedup_key=f"belief_coach:{tenant}:{belief_name}:{rule_id}",
            tenant=event.tenant,
            cta_label="Open advisor queue →",
            cta_target="/settings/advisor",
        )]
