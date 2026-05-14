"""Keyword-set knob — tunes set membership for an EventType's keyword list.

Apply adds OR removes a single keyword. Revert undoes the change.
The state is a snapshot dict `{event_type: frozenset[str]}` that the
evaluator's pipeline replay reads via dependency injection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from engine.autoresearcher.knobs import Knob, KnobError

Action = Literal["add", "remove"]


@dataclass
class KeywordSetState:
    """Snapshot of event-type → keyword-set mapping."""
    sets: dict[str, frozenset[str]] = field(default_factory=dict)

    def get(self, event_type: str) -> frozenset[str]:
        return self.sets.get(event_type, frozenset())

    def set(self, event_type: str, keywords: frozenset[str]) -> None:
        self.sets[event_type] = keywords


class KeywordSetKnob(Knob):
    """Tunes one (event_type, keyword) membership."""

    kind = "keyword_set_membership"

    def __init__(
        self,
        *,
        event_type: str,
        keyword: str,
        action: Action,
        state: KeywordSetState,
    ):
        if action not in ("add", "remove"):
            raise KnobError(f"action must be 'add' or 'remove', got {action!r}")
        knob_id = f"{event_type}:{keyword}:{action}"
        super().__init__(knob_id=knob_id)
        self.event_type = event_type
        self.keyword = keyword
        self.action = action
        self._state = state
        self._baseline = state.get(event_type)
        self._prev: frozenset[str] | None = None

    def current_value(self) -> frozenset[str]:
        return self._state.get(self.event_type)

    def baseline_value(self) -> frozenset[str]:
        return self._baseline

    def magnitude_bound(self) -> float:
        return 1.0  # single keyword change is by definition magnitude 1

    def apply(self, new_value: Any = None) -> None:
        """Apply the configured add/remove action.

        `new_value` is ignored — the knob's action + keyword are fixed
        at construction. This preserves the Knob contract while keeping
        set-valued tuning declarative.
        """
        self._prev = frozenset(self._state.get(self.event_type))
        if self.action == "add":
            self._state.set(self.event_type, self._prev | {self.keyword})
        else:  # remove
            self._state.set(self.event_type, self._prev - {self.keyword})

    def revert(self) -> None:
        if self._prev is None:
            raise KnobError("revert called without apply")
        self._state.set(self.event_type, self._prev)
        self._prev = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.knob_id,
            "event_type": self.event_type,
            "keyword": self.keyword,
            "action": self.action,
            "current_set_size": len(self.current_value()),
            "baseline_set_size": len(self.baseline_value()),
        }
