"""Phase 3 §5.2 — Stage 11 dispatcher.

Calls all three role generators against a shared EvidencePack and
returns the per-role payloads. This is the single function the writer
+ on-demand re-enrichment paths call to assemble role-distinct content.

Today the deterministic baselines are wired in directly. When the
LLM-prompt versions land (per §5.3), the dispatcher's signature stays
the same — only the imported generators change.

Cross-role drift detection (§5.5) lives in
`engine.analysis.cross_role_drift.compute_drift` and is called by
`output_verifier.verify_and_correct`; the dispatcher itself stays
narrow + pure.
"""
from __future__ import annotations

from typing import Any

from engine.analysis.evidence_pack import EvidencePack
from engine.analysis.role_generators.analyst import generate_analyst_payload
from engine.analysis.role_generators.ceo import generate_ceo_payload
from engine.analysis.role_generators.cfo import generate_cfo_payload
from engine.analysis.role_generators.types import (
    RecommendationStub,
    RoleDistinctPayload,
)


# Canonical role keys — mirrors the rest of the codebase
# (CrispOutput / CompanySwitcher / RolePanelPriority all use these).
_ROLE_KEYS: tuple[str, ...] = ("cfo", "ceo", "esg-analyst")


def dispatch_role_payloads(
    pack: EvidencePack,
    recommendations: list[RecommendationStub] | None = None,
    company_revenue_cr: float | None = None,
) -> dict[str, RoleDistinctPayload]:
    """Build all three RoleDistinctPayloads from one shared EvidencePack.

    Returns a dict keyed by canonical role: ``cfo``, ``ceo``, ``esg-analyst``.
    Caller can index e.g. ``payloads["cfo"].headline``.

    Each generator is wrapped so a single role's failure doesn't poison
    the other two — the dict still contains the two successful roles +
    an empty placeholder for the failing one (with role + headline =
    "generation failed"). The pipeline never raises out of this function.
    """
    recommendations = recommendations or []
    out: dict[str, RoleDistinctPayload] = {}

    try:
        out["cfo"] = generate_cfo_payload(
            pack, recommendations=recommendations,
            company_revenue_cr=company_revenue_cr,
        )
    except Exception:  # noqa: BLE001 — never break dispatcher on one role
        out["cfo"] = _empty_payload("cfo")

    try:
        out["ceo"] = generate_ceo_payload(pack, recommendations=recommendations)
    except Exception:  # noqa: BLE001
        out["ceo"] = _empty_payload("ceo")

    try:
        out["esg-analyst"] = generate_analyst_payload(
            pack, recommendations=recommendations,
        )
    except Exception:  # noqa: BLE001
        out["esg-analyst"] = _empty_payload("esg-analyst")

    return out


def dispatch_role_payloads_as_dict(
    pack: EvidencePack,
    recommendations: list[RecommendationStub] | None = None,
    company_revenue_cr: float | None = None,
) -> dict[str, dict[str, Any]]:
    """JSON-friendly variant — returns dict[role, dict] for direct stamping
    onto the persisted insight payload."""
    payloads = dispatch_role_payloads(
        pack, recommendations=recommendations,
        company_revenue_cr=company_revenue_cr,
    )
    return {role: payload.to_dict() for role, payload in payloads.items()}


def _empty_payload(role: str) -> RoleDistinctPayload:
    """Safe placeholder when a generator raises. Surfaces the failure
    rather than hiding it (the headline tells the consumer to investigate)."""
    from engine.analysis.role_generators.types import HeroMetric
    return RoleDistinctPayload(
        role=role,
        headline=f"{role}: generation failed — fall back to legacy view",
        hero_metric=HeroMetric(value="—", label="generation failed"),
        role_takeaways=["Role generator raised; check engine logs."],
        role_paragraph="",
        recommendations=[],
        visible_panels=[],
        hidden_panels=[],
    )


def role_keys() -> tuple[str, ...]:
    """The three canonical role keys the dispatcher emits."""
    return _ROLE_KEYS
