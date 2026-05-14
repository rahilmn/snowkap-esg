"""Persona-weight knob — tunes one (user, affinity_key) weight in the
persona model.

State is the per-user dict of affinity values [0, 1]. The autoresearcher
proposes small perturbations; the metric is top-K click-through rate
on held-out user actions.

Per-user isolation: a knob for user A cannot affect user B's state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError


@dataclass
class PersonaWeightState:
    """Snapshot of per-user affinity weights.

    Keyed by (user_id, key) → float in [0, 1].
    """
    values: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, user_id: str, key: str, default: float = 0.5) -> float:
        return self.values.get((user_id, key), default)

    def set(self, user_id: str, key: str, value: float) -> None:
        self.values[(user_id, key)] = value


class PersonaWeightKnob(Knob):
    kind = "persona_weight"

    def __init__(
        self,
        *,
        user_id: str,
        key: str,
        state: PersonaWeightState,
        magnitude: float = 0.10,
    ):
        if not user_id or not key:
            raise KnobError("user_id and key are required")
        knob_id = f"persona:{user_id}:{key}"
        super().__init__(knob_id=knob_id)
        self.user_id = user_id
        self.key = key
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(user_id, key)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.user_id, self.key)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be numeric, got {new_value!r}") from exc
        if v < 0 or v > 1:
            raise KnobError(f"persona weight {v} outside [0, 1]")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δ|={delta:.4f} > magnitude_bound={self._magnitude:.4f}"
            )
        self._prev = self.current_value()
        self._state.set(self.user_id, self.key, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.user_id, self.key, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "user_id": self.user_id,
            "key": self.key,
            "value": round(self.current_value(), 4),
            "baseline": round(self.baseline_value(), 4),
            "magnitude_bound": self._magnitude,
        }
