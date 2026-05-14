"""Forecast Coach — flags sentiment-trajectory flips and band changes."""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor.engine import AdvisorHint
from engine.advisor.events import AdvisorEvent, ForecastShiftEvent


_FLIP_DIRECTIONS = frozenset({"stable_to_declining", "stable_to_improving",
                              "improving_to_declining", "declining_to_improving"})


@dataclass
class ForecastCoach:
    name: str = "forecast_coach"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        if not isinstance(event, ForecastShiftEvent):
            return []
        flip = str(event.payload.get("flip") or "").lower()
        if flip not in _FLIP_DIRECTIONS:
            return []
        horizon = str(event.payload.get("horizon") or "3m")
        tenant = event.tenant or "unknown"
        sev = "high" if flip.endswith("to_declining") else "moderate"
        nice = flip.replace("_", " ")
        return [AdvisorHint(
            coach=self.name,
            kind="forecast_flip",
            severity=sev,
            headline=f"{tenant}: {horizon} forecast flipped {nice}",
            body=(
                f"The {horizon} sentiment trajectory changed direction. "
                "Recommend reviewing the advisor queue for queued belief proposals."
            ),
            dedup_key=f"forecast_coach:{tenant}:{horizon}:{flip}",
            tenant=event.tenant,
            cta_label="Open advisor queue →",
            cta_target="/settings/advisor",
        )]
