"""Phase C — AdvisorEngine + 5-layer suppression tests."""
from __future__ import annotations

from dataclasses import dataclass

from engine.advisor import AdvisorEngine, AdvisorHint, DataIngestEvent
from engine.advisor.events import AdvisorEvent


@dataclass
class _AlwaysFiresCoach:
    name: str = "always"

    def evaluate(self, event: AdvisorEvent) -> list[AdvisorHint]:
        return [AdvisorHint(
            coach=self.name,
            kind="test_kind",
            severity="low",
            headline="hint",
            body="body",
            dedup_key=f"always:{event.dedup_key}",
        )]


def test_emit_event_returns_hints_from_registered_coach():
    eng = AdvisorEngine()
    eng.register_coach(_AlwaysFiresCoach())
    ev = DataIngestEvent(payload={"tenants_stale": 4})
    hints = eng.emit_event(ev)
    assert len(hints) == 1
    assert hints[0].kind == "test_kind"


def test_dedup_layer_suppresses_repeat_within_window():
    eng = AdvisorEngine()
    eng.register_coach(_AlwaysFiresCoach())
    ev = DataIngestEvent(payload={"tenants_stale": 4})
    first = eng.emit_event(ev)
    # Second emit with same dedup key inside dedup window → suppressed
    second = eng.emit_event(ev)
    assert len(first) == 1
    assert len(second) == 0


def test_dismissal_blocks_further_kind():
    eng = AdvisorEngine()
    eng.register_coach(_AlwaysFiresCoach())
    eng.dismiss(kind="test_kind", tenant=None, user=None)
    hints = eng.emit_event(DataIngestEvent(payload={"tenants_stale": 4}))
    assert hints == []


def test_session_volume_cap_holds():
    eng = AdvisorEngine(suppression_config={"session_volume_cap": 2,
                                            "kind_cooldown_s": 0,
                                            "dedup_window_s": 0})

    @dataclass
    class _Bursty:
        name: str = "bursty"
        counter: int = 0

        def evaluate(self, _event):
            self.counter += 1
            return [AdvisorHint(
                coach=self.name, kind=f"k{self.counter}",
                severity="low", headline="x", body="x",
                dedup_key=f"bursty:{self.counter}",
                tenant="t1", user="u1",
            )]

    eng.register_coach(_Bursty())
    out = []
    for _ in range(5):
        out.extend(eng.emit_event(DataIngestEvent(payload={})))
    assert len(out) == 2  # session_volume_cap=2


def test_one_failing_coach_does_not_break_others():
    @dataclass
    class _Boom:
        name: str = "boom"

        def evaluate(self, _event):
            raise RuntimeError("kaboom")

    eng = AdvisorEngine()
    eng.register_coach(_Boom())
    eng.register_coach(_AlwaysFiresCoach())
    out = eng.emit_event(DataIngestEvent(payload={"tenants_stale": 4}))
    assert len(out) == 1
    assert out[0].coach == "always"
