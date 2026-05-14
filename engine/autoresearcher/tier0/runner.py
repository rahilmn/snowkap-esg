"""Tier-0 entry point — composes the introspector + corpus + loop + promoter.

Single function `run_tier0(budget, seed)` does the full lifecycle.
Returns a LoopResult + a summary dict for the CLI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.autoresearcher.corpus import load_held_out_corpus
from engine.autoresearcher.loop import LoopResult, run_loop
from engine.autoresearcher.ontology_introspector import discover_all_knobs
from engine.autoresearcher.tier0.promoter import queue_for_advisor_review


def run_tier0(
    *,
    budget: int = 50,
    seed: int = 42,
    keep_threshold: float = 0.02,
    min_age_days: int = 0,
    holdout_fraction: float = 0.20,
    base_data_dir: Path | None = None,
) -> LoopResult:
    """Run one Tier-0 autoresearcher session.

    Args:
        budget: max number of experiments
        seed: RNG seed for reproducibility
        keep_threshold: minimum metric_delta to accept
        min_age_days: only include articles older than this in the corpus
                      (default 0 = include everything; use 90 in production
                      once enough data is settled)
        holdout_fraction: fraction of corpus held out (vs. used as
                          training-context only)
        base_data_dir: override for tests
    """
    registry = discover_all_knobs()
    corpus = load_held_out_corpus(
        min_age_days=min_age_days,
        holdout_fraction=holdout_fraction,
    )
    return run_loop(
        tier="system",
        registry=registry,
        corpus=corpus,
        budget=budget,
        seed=seed,
        keep_threshold=keep_threshold,
        base_data_dir=base_data_dir,
        on_keep=lambda rec: queue_for_advisor_review(rec, base_data_dir=base_data_dir),
    )
