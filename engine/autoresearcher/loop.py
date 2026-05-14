"""Karpathy outer loop — propose → evaluate → keep/discard → ledger.

Stops on:
  - budget exhausted (N experiments)
  - external interrupt (KeyboardInterrupt)
  - corpus empty

The loop is deterministic given the same (registry, seed, corpus,
keep_threshold). v1 has no LLM in the path — every proposal is a
structured random walk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.autoresearcher.corpus import CorpusArticle
from engine.autoresearcher.evaluator import evaluate
from engine.autoresearcher.experimenter import Experimenter
from engine.autoresearcher.ledger import (
    ExperimentRecord,
    make_experiment_id,
    record_experiment,
)
from engine.autoresearcher.ontology_introspector import KnobRegistry


@dataclass
class LoopResult:
    tier: str
    budget: int
    seed: int
    n_keeps: int = 0
    n_discards: int = 0
    n_errors: int = 0
    top_delta: float = 0.0
    top_knob_id: str | None = None
    experiments: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "budget": self.budget,
            "seed": self.seed,
            "n_keeps": self.n_keeps,
            "n_discards": self.n_discards,
            "n_errors": self.n_errors,
            "top_delta": round(self.top_delta, 6),
            "top_knob_id": self.top_knob_id,
        }


def run_loop(
    *,
    tier: str,
    registry: KnobRegistry,
    corpus: list[CorpusArticle],
    budget: int = 50,
    seed: int = 42,
    keep_threshold: float = 0.02,
    base_data_dir: Path | None = None,
    on_keep: Any = None,
) -> LoopResult:
    """Run the outer keep/discard loop.

    Args:
        tier: 'system' | 'tenant' | 'user'
        registry: discovered knob registry (from ontology_introspector)
        corpus: held-out articles for metric evaluation
        budget: max number of experiments
        seed: RNG seed for the experimenter
        keep_threshold: minimum metric_delta to keep
        base_data_dir: override for tests (otherwise writes to repo data/)
        on_keep: optional callback called with the kept ExperimentRecord
                 (used by Tier-0 promoter to queue advisor reviews)
    """
    experimenter = Experimenter(registry, seed=seed)
    result = LoopResult(tier=tier, budget=budget, seed=seed)

    if not registry.knobs:
        return result
    if not corpus:
        return result

    for i in range(budget):
        proposal = experimenter.propose()
        if proposal is None:
            break

        eval_result = evaluate(proposal, corpus)

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        exp_id = make_experiment_id(seed, i)

        if eval_result.error is not None:
            decision = "discard"
            rationale = f"evaluation error: {eval_result.error}"
            result.n_errors += 1
        elif eval_result.delta > keep_threshold:
            decision = "keep"
            rationale = (
                f"composite Δ={eval_result.delta:+.4f} > threshold "
                f"({keep_threshold:.3f})"
            )
            result.n_keeps += 1
            if eval_result.delta > result.top_delta:
                result.top_delta = eval_result.delta
                result.top_knob_id = proposal.knob.knob_id
        else:
            decision = "discard"
            rationale = (
                f"composite Δ={eval_result.delta:+.4f} ≤ threshold "
                f"({keep_threshold:.3f})"
            )
            result.n_discards += 1

        record = ExperimentRecord(
            experiment_id=exp_id,
            ts=ts,
            tier=tier,
            seed=seed,
            knob_kind=proposal.knob.kind,
            knob_id=proposal.knob.knob_id,
            knob_before=proposal.describe(),
            knob_after=proposal.knob.describe(),
            metric_before=eval_result.metric_before.to_dict(),
            metric_after=eval_result.metric_after.to_dict(),
            metric_delta=eval_result.delta,
            decision=decision,  # type: ignore[arg-type]
            rationale=rationale,
            n_articles=eval_result.metric_after.n_articles,
        )
        record_experiment(record, base_data_dir=base_data_dir)
        result.experiments.append(record.to_dict())

        if decision == "keep" and on_keep is not None:
            try:
                on_keep(record)
            except Exception:
                pass

    return result
