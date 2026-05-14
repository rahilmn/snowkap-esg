"""Ontology-weight knob — tunes a single numeric weight on an
ontology triple (materiality weight, risk weight, regional boost).

Operates on a shared `OntologyWeightState` (in-memory snapshot of all
`materialityWeight` / `hasRiskWeight` / `boostValue` triples) that
the evaluator's pipeline replay reads via dependency injection. Live
TTL is never mutated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError


@dataclass
class OntologyWeightState:
    """In-memory snapshot of all weighted-predicate triples.

    Keyed by (predicate, subject, object) tuples. Values are floats.
    The autoresearcher introspector populates this from the live
    ontology at run start.
    """
    values: dict[tuple[str, str, str], float] = field(default_factory=dict)

    def get(self, predicate: str, subj: str, obj: str, default: float = 0.0) -> float:
        return self.values.get((predicate, subj, obj), default)

    def set(self, predicate: str, subj: str, obj: str, value: float) -> None:
        self.values[(predicate, subj, obj)] = value


class OntologyWeightKnob(Knob):
    """Tunes a single numeric ontology weight."""

    kind = "ontology_weight"

    def __init__(
        self,
        *,
        predicate: str,
        subj: str,
        obj: str,
        state: OntologyWeightState,
        magnitude: float = 0.2,
    ):
        knob_id = f"{predicate}:{subj}:{obj}"
        super().__init__(knob_id=knob_id)
        self.predicate = predicate
        self.subj = subj
        self.obj = obj
        self._state = state
        self._magnitude = float(magnitude)
        self._baseline = state.get(predicate, subj, obj)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.predicate, self.subj, self.obj)

    def baseline_value(self) -> float:
        return self._baseline

    def magnitude_bound(self) -> float:
        return self._magnitude

    def apply(self, new_value: Any) -> None:
        try:
            v = float(new_value)
        except (TypeError, ValueError) as exc:
            raise KnobError(f"new_value must be float, got {new_value!r}") from exc
        delta = abs(v - self._baseline)
        if delta > self._magnitude:
            raise KnobError(
                f"|Δ|={delta:.4f} > magnitude_bound={self._magnitude:.4f}"
            )
        # Soft clamp to [0, 2] — materiality weights are usually [0,1],
        # risk weights up to ~2.0
        if v < 0 or v > 2.0:
            raise KnobError(f"value {v} outside soft bounds [0, 2]")
        self._prev = self.current_value()
        self._state.set(self.predicate, self.subj, self.obj, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.predicate, self.subj, self.obj, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "predicate": self.predicate,
            "subject": self.subj,
            "object": self.obj,
            "value": round(self.current_value(), 6),
            "baseline": round(self.baseline_value(), 6),
            "magnitude_bound": self._magnitude,
        }
