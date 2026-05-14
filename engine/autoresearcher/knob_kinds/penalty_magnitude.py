"""Penalty-magnitude knob — tunes one of the criticality scorer's
penalty thresholds (staleness, confidence, polarity_drift).

These are the per-penalty multiplier values (default ~0.15-0.20).
Tunable inside a tight magnitude bound to avoid blowing up the
band-threshold invariants.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError

VALID_PENALTIES = frozenset({"staleness", "confidence", "polarity_drift"})


@dataclass
class PenaltyMagnitudeState:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, penalty: str, default: float = 0.0) -> float:
        return self.values.get(penalty, default)

    def set(self, penalty: str, value: float) -> None:
        self.values[penalty] = value


class PenaltyMagnitudeKnob(Knob):
    kind = "penalty_magnitude"

    def __init__(
        self,
        *,
        penalty: str,
        state: PenaltyMagnitudeState,
        magnitude: float = 0.05,
    ):
        if penalty not in VALID_PENALTIES:
            raise KnobError(f"unknown penalty {penalty!r}")
        super().__init__(knob_id=f"penalty:{penalty}")
        self.penalty = penalty
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(penalty)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.penalty)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be numeric, got {new_value!r}") from exc
        if v < 0 or v > 0.5:
            raise KnobError(f"penalty magnitude {v} outside [0, 0.5]")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δ|={delta:.4f} > magnitude_bound={self._magnitude:.4f}"
            )
        self._prev = self.current_value()
        self._state.set(self.penalty, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.penalty, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "penalty": self.penalty,
            "value": round(self.current_value(), 4),
            "baseline": round(self.baseline_value(), 4),
            "magnitude_bound": self._magnitude,
        }
