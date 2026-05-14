"""Scorer-component knob — tunes a single weight in
`criticality_scorer.WEIGHTS_DEFAULT` or `WEIGHTS_BY_ROLE[role]`.

Operates on a `ScorerWeightState` snapshot that the evaluator
threads into `compute_criticality` via dependency injection.

Critical invariant: per-role weight sums must stay ≈ 1.0 (the scorer
already normalises internally, but the autoresearcher commits to
"single-knob nudges" only — multi-component rebalance is out of
scope for v1 to keep the search space well-behaved).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError

VALID_COMPONENTS = frozenset({
    "materiality",
    "financial_magnitude",
    "actionability",
    "painpoint_match",
    "recency",
    "source_authority",
})

VALID_ROLES = frozenset({"default", "cfo", "ceo", "analyst"})


@dataclass
class ScorerWeightState:
    """Snapshot of `WEIGHTS_DEFAULT` + `WEIGHTS_BY_ROLE`.

    Keyed by (role, component) → float. role="default" maps to the
    cross-role WEIGHTS_DEFAULT dict.
    """
    values: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, role: str, component: str, default: float = 0.0) -> float:
        return self.values.get((role, component), default)

    def set(self, role: str, component: str, value: float) -> None:
        self.values[(role, component)] = value

    @classmethod
    def from_scorer_module(cls) -> "ScorerWeightState":
        """Snapshot the current scorer constants into the state."""
        from engine.analysis.criticality_scorer import (
            WEIGHTS_BY_ROLE,
            WEIGHTS_DEFAULT,
        )
        state = cls()
        for comp, v in WEIGHTS_DEFAULT.items():
            state.set("default", comp, v)
        for role, m in WEIGHTS_BY_ROLE.items():
            for comp, v in m.items():
                state.set(role, comp, v)
        return state


class ScorerComponentKnob(Knob):
    """Tunes one component weight for one role."""

    kind = "scorer_component_weight"

    def __init__(
        self,
        *,
        role: str,
        component: str,
        state: ScorerWeightState,
        magnitude: float = 0.10,
    ):
        if role not in VALID_ROLES:
            raise KnobError(f"unknown role {role!r}")
        if component not in VALID_COMPONENTS:
            raise KnobError(f"unknown component {component!r}")
        knob_id = f"scorer:{role}:{component}"
        super().__init__(knob_id=knob_id)
        self.role = role
        self.component = component
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(role, component)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.role, self.component)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be float, got {new_value!r}") from exc
        if v < 0 or v > 1:
            raise KnobError(f"weight {v} outside [0, 1]")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δ|={delta:.4f} > magnitude_bound={self._magnitude:.4f}"
            )
        self._prev = self.current_value()
        self._state.set(self.role, self.component, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.role, self.component, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "role": self.role,
            "component": self.component,
            "value": round(self.current_value(), 6),
            "baseline": round(self.baseline_value(), 6),
            "magnitude_bound": self._magnitude,
        }
