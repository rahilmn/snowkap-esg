"""Primitive-lag knob — tunes the `lagK` (lag in months/quarters) on a
single causal edge. Sibling to PrimitiveBetaKnob; same state pattern
but a different magnitude bound (lags are integers in [0, 12]).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError


@dataclass
class PrimitiveLagState:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, edge_id: str, default: float = 0.0) -> float:
        return self.values.get(edge_id, default)

    def set(self, edge_id: str, value: float) -> None:
        self.values[edge_id] = value


class PrimitiveLagKnob(Knob):
    kind = "primitive_lag"

    def __init__(
        self,
        *,
        edge_id: str,
        state: PrimitiveLagState,
        magnitude: float = 1.0,  # ±1 month per experiment
    ):
        super().__init__(knob_id=f"lag:{edge_id}")
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
            raise KnobError(f"new_value must be numeric, got {new_value!r}") from exc
        if v < 0 or v > 12:
            raise KnobError(f"lag {v} outside [0, 12] months")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δlag|={delta:.2f} > magnitude_bound={self._magnitude:.2f}"
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
            "value": round(self.current_value(), 4),
            "baseline": round(self.baseline_value(), 4),
            "magnitude_bound": self._magnitude,
        }
