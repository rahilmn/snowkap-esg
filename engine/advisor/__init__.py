"""Phase C — Multi-coach advisor dispatch (port of segmentation_agent/advisor/).

Push-style intelligence surface. Each coach inspects an event and may
emit one or more hints. Hints flow through a suppression engine
(dedup + dismissal-tracking + cooldown + global volume cap) before
reaching the user's session.

Hints are rendered as SSE `advisor_hint` events on the chat stream,
or as cards on the chat page sidebar.
"""
from engine.advisor.engine import AdvisorEngine, AdvisorHint
from engine.advisor.events import (
    AdvisorEvent,
    AutoresearcherKeepEvent,
    BeliefRevisionEvent,
    DataIngestEvent,
    ForecastShiftEvent,
    RiskArticleEvent,
)
from engine.advisor.suppression import SuppressionState

__all__ = [
    "AdvisorEngine",
    "AdvisorEvent",
    "AdvisorHint",
    "AutoresearcherKeepEvent",
    "BeliefRevisionEvent",
    "DataIngestEvent",
    "ForecastShiftEvent",
    "RiskArticleEvent",
    "SuppressionState",
]
