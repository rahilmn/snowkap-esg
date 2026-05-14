"""Tier-1 entry point.

Runs the autoresearcher loop on a tenant-filtered corpus. Each kept
experiment routes through `promote_tenant_knob` → R6 → CompanyAgent
belief update.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.autoresearcher.loop import LoopResult, run_loop
from engine.autoresearcher.ontology_introspector import discover_all_knobs
from engine.autoresearcher.tier1.corpus import load_tenant_corpus
from engine.autoresearcher.tier1.promoter import promote_tenant_knob


def run_tier1(
    *,
    tenant_slug: str,
    budget: int = 30,
    seed: int = 42,
    keep_threshold: float = 0.02,
    min_age_days: int = 0,
    holdout_fraction: float = 0.20,
    base_data_dir: Path | None = None,
    audit_dir: Path | None = None,
) -> LoopResult:
    """Run one Tier-1 autoresearcher session for a single tenant."""
    registry = discover_all_knobs()
    corpus = load_tenant_corpus(
        tenant_slug=tenant_slug,
        min_age_days=min_age_days,
        holdout_fraction=holdout_fraction,
    )

    def _on_keep(record: Any) -> None:
        promote_tenant_knob(
            record=record,
            tenant_slug=tenant_slug,
            audit_dir=audit_dir,
        )

    return run_loop(
        tier="tenant",
        registry=registry,
        corpus=corpus,
        budget=budget,
        seed=seed,
        keep_threshold=keep_threshold,
        base_data_dir=base_data_dir,
        on_keep=_on_keep,
    )
