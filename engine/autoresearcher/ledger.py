"""Append-only experiment journal — the autoresearcher's `results.tsv`
analogue, but JSONL (matches the L0-L7 audit log discipline).

Each entry records one experiment: knob snapshot before/after, metric
breakdowns, keep/discard decision, timestamp, seed. Writes are atomic
(single line append) so concurrent autoresearcher runs are safe.

The ledger ALSO emits an `engine.audit.append_decision` entry for
every experiment so L4 audit-the-audit picks it up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

Decision = Literal["keep", "discard"]


@dataclass
class ExperimentRecord:
    experiment_id: str
    ts: str
    tier: str                              # "system" | "tenant" | "user"
    seed: int
    knob_kind: str
    knob_id: str
    knob_before: dict[str, Any]
    knob_after: dict[str, Any]
    metric_before: dict[str, Any]
    metric_after: dict[str, Any]
    metric_delta: float
    decision: Decision
    rationale: str
    n_articles: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ts": self.ts,
            "tier": self.tier,
            "seed": self.seed,
            "knob_kind": self.knob_kind,
            "knob_id": self.knob_id,
            "knob_before": self.knob_before,
            "knob_after": self.knob_after,
            "metric_before": self.metric_before,
            "metric_after": self.metric_after,
            "metric_delta": round(self.metric_delta, 6),
            "decision": self.decision,
            "rationale": self.rationale,
            "n_articles": self.n_articles,
        }


def _ledger_path(tier: str, base_data_dir: Path | None = None) -> Path:
    if base_data_dir is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
        base_data_dir = repo_root / "data"
    p = base_data_dir / "autoresearcher" / tier / "experiments.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record_experiment(
    rec: ExperimentRecord,
    *,
    base_data_dir: Path | None = None,
    emit_audit: bool = True,
) -> Path:
    """Append a record to the per-tier JSONL + emit an audit entry."""
    path = _ledger_path(rec.tier, base_data_dir=base_data_dir)
    line = json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)

    if emit_audit:
        try:
            from engine.audit import append_decision, module_tag
            decision_type = (
                "autoresearcher_experiment_kept"
                if rec.decision == "keep"
                else "autoresearcher_experiment_discarded"
            )
            append_decision(
                decision_type,  # type: ignore[arg-type]
                automated=True,
                extra={
                    "experiment_id": rec.experiment_id,
                    "tier": rec.tier,
                    "knob_kind": rec.knob_kind,
                    "knob_id": rec.knob_id,
                    "metric_delta": rec.metric_delta,
                    "n_articles": rec.n_articles,
                },
                tags=module_tag(
                    attribution=f"autoresearcher_{rec.tier}",
                    signal_type="cascade_computation",
                    scope="global" if rec.tier == "system" else "tenant",
                    uncertainty="low" if rec.decision == "keep" else "moderate",
                ),
                base_data_dir=base_data_dir,
            )
        except Exception:
            # Audit append is best-effort; never block on ledger
            pass

    return path


def read_ledger(
    tier: str, *, base_data_dir: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate experiment records for one tier."""
    path = _ledger_path(tier, base_data_dir=base_data_dir)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                continue


def leaderboard(
    tier: str,
    *,
    top_n: int = 20,
    base_data_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Top-N kept experiments by metric_delta descending."""
    keeps = [
        r for r in read_ledger(tier, base_data_dir=base_data_dir)
        if r.get("decision") == "keep"
    ]
    keeps.sort(key=lambda r: r.get("metric_delta") or 0.0, reverse=True)
    return keeps[:top_n]


def make_experiment_id(seed: int, n: int) -> str:
    """Stable, monotonic experiment id from (seed, sequence-number)."""
    return f"exp-{seed:08d}-{n:06d}"
