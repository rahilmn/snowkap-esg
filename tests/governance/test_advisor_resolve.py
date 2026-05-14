"""L6 — Advisor resolve helpers (open_advisor_events + resolve_advisor_event)."""
from __future__ import annotations

import pytest

from engine.audit import (
    _advisor_event_id,
    append_decision,
    make_toulmin,
    open_advisor_events,
    read_advisor_resolutions,
    resolve_advisor_event,
    route_unverified_to_advisor,
)


VALID_TAGS = {
    "scope": "tenant",
    "signal_type": "analyst_judgment",
    "attribution": "criticality_scorer",
    "uncertainty": "high",
}


def _seed_high_uncertainty(tmp_path, tenant: str, article: str) -> None:
    append_decision(
        "materiality_downgrade",
        article_id=article,
        company_slug=tenant,
        toulmin=make_toulmin("x", ["y"], "z", qualifier="q"),
        tags=VALID_TAGS,
        base_data_dir=tmp_path,
    )


def test_advisor_event_id_is_deterministic():
    """L6 — same input → same ID across calls (used for resolution lookups)."""
    ev = {"ts": "2026-05-13T10:00:00+00:00", "event_type": "x", "article_id": "art_1"}
    assert _advisor_event_id(ev) == _advisor_event_id(ev)


def test_advisor_event_id_distinguishes_distinct_events():
    ev1 = {"ts": "2026-05-13T10:00:00+00:00", "event_type": "x", "article_id": "a"}
    ev2 = {"ts": "2026-05-13T10:00:01+00:00", "event_type": "x", "article_id": "a"}
    assert _advisor_event_id(ev1) != _advisor_event_id(ev2)


def test_open_advisor_events_returns_all_when_no_resolutions(tmp_path):
    _seed_high_uncertainty(tmp_path, "adani-power", "art_1")
    _seed_high_uncertainty(tmp_path, "adani-power", "art_2")
    events = open_advisor_events(base_data_dir=tmp_path)
    assert len(events) == 2
    # Each event surfaces a synthesised event_id
    for e in events:
        assert "event_id" in e and len(e["event_id"]) >= 8


def test_open_advisor_events_filters_resolved(tmp_path):
    _seed_high_uncertainty(tmp_path, "adani-power", "art_1")
    _seed_high_uncertainty(tmp_path, "adani-power", "art_2")
    all_events = open_advisor_events(base_data_dir=tmp_path)
    target = all_events[0]
    resolve_advisor_event(
        event_id=target["event_id"],
        resolution="approve",
        actor="manual:alice@snowkap.com",
        rationale="reviewed and accurate",
        base_data_dir=tmp_path,
    )
    remaining = open_advisor_events(base_data_dir=tmp_path)
    assert len(remaining) == 1
    assert remaining[0]["event_id"] != target["event_id"]


def test_open_advisor_events_filters_by_tenant(tmp_path):
    _seed_high_uncertainty(tmp_path, "adani-power", "art_a")
    _seed_high_uncertainty(tmp_path, "jsw-energy", "art_j")
    just_adani = open_advisor_events(base_data_dir=tmp_path, tenant="adani-power")
    assert len(just_adani) == 1
    assert just_adani[0]["company_slug"] == "adani-power"


def test_resolve_advisor_event_appends_record(tmp_path):
    _seed_high_uncertainty(tmp_path, "adani-power", "art_1")
    target = open_advisor_events(base_data_dir=tmp_path)[0]
    resolve_advisor_event(
        event_id=target["event_id"],
        resolution="reject",
        actor="manual:bob@snowkap.com",
        rationale="false positive",
        base_data_dir=tmp_path,
    )
    resolutions = list(read_advisor_resolutions(base_data_dir=tmp_path))
    assert len(resolutions) == 1
    assert resolutions[0]["resolution"] == "reject"
    assert resolutions[0]["actor"] == "manual:bob@snowkap.com"


def test_resolve_advisor_event_validates_inputs(tmp_path):
    with pytest.raises(ValueError, match="resolution"):
        resolve_advisor_event(
            event_id="x", resolution="defer",  # not allowed
            actor="manual:a@b.c", base_data_dir=tmp_path,
        )
    with pytest.raises(ValueError, match="event_id"):
        resolve_advisor_event(
            event_id="", resolution="approve",
            actor="manual:a@b.c", base_data_dir=tmp_path,
        )
    with pytest.raises(ValueError, match="actor"):
        resolve_advisor_event(
            event_id="x", resolution="approve", actor="",
            base_data_dir=tmp_path,
        )


def test_apply_resolution_action_returns_none_for_high_uncertainty(tmp_path):
    """High-uncertainty events don't have a candidate to promote;
    the side-effect path no-ops."""
    from engine.audit import apply_resolution_action
    fake_event = {
        "event_type": "high_uncertainty_decision",
        "article_id": "art_1",
        "company_slug": "adani-power",
    }
    result = apply_resolution_action(
        event=fake_event, resolution="approve",
        actor="manual:alice@snowkap.com", rationale="reviewed",
    )
    assert result is None


def test_apply_resolution_action_calls_promoter_for_unverified_candidate(monkeypatch, tmp_path):
    """approve on an unverified_candidate → promoter.manual_decide(promote)."""
    from engine.audit import apply_resolution_action
    captured = {}

    class _StubResult:
        ok = True
        message = "promoted"
        category = "entity"
        slug = "tata-chemicals"

    def fake_manual_decide(*, candidate_id, decision, toulmin, user_id):
        captured["candidate_id"] = candidate_id
        captured["decision"] = decision
        captured["toulmin"] = toulmin
        captured["user_id"] = user_id
        return _StubResult()

    import engine.ontology.discovery.promoter as _promoter
    monkeypatch.setattr(_promoter, "manual_decide", fake_manual_decide)

    fake_event = {
        "event_type": "unverified_candidate",
        "candidate_id": "tata-chemicals",
        "category": "entity",
    }
    result = apply_resolution_action(
        event=fake_event, resolution="approve",
        actor="manual:alice@snowkap.com",
        rationale="3+ articles, 2+ sources, confidence high",
    )
    assert result is not None
    assert result["ok"] is True
    assert captured["candidate_id"] == "entity:tata-chemicals"
    assert captured["decision"] == "promote"
    assert captured["user_id"] == "alice@snowkap.com"
    assert "3+ articles" in captured["toulmin"]["grounds"][0]


def test_apply_resolution_action_rejects_via_promoter_too(monkeypatch):
    """reject also routes through the promoter (so the candidate buffer
    is updated, not just the resolution log)."""
    from engine.audit import apply_resolution_action

    class _StubResult:
        ok = True
        message = "rejected"
        category = "entity"
        slug = "x"

    seen = {}
    def fake_manual_decide(**kwargs):
        seen.update(kwargs)
        return _StubResult()

    import engine.ontology.discovery.promoter as _promoter
    monkeypatch.setattr(_promoter, "manual_decide", fake_manual_decide)

    fake_event = {
        "event_type": "unverified_candidate",
        "candidate_id": "x", "category": "entity",
    }
    apply_resolution_action(
        event=fake_event, resolution="reject",
        actor="manual:b@c.d", rationale="off-topic",
    )
    assert seen["decision"] == "reject"


def test_apply_resolution_action_handles_promoter_failure(monkeypatch):
    """Promoter raising MUST NOT propagate — the resolution log is
    already durable; we just report the side-effect failure."""
    from engine.audit import apply_resolution_action

    def boom(**kwargs):
        raise RuntimeError("ttl write failed")

    import engine.ontology.discovery.promoter as _promoter
    monkeypatch.setattr(_promoter, "manual_decide", boom)

    fake_event = {
        "event_type": "unverified_candidate",
        "candidate_id": "x", "category": "entity",
    }
    result = apply_resolution_action(
        event=fake_event, resolution="approve",
        actor="manual:b@c.d", rationale="r",
    )
    assert result is not None
    assert result["ok"] is False
    assert "promoter call failed" in result["message"]


def test_apply_resolution_action_handles_missing_fields():
    """Malformed event (no candidate_id / category) returns a failure
    shape, not a crash."""
    from engine.audit import apply_resolution_action
    incomplete = {"event_type": "unverified_candidate"}  # no candidate_id/category
    result = apply_resolution_action(
        event=incomplete, resolution="approve",
        actor="manual:a@b.c", rationale="r",
    )
    assert result is not None
    assert result["ok"] is False
    assert "missing" in result["message"].lower()


def test_open_advisor_includes_unverified_candidates(tmp_path):
    """L6 — `unverified_candidate` events flow through the same queue."""
    route_unverified_to_advisor(
        candidate_id="cand_001",
        category="materiality_revision",
        rationale="needs analyst",
        tags={
            "scope": "tenant",
            "signal_type": "analyst_judgment",
            "attribution": "manual:queue_dispatcher",
            "uncertainty": "unverified",
        },
        base_data_dir=tmp_path,
    )
    events = open_advisor_events(base_data_dir=tmp_path)
    types = [e["event_type"] for e in events]
    assert "unverified_candidate" in types
