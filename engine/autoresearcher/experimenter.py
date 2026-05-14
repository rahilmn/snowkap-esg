"""Deterministic structured random walk over the knob space.

Given a `KnobRegistry`, picks one knob per call + proposes a small
perturbation. Seed-stable: same seed + same registry order = same
sequence of proposals (good for reproducibility + smoke tests).

v1 is purely deterministic. The `llm_proposer.py` hook is reserved
for v2 — it would replace the random walk with an LLM-driven smart
proposer once we see what knob-spaces matter from deterministic runs.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from engine.autoresearcher.knobs import Knob
from engine.autoresearcher.ontology_introspector import KnobRegistry


@dataclass
class Proposal:
    knob: Knob
    new_value: Any
    rationale: str

    def describe(self) -> dict[str, Any]:
        return {
            "knob_id": self.knob.knob_id,
            "knob_kind": self.knob.kind,
            "current": self.knob.current_value() if self.knob.kind != "keyword_set_membership" else None,
            "proposed": self.new_value,
            "rationale": self.rationale,
        }


class Experimenter:
    """Stateful random-walk proposer.

    Each call to `propose()` returns one Proposal. State is the
    RNG; identical seed → identical sequence regardless of registry
    contents (the registry-ordered traversal is stable too).
    """

    def __init__(self, registry: KnobRegistry, *, seed: int = 42):
        self.registry = registry
        self.seed = seed
        self._rng = random.Random(seed)
        self._call_count = 0

    def propose(self) -> Proposal | None:
        """Return one Proposal, or None when the registry is empty."""
        if not self.registry.knobs:
            return None
        self._call_count += 1
        knob = self._rng.choice(self.registry.knobs)

        # Set-valued knobs (keyword sets) take no `new_value`; the
        # apply() is parameterless. Return a sentinel so the evaluator
        # knows to call apply() without args.
        if knob.kind == "keyword_set_membership":
            return Proposal(
                knob=knob,
                new_value=None,
                rationale=f"random walk: toggle keyword (id={knob.knob_id})",
            )

        # Numeric knobs: sample a perturbation within the magnitude bound
        baseline = knob.baseline_value()
        if not isinstance(baseline, (int, float)):
            return None
        bound = knob.magnitude_bound()
        delta = self._rng.uniform(-bound, bound)
        new_value = float(baseline) + delta
        return Proposal(
            knob=knob,
            new_value=new_value,
            rationale=f"random walk: Δ={delta:+.4f} from baseline {baseline:.4f}",
        )
