"""Knob ABC — the tunable-state abstraction for the autoresearcher loop.

A Knob is a pure-functional + reversible representation of a single
piece of system state the autoresearcher is allowed to perturb. Every
Knob satisfies:

  - `apply(new_value)` mutates an in-memory snapshot of state
  - `revert()` restores the pre-apply value exactly
  - `magnitude_bound()` caps how far a single experiment may push
  - `describe()` serialises to a JSON-friendly dict for ledger + audit

Knob subclasses live in `engine/autoresearcher/knob_kinds/`. Each
subclass declares a class-level `kind` string (`"materiality_weight"`,
`"scorer_component_weight"`, etc.) so the ledger + introspector + UI
can route knobs by kind.

The BLACKLIST is a single source of truth for "this kind / this knob
id MUST NEVER be tuned, even if discovered". Items on the blacklist
trip an immediate `KnobError` at apply() time, so even a buggy
experimenter cannot accidentally touch load-bearing state.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any


class KnobError(RuntimeError):
    """Raised on invalid Knob operations (bad bounds, revert-before-apply, blacklist hit)."""


# Kinds that are LOAD-BEARING — any attempt to apply a knob of these
# kinds raises KnobError before touching state. The 13 atomic knobs
# under these kinds (4 band thresholds + 4 mandatory toggles + 5
# fallback toggles) are protected.
BLACKLIST: frozenset[str] = frozenset({
    "criticality_band_threshold",
    "mandatory_rule_toggle",
    "fallback_headline_toggle",
})


# Knob-id patterns that map to a blacklisted kind even when the Knob
# instance's declared `kind` differs (e.g. a generic ordinal-mapping
# knob whose id happens to encode a band threshold).
_BLACKLIST_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^criticality_band_threshold:"),
    re.compile(r"^mandatory_rule_toggle:"),
    re.compile(r"^fallback_headline_toggle:"),
)


def is_blacklisted(*, kind: str | None = None, knob_id: str | None = None) -> bool:
    """Return True if the kind or id pattern indicates a load-bearing knob."""
    if kind and kind in BLACKLIST:
        return True
    if knob_id:
        for pat in _BLACKLIST_ID_PATTERNS:
            if pat.match(knob_id):
                return True
    return False


class Knob(ABC):
    """Base class for all autoresearcher knobs.

    Subclasses MUST:
      - declare a class-level `kind: str`
      - implement `current_value`, `baseline_value`, `magnitude_bound`,
        `apply(new_value)`, `revert()`, `describe()`
    """

    #: Subclasses override this with a stable, unique kind slug.
    kind: str = "abstract"

    def __init__(self, knob_id: str):
        if not knob_id or not isinstance(knob_id, str):
            raise KnobError(f"knob_id must be a non-empty str, got {knob_id!r}")
        if is_blacklisted(kind=self.kind, knob_id=knob_id):
            raise KnobError(
                f"Knob blocked at construction: kind={self.kind!r} id={knob_id!r} "
                f"is on the load-bearing blacklist"
            )
        self.knob_id = knob_id

    # ---- contract --------------------------------------------------------

    @abstractmethod
    def current_value(self) -> Any:
        """Read the current value (without mutating)."""

    @abstractmethod
    def baseline_value(self) -> Any:
        """The starting value before any apply() — used by magnitude bound."""

    @abstractmethod
    def magnitude_bound(self) -> float:
        """Maximum allowed |new_value - baseline_value| in one experiment."""

    @abstractmethod
    def apply(self, new_value: Any) -> None:
        """Mutate the in-memory snapshot. Must raise KnobError if out of bounds."""

    @abstractmethod
    def revert(self) -> None:
        """Restore the value to what it was immediately before apply().

        Must raise KnobError when called without a prior apply().
        """

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """JSON-friendly snapshot of {kind, id, value, baseline, ...}."""

    # ---- helpers ---------------------------------------------------------

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} kind={self.kind} id={self.knob_id} value={self.current_value()}>"
