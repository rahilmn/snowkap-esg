"""Data Coach — surfaces freshness gaps in tenant news coverage."""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor.engine import AdvisorHint
from engine.advisor.events import AdvisorEvent, DataIngestEvent


@dataclass
class DataCoach:
    name: str = "data_coach"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        if not isinstance(event, DataIngestEvent):
            return []
        stale = int(event.payload.get("tenants_stale", 0))
        failures = list(event.payload.get("failures", []))
        hints: list[AdvisorHint] = []
        if stale >= 3:
            hints.append(AdvisorHint(
                coach=self.name,
                kind="freshness_gap",
                severity="moderate",
                headline=f"{stale} tenants have no fresh news in >24h",
                body=(
                    "Check the NewsAPI.ai monthly quota and the Google News fallback. "
                    "Tenants without coverage can't trigger advisor signals."
                ),
                dedup_key=f"data_coach:freshness:{stale}",
                cta_label="Open ingest health →",
                cta_target="/settings/ingest",
            ))
        if failures:
            hints.append(AdvisorHint(
                coach=self.name,
                kind="ingest_failure",
                severity="high",
                headline=f"Ingest failed for {len(failures)} tenant(s)",
                body=", ".join(failures[:5]),
                dedup_key=f"data_coach:failure:{','.join(sorted(failures))[:64]}",
                cta_label="View failures →",
                cta_target="/settings/ingest",
            ))
        return hints
