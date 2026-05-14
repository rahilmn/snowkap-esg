"""Phase 6 §8.1 — Persona dataclass + onboarding MCQ schema.

Stored per user_id. Two users at the same company with the same role
can have different personas — and should.

Captured signals:
  - esg_focus: top-3 of 10 ESG areas
  - frameworks: top-3 of 10 disclosure frameworks
  - geographies: top-3 of 8 regions
  - horizon: single quarterly | annual | 3yr | 5yr_plus
  - decision_style: single data_first | narrative_first | regulatory_first | competitive_first
  - risk_appetite: single defensive | balanced | opportunistic

Derived (auto-drift, weekly cron):
  - click_affinity: topic → 0..1 score from open / save behaviour
  - skip_affinity: topic → 0..1 score from skip / dismiss behaviour
  - last_active

The MCQ wire format (PERSONA_QUESTIONS) is a constant here so backend +
frontend stay in sync. JSON sidecar at
``engine/persona/persona_questions.json`` stays a derived artifact —
single source of truth lives in this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


Role = Literal["cfo", "ceo", "analyst", "other"]
Horizon = Literal["quarterly", "annual", "3yr", "5yr_plus"]
DecisionStyle = Literal[
    "data_first", "narrative_first", "regulatory_first", "competitive_first"
]
RiskAppetite = Literal["defensive", "balanced", "opportunistic"]


VALID_ESG_FOCUS = (
    "climate", "water", "biodiversity", "labour", "supply_chain",
    "governance", "dei", "human_rights", "circular_economy", "community",
)
VALID_FRAMEWORKS = (
    "BRSR", "GRI", "TCFD", "CSRD", "SASB", "CDP", "ISSB",
    "EU_Taxonomy", "SEC_climate", "none",
)
VALID_GEOGRAPHIES = (
    "india", "eu", "us", "uk", "apac", "mena", "latam", "africa",
)
VALID_HORIZONS = ("quarterly", "annual", "3yr", "5yr_plus")
VALID_DECISION_STYLES = (
    "data_first", "narrative_first", "regulatory_first", "competitive_first",
)
VALID_RISK_APPETITES = ("defensive", "balanced", "opportunistic")
VALID_ROLES = ("cfo", "ceo", "analyst", "other")


@dataclass
class Persona:
    user_id: str
    role: str = "other"
    esg_focus: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    geographies: list[str] = field(default_factory=list)
    horizon: str = "annual"
    decision_style: str = "narrative_first"
    risk_appetite: str = "balanced"

    # Derived (auto-drift)
    click_affinity: dict[str, float] = field(default_factory=dict)
    skip_affinity: dict[str, float] = field(default_factory=dict)
    last_active: str | None = None

    # Provenance
    onboarded_at: str = field(default_factory=lambda: _now())
    last_edited_at: str | None = None
    last_drift_update_at: str | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_persona_for_role(user_id: str, role: str = "other") -> Persona:
    """Build a sensible default persona keyed off the user's role.

    Used when the user skips the MCQ. Per the plan §8.2: don't force
    the MCQ — capture the cost in a banner and ship a role-default
    persona so personalization still has SOMETHING to work with.
    """
    role = role.lower().strip() if role else "other"
    if role not in VALID_ROLES:
        role = "other"

    # Sensible defaults derived from each role's typical concerns
    defaults = {
        "cfo": Persona(
            user_id=user_id,
            role="cfo",
            esg_focus=["climate", "governance", "supply_chain"],
            frameworks=["BRSR", "TCFD", "ISSB"],
            geographies=["india"],
            horizon="annual",
            decision_style="data_first",
            risk_appetite="balanced",
        ),
        "ceo": Persona(
            user_id=user_id,
            role="ceo",
            esg_focus=["climate", "governance", "supply_chain"],
            frameworks=["BRSR", "CSRD", "TCFD"],
            geographies=["india", "eu"],
            horizon="3yr",
            decision_style="competitive_first",
            risk_appetite="balanced",
        ),
        "analyst": Persona(
            user_id=user_id,
            role="analyst",
            esg_focus=["climate", "water", "labour"],
            frameworks=["BRSR", "GRI", "ISSB"],
            geographies=["india"],
            horizon="annual",
            decision_style="regulatory_first",
            risk_appetite="balanced",
        ),
    }
    return defaults.get(
        role,
        Persona(user_id=user_id, role="other"),
    )


def deserialise_persona(data: dict[str, Any]) -> Persona:
    """Build a Persona from a dict (e.g. parsed from JSON / DB row).

    Tolerant: missing fields use the dataclass defaults; unknown
    fields are dropped silently. Lists / dicts are coerced to safe
    types (no None inside lists).
    """
    if not isinstance(data, dict):
        return Persona(user_id="")

    def _list(key: str, valid: tuple[str, ...] | None = None) -> list[str]:
        raw = data.get(key) or []
        if not isinstance(raw, list):
            return []
        out = [str(x) for x in raw if x is not None]
        if valid is not None:
            out = [x for x in out if x in valid]
        return out

    def _str(key: str, default: str, valid: tuple[str, ...] | None = None) -> str:
        v = data.get(key)
        if not isinstance(v, str) or not v:
            return default
        if valid is not None and v not in valid:
            return default
        return v

    def _affinity(key: str) -> dict[str, float]:
        raw = data.get(key) or {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in raw.items():
            try:
                out[str(k)] = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
        return out

    return Persona(
        user_id=str(data.get("user_id") or ""),
        role=_str("role", "other", VALID_ROLES),
        esg_focus=_list("esg_focus", VALID_ESG_FOCUS),
        frameworks=_list("frameworks", VALID_FRAMEWORKS),
        geographies=_list("geographies", VALID_GEOGRAPHIES),
        horizon=_str("horizon", "annual", VALID_HORIZONS),
        decision_style=_str(
            "decision_style", "narrative_first", VALID_DECISION_STYLES,
        ),
        risk_appetite=_str("risk_appetite", "balanced", VALID_RISK_APPETITES),
        click_affinity=_affinity("click_affinity"),
        skip_affinity=_affinity("skip_affinity"),
        last_active=data.get("last_active"),
        onboarded_at=data.get("onboarded_at") or _now(),
        last_edited_at=data.get("last_edited_at"),
        last_drift_update_at=data.get("last_drift_update_at"),
        version=int(data.get("version") or 1),
    )


# ---------------------------------------------------------------------------
# Onboarding MCQ — single source of truth (§8.2)
# ---------------------------------------------------------------------------


PERSONA_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "esg_focus",
        "question": "Which ESG areas matter most for your work? (Pick up to 3)",
        "type": "multi_select",
        "max_selections": 3,
        "options": [
            {"value": "climate", "label": "Climate & emissions"},
            {"value": "water", "label": "Water & resource use"},
            {"value": "biodiversity", "label": "Biodiversity & nature"},
            {"value": "labour", "label": "Labour & worker safety"},
            {"value": "supply_chain", "label": "Supply chain & sourcing"},
            {"value": "governance", "label": "Governance & ethics"},
            {"value": "dei", "label": "Diversity & inclusion"},
            {"value": "human_rights", "label": "Human rights"},
            {"value": "circular_economy", "label": "Circular economy & waste"},
            {"value": "community", "label": "Community impact"},
        ],
    },
    {
        "id": "frameworks",
        "question": "Which disclosure frameworks do you work with? (Pick up to 3)",
        "type": "multi_select",
        "max_selections": 3,
        "options": [
            {"value": "BRSR", "label": "BRSR (India / SEBI)"},
            {"value": "GRI", "label": "GRI Standards"},
            {"value": "TCFD", "label": "TCFD"},
            {"value": "CSRD", "label": "CSRD / ESRS (EU)"},
            {"value": "SASB", "label": "SASB"},
            {"value": "CDP", "label": "CDP"},
            {"value": "ISSB", "label": "ISSB / IFRS S1-S2"},
            {"value": "EU_Taxonomy", "label": "EU Taxonomy"},
            {"value": "SEC_climate", "label": "SEC Climate Rule (US)"},
            {"value": "none", "label": "None / not sure yet"},
        ],
    },
    {
        "id": "geographies",
        "question": "Which regions affect your decisions? (Pick up to 3)",
        "type": "multi_select",
        "max_selections": 3,
        "options": [
            {"value": "india", "label": "India"},
            {"value": "eu", "label": "European Union"},
            {"value": "us", "label": "United States"},
            {"value": "uk", "label": "United Kingdom"},
            {"value": "apac", "label": "APAC (ex-India)"},
            {"value": "mena", "label": "Middle East & North Africa"},
            {"value": "latam", "label": "Latin America"},
            {"value": "africa", "label": "Africa (ex-North)"},
        ],
    },
    {
        "id": "horizon",
        "question": "What time horizon matters most for your decisions?",
        "type": "single_select",
        "options": [
            {"value": "quarterly", "label": "This quarter — earnings, immediate P&L"},
            {"value": "annual", "label": "This year — annual planning, disclosure cycle"},
            {"value": "3yr", "label": "3 years — strategic plan, capex cycle"},
            {"value": "5yr_plus", "label": "5+ years — net-zero, long-term commitments"},
        ],
    },
    {
        "id": "decision_style",
        "question": "When you read an ESG signal, what do you want first?",
        "type": "single_select",
        "options": [
            {"value": "data_first", "label": "The numbers — ₹ impact, KPIs, ratios"},
            {"value": "narrative_first", "label": "The story — what happened, what it means"},
            {"value": "regulatory_first", "label": "The rule — what disclosure or framework triggers"},
            {"value": "competitive_first", "label": "The peers — how does this compare to my competitors"},
        ],
    },
    {
        "id": "risk_appetite",
        "question": "How does your organization typically respond to ESG signals?",
        "type": "single_select",
        "options": [
            {"value": "defensive", "label": "Defensive — minimize downside, stay compliant"},
            {"value": "balanced", "label": "Balanced — manage risk, capture opportunity"},
            {"value": "opportunistic", "label": "Opportunistic — lead the market, take positions"},
        ],
    },
]
