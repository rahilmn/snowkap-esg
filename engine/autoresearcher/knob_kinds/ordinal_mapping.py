"""Ordinal-mapping knob — tunes the numeric values for qualitative
ontology predicates (confidence_band, severity_band, stakeholder_stance,
headline_priority).

Each atomic knob is one (category, label) pair, e.g.
`ordinal_mapping:confidence_band:moderate` → 0.60.

These values live in `data/ontology/quantitative_mappings.ttl` and are
read by the engine via `engine.ontology.intelligence.query_*_mapping`
functions. Apply/revert operates on an in-memory snapshot dict that
the autoresearcher's evaluator threads into the SPARQL query layer
during a replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.autoresearcher.knobs import Knob, KnobError

VALID_CATEGORIES = frozenset({
    "confidence_band", "severity_band", "stakeholder_stance", "headline_priority",
})


@dataclass
class OrdinalMappingState:
    """The mutable state container the OrdinalMappingKnob operates on.

    A single shared instance lives on the autoresearcher's run context
    and is consulted by the engine during replay via dependency
    injection (e.g. monkey-patched query_band_mapping that prefers
    this state over the live ontology).
    """
    values: dict[tuple[str, str], float]

    def get(self, category: str, label: str, default: float = 0.5) -> float:
        return self.values.get((category, label), default)

    def set(self, category: str, label: str, value: float) -> None:
        self.values[(category, label)] = value

    @classmethod
    def from_query_all(cls) -> "OrdinalMappingState":
        """Build the state from the current ontology."""
        from engine.ontology.intelligence import query_quantitative_mappings_all
        snapshot = query_quantitative_mappings_all()
        flat: dict[tuple[str, str], float] = {}
        for cat, m in snapshot.items():
            for label, val in m.items():
                flat[(cat, label)] = val
        return cls(values=flat)


class OrdinalMappingKnob(Knob):
    """Tunes one (category, label) → float mapping."""

    kind = "ordinal_mapping"

    def __init__(
        self,
        *,
        category: str,
        label: str,
        state: OrdinalMappingState,
        magnitude: float = 0.15,
    ):
        if category not in VALID_CATEGORIES:
            raise KnobError(f"unknown ordinal-mapping category {category!r}")
        knob_id = f"{category}:{label}"
        super().__init__(knob_id=knob_id)
        self.category = category
        self.label = label
        self._state = state
        self._magnitude = float(magnitude)
        # Baseline is whatever's currently in the state at construction
        self._baseline = state.get(category, label)
        self._prev: float | None = None

    def current_value(self) -> float:
        return self._state.get(self.category, self.label)

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
                f"|Δ|={delta:.4f} > magnitude_bound={self._magnitude:.4f} "
                f"for {self.category}:{self.label}"
            )
        self._prev = self.current_value()
        self._state.set(self.category, self.label, v)

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.category, self.label, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "category": self.category,
            "label": self.label,
            "value": round(self.current_value(), 6),
            "baseline": round(self.baseline_value(), 6),
            "magnitude_bound": self._magnitude,
        }
