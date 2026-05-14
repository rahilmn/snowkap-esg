"""Risk-threshold knob — tunes one of the 25 canonical τ thresholds
(e.g. drought PDSI ≤ -3, energy-price YoY ≥ 25%).

Each threshold has a domain-specific scale. We store + tune them as
raw floats; downstream callers compare against the same units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError


@dataclass
class RiskThresholdState:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, threshold_id: str, default: float = 0.0) -> float:
        return self.values.get(threshold_id, default)

    def set(self, threshold_id: str, value: float) -> None:
        self.values[threshold_id] = value


class RiskThresholdKnob(Knob):
    kind = "risk_threshold"

    def __init__(
        self,
        *,
        threshold_id: str,
        state: RiskThresholdState,
        magnitude: float = 0.15,
    ):
        super().__init__(knob_id=f"tau:{threshold_id}")
        self.threshold_id = threshold_id
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(threshold_id)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.threshold_id)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be numeric, got {new_value!r}") from exc
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δτ|={delta:.4f} > magnitude_bound={self._magnitude:.4f}"
            )
        self._prev = self.current_value()
        self._state.set(self.threshold_id, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.threshold_id, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "threshold_id": self.threshold_id,
            "value": round(self.current_value(), 6),
            "baseline": round(self.baseline_value(), 6),
            "magnitude_bound": self._magnitude,
        }
