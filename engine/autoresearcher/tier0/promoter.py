"""Tier-0 promoter — routes accepted knob changes to the advisor queue.

NEVER auto-commits to the live ontology. Every accepted knob fires a
`route_unverified_to_advisor` event tagged with
`signal_type=cascade_computation, scope=global, uncertainty=unverified`,
matching the L6 advisor-queue contract.

An admin then reviews in `/settings/advisor`. Approve → existing
`apply_resolution_action` routes to the discovery promoter (which
would touch the actual TTL only if the change is approved through the
human-in-the-loop chain).
"""
from __future__ import annotations

from pathlib import Path

from engine.autoresearcher.ledger import ExperimentRecord


def queue_for_advisor_review(
    record: ExperimentRecord,
    *,
    base_data_dir: Path | None = None,
) -> bool:
    """Append an `unverified_candidate` event to the advisor queue.

    Returns True on success. Best-effort — failures (e.g. audit
    module not loaded) are silently logged and do not propagate.
    """
    try:
        from engine.audit import module_tag, route_unverified_to_advisor
        candidate_id = f"autoresearcher:{record.knob_id}:{record.experiment_id}"
        rationale = (
            f"Autoresearcher proposed a {record.knob_kind} change "
            f"(knob={record.knob_id}). Metric Δ={record.metric_delta:+.4f} on "
            f"{record.n_articles} held-out articles. "
            f"Before: {record.metric_before.get('composite', 'n/a')}, "
            f"After: {record.metric_after.get('composite', 'n/a')}."
        )
        route_unverified_to_advisor(
            candidate_id=candidate_id,
            category="autoresearcher_knob_change",
            rationale=rationale,
            tags=module_tag(
                attribution="autoresearcher_tier0",
                signal_type="cascade_computation",
                scope="global",
                uncertainty="unverified",
            ),
            base_data_dir=base_data_dir,
        )
        return True
    except Exception:
        return False
