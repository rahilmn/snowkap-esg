"""Tier-1 promoter — routes accepted knob changes through R6 →
CompanyAgent belief update.

Unlike Tier-0 (which only writes to the advisor queue), Tier-1
auto-commits the belief update because:
  - The scope is bounded to one tenant
  - The R6 rule fires a structured BeliefProposal which the
    CompanyAgent processes through its existing audit-laden
    `update_typed_belief` path
  - Per-tenant blast radius is small; per-tenant rollback is cheap

Caller passes a CompanyAgent instance + the experiment record. The
promoter constructs the `autoresearcher_proposal` dict that R6 reads.
"""
from __future__ import annotations

from typing import Any

from engine.autoresearcher.ledger import ExperimentRecord


def promote_tenant_knob(
    *,
    record: ExperimentRecord,
    tenant_slug: str,
    article_sample: dict[str, Any] | None = None,
    audit_dir: Any = None,
) -> dict[str, Any]:
    """Fire a belief proposal via R6 for this tenant.

    Returns a summary dict for the caller.

    Args:
        record: the kept experiment to promote
        tenant_slug: the tenant slug
        article_sample: an exemplar article dict (the proposal needs SOMETHING
            with a `topic` field for R6 to fire); when None we use a
            placeholder topic derived from the knob_id
        audit_dir: optional audit_dir for CompanyAgent (test override)
    """
    try:
        from engine.governance.belief_revision import revise_from_article
        from engine.governance.company_agent import CompanyAgent
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"belief_revision import failed: {exc}"}

    # Pick an article shape R6 can fire on
    article = article_sample or {
        "id": f"autoresearcher-{record.experiment_id}",
        "event_id": "x",
        "event_polarity": "neutral",
        "materiality": "MODERATE",
        "topic": _topic_from_knob_id(record.knob_id),
    }

    proposal = {
        "knob_kind": record.knob_kind,
        "knob_id": record.knob_id,
        "metric_delta": record.metric_delta,
        "keep_threshold": 0.02,
    }

    proposals = revise_from_article(
        article=article,
        autoresearcher_proposal=proposal,
    )

    r6_proposals = [p for p in proposals if getattr(p, "rule_id", "") == "R6"]
    if not r6_proposals:
        return {
            "ok": True,
            "applied": False,
            "message": "R6 did not fire (delta below threshold or topic missing)",
        }

    agent = CompanyAgent(tenant=tenant_slug, audit_dir=audit_dir, auto_persist=False)
    applied = 0
    for p in r6_proposals:
        try:
            agent.update_typed_belief(
                p.belief,
                rationale=p.rationale,
                actor="autoresearcher_tier1",
            )
            applied += 1
        except Exception:
            continue

    return {
        "ok": True,
        "applied": applied > 0,
        "n_beliefs_updated": applied,
        "tenant_slug": tenant_slug,
    }


def _topic_from_knob_id(knob_id: str) -> str:
    """Best-effort: extract a topic from the knob_id when no article
    sample is provided. Falls back to a generic 'autoresearcher' tag.

    Recognises `topic_*` segments anywhere in the colon-delimited id
    (e.g. `materialFor:topic_climate:industry_power` → `climate`).
    """
    for part in knob_id.split(":"):
        if part.startswith("topic_"):
            tail = part[len("topic_"):]
            if tail:
                return tail
    return "autoresearcher"
