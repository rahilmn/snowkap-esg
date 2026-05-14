"""Autoresearcher Coach — surfaces top-hit experiments + advisor queue spillover.

Fires when the autoresearcher loop accepts a `keep` with a
metric_delta above the editorial threshold (default +0.03 = 3pp on the
composite calibration metric). These are the experiments most worth
human review before they hit production.
"""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor.engine import AdvisorHint
from engine.advisor.events import AdvisorEvent, AutoresearcherKeepEvent


_EDITORIAL_DELTA = 0.03   # +3pp on the calibration metric is the bar


@dataclass
class AutoresearcherCoach:
    name: str = "autoresearcher_coach"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        if not isinstance(event, AutoresearcherKeepEvent):
            return []
        metric_delta = float(event.payload.get("metric_delta") or 0.0)
        if metric_delta < _EDITORIAL_DELTA:
            return []
        knob_id = str(event.payload.get("knob_id") or "unknown_knob")
        tier = str(event.payload.get("tier") or "system")
        return [AdvisorHint(
            coach=self.name,
            kind="autoresearcher_top_hit",
            severity="moderate",
            headline=f"Autoresearcher top hit ({tier}): Δ=+{metric_delta:.3f}",
            body=(
                f"Knob `{knob_id}` improved the calibration metric by "
                f"{metric_delta:+.3f}. Review on the advisor queue and "
                "approve to promote into the live pipeline."
            ),
            dedup_key=f"autoresearcher_coach:{tier}:{knob_id}",
            tenant=event.tenant,
            cta_label="Open autoresearcher queue →",
            cta_target="/settings/autoresearcher",
        )]
