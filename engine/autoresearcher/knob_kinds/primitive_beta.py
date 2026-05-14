"""Primitive-β knob — tunes the elasticity coefficient on a single
causal edge (P→P or P→outcome) in the ontology.

171 atomic knobs (one per edge). State is shared via
`PrimitiveBetaState` snapshot keyed by edge_id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError


@dataclass
class PrimitiveBetaState:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, edge_id: str, default: float = 0.0) -> float:
        return self.values.get(edge_id, default)

    def set(self, edge_id: str, value: float) -> None:
        self.values[edge_id] = value


class PrimitiveBetaKnob(Knob):
    kind = "primitive_beta"

    def __init__(
        self,
        *,
        edge_id: str,
        state: PrimitiveBetaState,
        magnitude: float = 0.10,
    ):
        super().__init__(knob_id=f"beta:{edge_id}")
        self.edge_id = edge_id
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(edge_id)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.edge_id)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be float, got {new_value!r}") from exc
        # β should generally stay in [-1, 1]; outside is suspicious
        if v < -1.5 or v > 1.5:
            raise KnobError(f"β={v} outside soft bounds [-1.5, 1.5]")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δβ|={delta:.4f} > magnitude_bound={self._magnitude:.4f} for {self.edge_id}"
            )
        self._prev = self.current_value()
        self._state.set(self.edge_id, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.edge_id, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "edge_id": self.edge_id,
            "value": round(self.current_value(), 6),
            "baseline": round(self.baseline_value(), 6),
            "magnitude_bound": self._magnitude,
        }
