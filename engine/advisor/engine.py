"""Phase C — Advisor engine: event in → hint out.

Multi-coach dispatch:
  * `register_coach(coach)` adds a Coach to the registry
  * `emit_event(event)` walks the registered coaches, collects hints,
    runs each through `evaluate_suppression`, and returns the survivors
  * `dismiss_hint(hint_id, tenant, user)` records a dismissal so the
    same kind doesn't fire again for `dismissal_cooldown`

A Coach is a callable: `(event) -> list[AdvisorHint]`. Coaches that
don't care about an event return `[]` — the engine drops them cheaply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from engine.advisor.events import AdvisorEvent
from engine.advisor.suppression import SuppressionState, evaluate_suppression


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_hint_id() -> str:
    return f"hint-{uuid4().hex[:12]}"


@dataclass
class AdvisorHint:
    """A push-style suggestion the user might want to act on.

    `severity` is informational — UI picks colour from it (low=blue,
    moderate=amber, high=red). Suppression decisions are based on
    `kind` + `dedup_key`, not severity.
    """
    coach: str
    kind: str
    severity: str
    headline: str
    body: str
    dedup_key: str
    tenant: str | None = None
    user: str | None = None
    cta_label: str | None = None
    cta_target: str | None = None
    hint_id: str = field(default_factory=_new_hint_id)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict:
        return {
            "hint_id": self.hint_id,
            "coach": self.coach,
            "kind": self.kind,
            "severity": self.severity,
            "headline": self.headline,
            "body": self.body,
            "tenant": self.tenant,
            "user": self.user,
            "cta_label": self.cta_label,
            "cta_target": self.cta_target,
            "created_at": self.created_at,
        }


class Coach(Protocol):
    """A coach is anything that takes an event and returns 0+ hints."""

    name: str

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        ...


@dataclass
class AdvisorEngine:
    coaches: list[Coach] = field(default_factory=list)
    state: SuppressionState = field(default_factory=SuppressionState)
    suppression_config: dict | None = None

    def register_coach(self, coach: Coach) -> None:
        self.coaches.append(coach)

    def emit_event(self, event: AdvisorEvent) -> list[AdvisorHint]:
        """Walk the coach list, collect candidates, apply suppression."""
        survivors: list[AdvisorHint] = []
        for coach in self.coaches:
            try:
                candidates = coach.evaluate(event)
            except Exception:  # noqa: BLE001 — one coach failing must not break the others
                continue
            for hint in candidates:
                reason = evaluate_suppression(
                    self.state,
                    kind=hint.kind,
                    dedup_key=hint.dedup_key,
                    tenant=hint.tenant,
                    user=hint.user,
                    config=self.suppression_config,
                )
                if reason is None:
                    survivors.append(hint)
        return survivors

    def dismiss(self, *, kind: str, tenant: str | None, user: str | None) -> None:
        """Record a user dismissal — future hints of `kind` are suppressed."""
        self.state.dismiss(kind, tenant, user)
