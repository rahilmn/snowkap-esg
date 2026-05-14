"""L5 — SOP phase-gate state machine.

Explicit, audited state machine for the 4-phase company onboarding flow.
Replaces the implicit transitions scattered through `company_onboarder.py`
and `admin_onboard.py` so every state change is:
  1. Validated against the legal-transition graph (no skipping states)
  2. Audited via `append_decision` with L2 tags (signal_type=cascade_computation,
     scope=tenant) so L4 audit-the-audit can verify the trail
  3. Toulmin'd (actor=who, reason=why) per the L3 citation cap

The 5 states mirror `engine/models/onboarding_status.py` exactly so a
follow-up PR can swap the implicit transitions for `PhaseGate.advance()`
calls without renaming churn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from engine.audit import append_decision, make_toulmin


class PhaseState(str, Enum):
    PENDING = "pending"
    FETCHING = "fetching"
    ANALYSING = "analysing"
    READY = "ready"
    FAILED = "failed"


# Legal transitions: from_state → set of allowed to_states.
# READY and FAILED are terminal (empty set).
LEGAL_TRANSITIONS: dict[PhaseState, set[PhaseState]] = {
    PhaseState.PENDING: {PhaseState.FETCHING, PhaseState.FAILED},
    PhaseState.FETCHING: {PhaseState.ANALYSING, PhaseState.FAILED},
    PhaseState.ANALYSING: {PhaseState.READY, PhaseState.FAILED},
    PhaseState.READY: set(),
    PhaseState.FAILED: set(),
}


class PhaseGateError(RuntimeError):
    """Raised when an illegal transition is attempted."""


@dataclass
class PhaseGate:
    """A per-tenant onboarding state machine with audited transitions.

    Construct one per onboard. Call `advance(to_state, actor, reason)`
    at each phase boundary. Reading `gate.state` returns the current state.
    """
    tenant: str
    state: PhaseState = PhaseState.PENDING
    audit_dir: Path | None = None
    history: list[tuple[PhaseState, PhaseState, str]] = field(default_factory=list)

    def advance(self, to_state: PhaseState, *, actor: str, reason: str) -> None:
        """Transition to `to_state`, validating + auditing.

        Args:
            to_state: target state.
            actor: who initiated the transition (module slug or
                `manual:<email>`). Routed into both `tags.attribution`
                AND the Toulmin claim.
            reason: free-form rationale (1 line). Routed into Toulmin grounds.

        Raises:
            PhaseGateError: on illegal transition or attempted advance
                from a terminal state.
        """
        allowed = LEGAL_TRANSITIONS.get(self.state, set())
        if not allowed:
            raise PhaseGateError(
                f"phase_gate(tenant={self.tenant}): state {self.state.value} is "
                f"terminal; cannot advance to {to_state.value}"
            )
        if to_state not in allowed:
            raise PhaseGateError(
                f"phase_gate(tenant={self.tenant}): illegal transition "
                f"{self.state.value} → {to_state.value} (legal targets: "
                f"{[s.value for s in allowed]})"
            )

        # Audit the transition (uses L2 tags + L3 Toulmin caps)
        # automated=True iff attribution is a module slug (not manual:)
        is_manual = actor.startswith("manual:")
        toulmin = make_toulmin(
            claim=f"phase {self.state.value} → {to_state.value} by {actor}",
            grounds=[reason],
            warrant=f"legal transition per LEGAL_TRANSITIONS[{self.state.value}]",
        )
        # Reuse the closest decision_type — phase advance is a form of
        # tier_shift (the engine moved the tenant up the onboarding ladder).
        # If we add more decision types later, swap this for "phase_advance".
        append_decision(
            "tier_shift",
            company_slug=self.tenant,
            before=self.state.value,
            after=to_state.value,
            toulmin=toulmin,
            automated=not is_manual,
            tags={
                "scope": "tenant",
                "signal_type": "cascade_computation",
                "attribution": actor,
                "uncertainty": "low",
            },
            base_data_dir=self.audit_dir,
        )

        prev = self.state
        self.state = to_state
        self.history.append((prev, to_state, reason))
