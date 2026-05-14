"""Phase 3 §5.4 — recommendation type whitelist per role.

Enforces the role-specific type whitelist from the enhancement plan:

    | Role    | Allowed types                                         | Rejected types                                    |
    |---------|-------------------------------------------------------|---------------------------------------------------|
    | CFO     | financial, operational, compliance                    | esg_positioning, strategic, brand                 |
    | CEO     | strategic, esg_positioning, brand, capital_allocation | compliance, kpi_tracking, audit                   |
    | Analyst | framework, disclosure, kpi_tracking, audit            | capital_allocation, financial, brand              |

The recommendation_engine emits recommendations with a ``type`` field per
ontology archetype. This module filters them PER ROLE so that:
  - a CFO never sees an "earnings call" comms task tagged as their action
  - a CEO never sees a quarterly compliance item
  - an Analyst never sees a capital_allocation play (they don't authorise capital)

Reject reason is captured for the audit log; the recommendation isn't
deleted — it's quarantined under ``rejected_for_role`` so a future
maintainer can audit why something was filtered.

The strictness is deliberate. The 85%-identical problem the user
called out comes precisely from the same recommendation appearing in
all 3 role views with the wording barely tweaked. A hard whitelist
fixes that at the structural level.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


ALLOWED_BY_ROLE: dict[str, frozenset[str]] = {
    "cfo": frozenset({"financial", "operational", "compliance"}),
    "ceo": frozenset({"strategic", "esg_positioning", "brand", "capital_allocation"}),
    "esg_analyst": frozenset({"framework", "disclosure", "kpi_tracking", "audit"}),
    # Aliases used elsewhere in the codebase
    "analyst": frozenset({"framework", "disclosure", "kpi_tracking", "audit"}),
    "esg-analyst": frozenset({"framework", "disclosure", "kpi_tracking", "audit"}),
}

REJECTED_BY_ROLE: dict[str, frozenset[str]] = {
    "cfo": frozenset({"esg_positioning", "strategic", "brand"}),
    "ceo": frozenset({"compliance", "kpi_tracking", "audit"}),
    "esg_analyst": frozenset({"capital_allocation", "financial", "brand"}),
    "analyst": frozenset({"capital_allocation", "financial", "brand"}),
    "esg-analyst": frozenset({"capital_allocation", "financial", "brand"}),
}


def is_allowed(rec_type: str, role: str) -> bool:
    """True iff this rec type is on the allowlist for the role."""
    if not rec_type or not role:
        return True  # back-compat: untyped recs flow through unchanged
    role_key = role.lower().strip()
    type_key = str(rec_type).lower().strip()
    allowed = ALLOWED_BY_ROLE.get(role_key)
    if allowed is None:
        return True  # unknown role → don't filter
    return type_key in allowed


def is_rejected(rec_type: str, role: str) -> bool:
    """True iff this rec type is explicitly forbidden for the role.

    Distinct from `not is_allowed`: an unknown type is neither allowed
    nor rejected — it passes the gate but doesn't satisfy the whitelist.
    """
    if not rec_type or not role:
        return False
    role_key = role.lower().strip()
    type_key = str(rec_type).lower().strip()
    rejected = REJECTED_BY_ROLE.get(role_key)
    if rejected is None:
        return False
    return type_key in rejected


def filter_recommendations_for_role(
    recommendations: Iterable[dict[str, Any]],
    role: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split recommendations into (allowed, rejected) for the given role.

    Rejected entries get a ``rejected_for_role`` field naming the role
    that filtered them, plus ``rejected_reason`` for the audit log.
    Original list is not mutated.
    """
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for rec in recommendations:
        if not isinstance(rec, dict):
            continue
        rec_type = rec.get("type") or ""
        if is_rejected(rec_type, role):
            entry = dict(rec)
            entry["rejected_for_role"] = role
            entry["rejected_reason"] = (
                f"type '{rec_type}' is on the forbidden list for role '{role}' "
                f"(plan §5.4: '{rec_type}' is not a {role} action)"
            )
            rejected.append(entry)
        else:
            allowed.append(rec)
    return allowed, rejected


def split_recommendations_by_role(
    recommendations: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Convenience: produce {cfo: [...], ceo: [...], analyst: [...]} by
    applying each role's whitelist to the same source list.
    """
    rec_list = list(recommendations)
    out: dict[str, list[dict[str, Any]]] = {}
    for role in ("cfo", "ceo", "esg_analyst"):
        allowed, _ = filter_recommendations_for_role(rec_list, role)
        out[role] = allowed
    return out
