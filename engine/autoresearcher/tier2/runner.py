"""Tier-2 entry point — per-user autoresearcher run."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.autoresearcher.knob_kinds.persona_weight import (
    PersonaWeightKnob,
    PersonaWeightState,
)
from engine.autoresearcher.loop import LoopResult, run_loop
from engine.autoresearcher.ontology_introspector import KnobRegistry
from engine.autoresearcher.tier2.corpus import load_user_corpus
from engine.autoresearcher.tier2.promoter import promote_user_knob


# Default affinity keys — these are the per-user tunables. Real
# values come from the persona MCQ; we seed a starter set here.
DEFAULT_AFFINITY_KEYS = (
    "esg_focus_climate", "esg_focus_water", "esg_focus_governance",
    "framework_brsr", "framework_csrd", "framework_tcfd",
    "geo_in", "geo_eu", "geo_us",
    "risk_appetite", "horizon_short", "horizon_long",
)


def _build_user_registry(user_id: str) -> tuple[KnobRegistry, PersonaWeightState]:
    """One PersonaWeightKnob per default affinity key for this user."""
    state = PersonaWeightState()
    for key in DEFAULT_AFFINITY_KEYS:
        # Seed at 0.5 (neutral) — the autoresearcher tunes from there
        state.set(user_id, key, 0.5)
    registry = KnobRegistry()
    for key in DEFAULT_AFFINITY_KEYS:
        registry.knobs.append(PersonaWeightKnob(
            user_id=user_id, key=key, state=state,
        ))
    return registry, state


def run_tier2(
    *,
    user_id: str,
    budget: int = 20,
    seed: int = 42,
    keep_threshold: float = 0.02,
    base_data_dir: Path | None = None,
    repo_root: Path | None = None,
) -> LoopResult:
    """Run one Tier-2 autoresearcher session for a single user."""
    registry, state = _build_user_registry(user_id)
    corpus = load_user_corpus(user_id=user_id, repo_root=repo_root)

    def _on_keep(record: Any) -> None:
        promote_user_knob(
            record=record, user_id=user_id, state=state, repo_root=repo_root,
        )

    return run_loop(
        tier="user",
        registry=registry,
        corpus=corpus,
        budget=budget,
        seed=seed,
        keep_threshold=keep_threshold,
        base_data_dir=base_data_dir,
        on_keep=_on_keep,
    )
