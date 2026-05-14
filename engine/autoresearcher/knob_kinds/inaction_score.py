"""Inaction-score knob — tunes the `baseRiskScore` per priority band
or `recTypeBonus` per recommendation type in the
`RiskOfInactionConfig` ontology block.

12 atomic knobs total (4 base scores × HIGH/MODERATE/LOW/CRITICAL + 8
recTypeBonuses).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from engine.autoresearcher.knobs import Knob, KnobError

Kind = Literal["base", "rec_type_bonus"]


@dataclass
class InactionScoreState:
    """Snapshot of the risk-of-inaction config.

    Keyed by (kind, slot) → value. kind ∈ {"base", "rec_type_bonus"};
    slot is the band name or rec-type slug.
    """
    values: dict[tuple[str, str], float] = field(default_factory=dict)

    def get(self, kind: str, slot: str, default: float = 0.0) -> float:
        return self.values.get((kind, slot), default)

    def set(self, kind: str, slot: str, value: float) -> None:
        self.values[(kind, slot)] = value


class InactionScoreKnob(Knob):
    kind = "inaction_score"

    def __init__(
        self,
        *,
        kind: Kind,
        slot: str,
        state: InactionScoreState,
        magnitude: float = 2.0,  # ±2 score points
    ):
        if kind not in ("base", "rec_type_bonus"):
            raise KnobError(f"unknown kind {kind!r}")
        super().__init__(knob_id=f"inaction:{kind}:{slot}")
        self.score_kind = kind
        self.slot = slot
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(kind, slot)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.score_kind, self.slot)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be numeric, got {new_value!r}") from exc
        # Base scores live in [0, 50]; bonuses in [-5, +10]
        if self.score_kind == "base" and (v < 0 or v > 50):
            raise KnobError(f"base score {v} outside [0, 50]")
        if self.score_kind == "rec_type_bonus" and (v < -5 or v > 10):
            raise KnobError(f"rec-type bonus {v} outside [-5, 10]")
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δ|={delta:.2f} > magnitude_bound={self._magnitude:.2f}"
            )
        self._prev = self.current_value()
        self._state.set(self.score_kind, self.slot, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.score_kind, self.slot, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "score_kind": self.score_kind,
            "slot": self.slot,
            "value": round(self.current_value(), 4),
            "baseline": round(self.baseline_value(), 4),
            "magnitude_bound": self._magnitude,
        }
