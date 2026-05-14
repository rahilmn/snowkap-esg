"""Set-membership knob — tunes `triggersRiskCategory` /
`triggersTEMPLES` set membership for one (topic, category) pair.

Sibling to KeywordSetKnob; differs only in the predicate it applies to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from engine.autoresearcher.knobs import Knob, KnobError

Action = Literal["add", "remove"]


@dataclass
class SetMembershipState:
    """Snapshot of `triggers<X>` predicate → set of objects.

    Keyed by (predicate, subject) → frozenset[str] of objects.
    """
    sets: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)

    def get(self, predicate: str, subject: str) -> frozenset[str]:
        return self.sets.get((predicate, subject), frozenset())

    def set(self, predicate: str, subject: str, values: frozenset[str]) -> None:
        self.sets[(predicate, subject)] = values


class SetMembershipKnob(Knob):
    kind = "set_membership"

    def __init__(
        self,
        *,
        predicate: str,
        subject: str,
        member: str,
        action: Action,
        state: SetMembershipState,
    ):
        if action not in ("add", "remove"):
            raise KnobError(f"action must be 'add' or 'remove', got {action!r}")
        knob_id = f"{predicate}:{subject}:{member}:{action}"
        super().__init__(knob_id=knob_id)
        self.predicate = predicate
        self.subject = subject
        self.member = member
        self.action = action
        self._state = state
        self._baseline = state.get(predicate, subject)
        self._prev: frozenset[str] | None = None

    def current_value(self) -> frozenset[str]:
        return self._state.get(self.predicate, self.subject)

    def baseline_value(self) -> frozenset[str]:
        return self._baseline

    def magnitude_bound(self) -> float:
        return 1.0

    def apply(self, new_value: Any = None) -> None:
        current = frozenset(self._state.get(self.predicate, self.subject))
        self._prev = current
        if self.action == "add":
            self._state.set(self.predicate, self.subject, current | {self.member})
        else:
            self._state.set(self.predicate, self.subject, current - {self.member})

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.predicate, self.subject, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "predicate": self.predicate,
            "subject": self.subject,
            "member": self.member,
            "action": self.action,
            "current_size": len(self.current_value()),
            "baseline_size": len(self.baseline_value()),
        }
