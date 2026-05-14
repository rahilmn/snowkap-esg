"""Risk Coach — flags CRITICAL/HIGH-materiality articles + hallucination fires."""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor.engine import AdvisorHint
from engine.advisor.events import AdvisorEvent, RiskArticleEvent


_CRITICAL_BANDS = frozenset({"CRITICAL", "HIGH"})


@dataclass
class RiskCoach:
    name: str = "risk_coach"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        if not isinstance(event, RiskArticleEvent):
            return []
        materiality = str(event.payload.get("materiality") or "").upper()
        if materiality not in _CRITICAL_BANDS:
            return []
        title = str(event.payload.get("title") or "untitled")
        article_id = str(event.payload.get("article_id") or "")
        tenant = event.tenant or "unknown"
        return [AdvisorHint(
            coach=self.name,
            kind="critical_article",
            severity="high" if materiality == "CRITICAL" else "moderate",
            headline=f"{tenant}: {materiality} signal",
            body=title[:240],
            dedup_key=f"risk_coach:{tenant}:{article_id}",
            tenant=event.tenant,
            cta_label="Open in feed →",
            cta_target=f"/home?company={tenant}&article={article_id}",
        )]
