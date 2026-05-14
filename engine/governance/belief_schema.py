"""L7 — Domain belief schema (typed ESG layer).

A focused schema for the 5 belief types that drive Snowkap's day-to-day
decisions. The wider `CompanyAgent.update_belief()` still accepts
free-form values for ad-hoc state; the typed schema here adds:

  - Enum-constrained value domains so downstream consumers can switch
    on `belief.kind` without runtime type checks
  - JSON-friendly `to_dict()` + `from_dict()` round-tripping for
    persistence (deferred; design ready)
  - Validation at construction time — illegal bands / negative
    exposures raise before they hit the audit log

The 5 belief kinds:
  1. risk_band                — current ESG risk band per topic
  2. financial_exposure       — ₹ cascade-computed exposure per scenario
  3. transition_stance        — where the company sits on the climate
                                transition continuum
  4. framework_compliance     — per-framework disclosure readiness
  5. painpoint_severity       — how acute a tenant's matched painpoint is

Each TypedBelief subclass carries:
  - `kind`            : Literal — the discriminator
  - `value`           : the structured payload (band, ₹, stance, …)
  - `last_evidence`   : URI / article ID that justified the current value
  - `confidence_band` : low | moderate | high (matches L2 uncertainty
                        enum)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Enum domains
# ---------------------------------------------------------------------------

RiskBand = Literal["LOW", "MODERATE", "HIGH", "CRITICAL"]
TransitionStance = Literal[
    "leader",       # ahead of regulatory curve (Tata Power 2024)
    "fast_follower",
    "compliant",
    "lagging",
    "regressing",   # actively backsliding (rare; e.g. coal expansion)
]
FrameworkComplianceStatus = Literal[
    "compliant",
    "in_progress",
    "gap_identified",
    "non_compliant",
    "not_applicable",
]
ConfidenceBand = Literal["low", "moderate", "high"]


VALID_RISK_BANDS = frozenset({"LOW", "MODERATE", "HIGH", "CRITICAL"})
VALID_TRANSITION_STANCES = frozenset({
    "leader", "fast_follower", "compliant", "lagging", "regressing",
})
VALID_FRAMEWORK_STATUSES = frozenset({
    "compliant", "in_progress", "gap_identified", "non_compliant", "not_applicable",
})
VALID_CONFIDENCE_BANDS = frozenset({"low", "moderate", "high"})


# ---------------------------------------------------------------------------
# Base typed belief
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TypedBelief:
    """Base class for the 5 belief kinds. Subclasses override `kind` +
    value type. Always carries provenance + confidence + timestamp."""
    kind: str
    confidence_band: ConfidenceBand = "moderate"
    last_evidence: str = ""           # article_id, ontology URI, or analyst note
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.confidence_band not in VALID_CONFIDENCE_BANDS:
            raise ValueError(
                f"confidence_band={self.confidence_band!r} not in "
                f"{sorted(VALID_CONFIDENCE_BANDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# ---------------------------------------------------------------------------
# 1. RiskBand belief
# ---------------------------------------------------------------------------


@dataclass
class RiskBandBelief(TypedBelief):
    """Current ESG risk band for a (tenant × topic) pair."""
    kind: Literal["risk_band"] = "risk_band"
    topic: str = ""                   # ESG theme slug, e.g. "climate"
    band: RiskBand = "MODERATE"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.band not in VALID_RISK_BANDS:
            raise ValueError(f"band={self.band!r} not in {sorted(VALID_RISK_BANDS)}")
        if not self.topic.strip():
            raise ValueError("topic must be non-empty")


# ---------------------------------------------------------------------------
# 2. FinancialExposure belief
# ---------------------------------------------------------------------------


@dataclass
class FinancialExposureBelief(TypedBelief):
    """Cascade-computed financial exposure (in ₹ Cr) per scenario."""
    kind: Literal["financial_exposure"] = "financial_exposure"
    scenario: str = ""                # e.g. "climate_transition_2030"
    exposure_cr_lo: float = 0.0       # low end of range
    exposure_cr_hi: float = 0.0       # high end of range
    method: str = ""                  # "cascade" | "from_article" | "peer_benchmark"

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.exposure_cr_lo < 0 or self.exposure_cr_hi < 0:
            raise ValueError("exposure values must be non-negative")
        if self.exposure_cr_hi < self.exposure_cr_lo:
            raise ValueError(
                f"exposure_cr_hi={self.exposure_cr_hi} < exposure_cr_lo={self.exposure_cr_lo}"
            )
        if not self.scenario.strip():
            raise ValueError("scenario must be non-empty")


# ---------------------------------------------------------------------------
# 3. TransitionStance belief
# ---------------------------------------------------------------------------


@dataclass
class TransitionStanceBelief(TypedBelief):
    """Where the company sits on the climate transition continuum."""
    kind: Literal["transition_stance"] = "transition_stance"
    stance: TransitionStance = "compliant"
    horizon_fy: str = ""              # e.g. "FY27" — when this stance applies

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.stance not in VALID_TRANSITION_STANCES:
            raise ValueError(
                f"stance={self.stance!r} not in {sorted(VALID_TRANSITION_STANCES)}"
            )


# ---------------------------------------------------------------------------
# 4. FrameworkCompliance belief
# ---------------------------------------------------------------------------


@dataclass
class FrameworkComplianceBelief(TypedBelief):
    """Per-framework disclosure readiness for the tenant."""
    kind: Literal["framework_compliance"] = "framework_compliance"
    framework_id: str = ""            # e.g. "BRSR", "CSRD", "TCFD"
    status: FrameworkComplianceStatus = "in_progress"
    deadline: str = ""                # ISO date or fuzzy ("Q3 FY27"); empty if non-binding

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.status not in VALID_FRAMEWORK_STATUSES:
            raise ValueError(
                f"status={self.status!r} not in {sorted(VALID_FRAMEWORK_STATUSES)}"
            )
        if not self.framework_id.strip():
            raise ValueError("framework_id must be non-empty")


# ---------------------------------------------------------------------------
# 5. PainpointSeverity belief
# ---------------------------------------------------------------------------


@dataclass
class PainpointSeverityBelief(TypedBelief):
    """How acute a tenant's matched painpoint currently is (0..1)."""
    kind: Literal["painpoint_severity"] = "painpoint_severity"
    painpoint_topic: str = ""         # the painpoint vocabulary slug
    severity: float = 0.0             # 0..1

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0.0 <= self.severity <= 1.0):
            raise ValueError(f"severity={self.severity} must be in [0.0, 1.0]")
        if not self.painpoint_topic.strip():
            raise ValueError("painpoint_topic must be non-empty")


# ---------------------------------------------------------------------------
# 6. FYCascadeSnapshot belief
# ---------------------------------------------------------------------------


@dataclass
class FYCascadeSnapshotBelief(TypedBelief):
    """Per-primitive Δ snapshot for a given fiscal year.

    The CompanyAgent uses this to track multi-year evolution of the
    primitive cascade: each FY gets its own belief, indexed by
    `fy + primitive`. Reading the history of snapshots reconstructs
    the cascade's trajectory.
    """
    kind: Literal["fy_cascade_snapshot"] = "fy_cascade_snapshot"
    fy: str = ""                      # e.g. "FY27"
    primitive: str = ""               # e.g. "GE" (GHG Emissions), "OX" (Opex)
    delta_cr: float = 0.0             # ₹ Cr Δ from baseline
    base_value_cr: float = 0.0        # ₹ Cr baseline for the FY
    method: str = ""                  # "cascade" | "from_article" | "peer_benchmark"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.fy.strip():
            raise ValueError("fy must be non-empty (e.g. 'FY27')")
        if not self.primitive.strip():
            raise ValueError("primitive must be non-empty (e.g. 'GE', 'OX')")
        if self.base_value_cr < 0:
            raise ValueError(f"base_value_cr={self.base_value_cr} must be non-negative")


# ---------------------------------------------------------------------------
# Discriminator union — `Any` of the 6 typed beliefs
# ---------------------------------------------------------------------------


KnownBelief = (
    RiskBandBelief
    | FinancialExposureBelief
    | TransitionStanceBelief
    | FrameworkComplianceBelief
    | PainpointSeverityBelief
    | FYCascadeSnapshotBelief
)
