"""Phase C — Advisor event dataclasses.

Events are the *input* to the coach engine — produced by other parts
of Snowkap (scheduler, on-demand pipeline, forecaster, autoresearcher
loop, belief revision rules) and emitted into `AdvisorEngine`.

Each event carries enough context for any coach to evaluate it,
plus a `key` used by the suppression engine for dedup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AdvisorEvent:
    """Base class. Concrete subclasses set `kind` + `dedup_key`."""
    kind: str
    dedup_key: str
    tenant: str | None = None
    user: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=_utc_now)


@dataclass
class DataIngestEvent(AdvisorEvent):
    """Emitted by the scheduler when an overnight batch completes.

    `payload` shape: `{tenants_fresh: int, tenants_stale: int, failures: list[str]}`.
    """
    kind: str = "data_ingest"
    dedup_key: str = "data_ingest:overnight"


@dataclass
class RiskArticleEvent(AdvisorEvent):
    """Emitted when a HOME-tier article lands with CRITICAL or HIGH materiality."""
    kind: str = "risk_article"
    dedup_key: str = ""


@dataclass
class ForecastShiftEvent(AdvisorEvent):
    """Emitted when a forecaster output flips direction or band."""
    kind: str = "forecast_shift"
    dedup_key: str = ""


@dataclass
class BeliefRevisionEvent(AdvisorEvent):
    """Emitted when an L7 belief revision rule (R1-R6) proposes a change."""
    kind: str = "belief_revision"
    dedup_key: str = ""


@dataclass
class AutoresearcherKeepEvent(AdvisorEvent):
    """Emitted when the autoresearcher loop accepts an experiment (keep)."""
    kind: str = "autoresearcher_keep"
    dedup_key: str = ""
