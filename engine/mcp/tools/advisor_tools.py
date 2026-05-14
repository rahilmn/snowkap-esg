"""MCP adapters for advisor-queue / advisor-resolve.

`advisor-resolve` is marked destructive in the manifest — the server
caller is responsible for showing the user a verbatim sign-off before
invoking it (Phase C differentiator amplification).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.audit import (
    _advisor_event_id,
    apply_resolution_action,
    read_advisor_queue,
    resolve_advisor_event,
)


def handle_advisor_queue(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Read pending advisor events.

    Filters resolved events by computing the event_id of every advisor
    queue entry and excluding any that already appear in resolutions.
    """
    from engine.audit import read_advisor_resolutions

    resolved_ids = {
        _advisor_event_id(ev) for ev in read_advisor_resolutions(base_data_dir=base_data)
    }
    pending: list[dict[str, Any]] = []
    for ev in read_advisor_queue(base_data_dir=base_data):
        eid = _advisor_event_id(ev)
        if eid in resolved_ids:
            continue
        enriched = dict(ev)
        enriched["event_id"] = eid
        tenant = payload.get("tenant")
        if tenant and (enriched.get("tags") or {}).get("scope") != "global":
            # Tenant filter is a soft hint; only filter when the event
            # carries a tenant tag and it doesn't match.
            tags = enriched.get("tags") or {}
            ev_tenant = tags.get("tenant")
            if ev_tenant and ev_tenant != tenant:
                continue
        pending.append(enriched)
    return {"pending": pending, "count": len(pending)}


def handle_advisor_resolve(payload: dict[str, Any], base_data: Path) -> dict[str, Any]:
    """Resolve an advisor event (approve | reject).

    Destructive: writes to `advisor_resolutions.jsonl` AND triggers
    promoter manual_decide for `unverified_candidate` events.
    """
    target_event_id = payload["event_id"]
    resolution = payload["resolution"]
    rationale = payload.get("rationale", "")

    # Find the event in the queue
    target_event: dict[str, Any] | None = None
    for ev in read_advisor_queue(base_data_dir=base_data):
        if _advisor_event_id(ev) == target_event_id:
            target_event = ev
            break
    if target_event is None:
        return {"ok": False, "error": f"no advisor event with id={target_event_id}"}

    # Resolve
    resolution_entry = resolve_advisor_event(
        event_id=target_event_id,
        resolution=resolution,
        actor="mcp:advisor-resolve",
        rationale=rationale,
        base_data_dir=base_data,
    )
    side_effect = apply_resolution_action(
        event=target_event,
        resolution=resolution,
        actor="mcp:advisor-resolve",
        rationale=rationale,
    )
    return {"ok": True, "resolution": resolution_entry, "side_effect": side_effect}
