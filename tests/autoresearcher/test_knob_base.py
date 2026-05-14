"""Knob ABC contract tests."""
from __future__ import annotations

import pytest

from engine.autoresearcher.knobs import (
    BLACKLIST,
    Knob,
    KnobError,
    is_blacklisted,
)


class _StubKnob(Knob):
    """Minimal concrete Knob for testing the ABC contract."""

    kind = "stub"

    def __init__(self, knob_id: str, value: float, *, baseline: float = 0.0):
        super().__init__(knob_id=knob_id)
        self._value = value
        self._baseline = baseline
        self._applied = False

    def current_value(self):
        return self._value

    def baseline_value(self):
        return self._baseline

    def magnitude_bound(self) -> float:
        return 0.5

    def apply(self, new_value) -> None:
        if abs(new_value - self._baseline) > self.magnitude_bound():
            raise KnobError(f"magnitude {abs(new_value - self._baseline)} > bound {self.magnitude_bound()}")
        self._prev = self._value
        self._value = new_value
        self._applied = True

    def revert(self) -> None:
        if not self._applied:
            raise KnobError("revert called without apply")
        self._value = self._prev
        self._applied = False

    def describe(self) -> dict:
        return {"kind": self.kind, "id": self.knob_id, "value": self._value}


def test_knob_id_required():
    """A Knob must carry a non-empty knob_id."""
    with pytest.raises(KnobError):
        _StubKnob(knob_id="", value=0.1)


def test_apply_then_revert_round_trip():
    """apply() followed by revert() restores original value."""
    k = _StubKnob(knob_id="t1", value=0.2)
    before = k.current_value()
    k.apply(0.4)
    assert k.current_value() == 0.4
    k.revert()
    assert k.current_value() == before


def test_apply_rejects_out_of_bounds():
    """A magnitude exceeding the knob's bound must raise."""
    k = _StubKnob(knob_id="t2", value=0.2, baseline=0.0)
    with pytest.raises(KnobError, match="magnitude"):
        k.apply(2.0)  # 2.0 - 0.0 = 2.0 > 0.5 bound


def test_revert_without_apply_raises():
    """revert() before apply() is a programmer error."""
    k = _StubKnob(knob_id="t3", value=0.2)
    with pytest.raises(KnobError):
        k.revert()


def test_describe_returns_serialisable_dict():
    """describe() must produce a JSON-serialisable dict with kind + id."""
    import json
    k = _StubKnob(knob_id="t4", value=0.3)
    d = k.describe()
    json.dumps(d)  # raises if not serialisable
    assert d["kind"] == "stub"
    assert d["id"] == "t4"


def test_blacklist_includes_load_bearing_knob_kinds():
    """Per the plan, these must never be touched by the autoresearcher."""
    assert "criticality_band_threshold" in BLACKLIST
    assert "mandatory_rule_toggle" in BLACKLIST
    assert "fallback_headline_toggle" in BLACKLIST


def test_is_blacklisted_detects_by_kind_or_id():
    assert is_blacklisted(kind="criticality_band_threshold") is True
    assert is_blacklisted(kind="stub") is False
    # Pattern-id check (e.g. "band:CRITICAL" matches the threshold knob)
    assert is_blacklisted(knob_id="criticality_band_threshold:CRITICAL") is True
