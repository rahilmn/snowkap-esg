"""L6 — Advisor queue (reactive observability).

When an audit entry has `tags.uncertainty in ("high",)` or carries an
`unverified` candidate, fire a structured event into the advisor queue.
The L7 CompanyAgent will subscribe to this queue.

Co-design with L3:
  - L3 forbids `unverified` at the journal layer (it raises)
  - L6 provides `route_unverified_to_advisor()` as the alternative path
  - `high` uncertainty CAN be journalled, but ALSO fires an advisor event
    (the journal records the decision; the advisor flags it for review)
"""
from __future__ import annotations

import json

import pytest

from engine.audit import (
    ADVISOR_QUEUE,
    append_decision,
    route_unverified_to_advisor,
    read_advisor_queue,
)


VALID_TAGS = {
    "scope": "tenant",
    "signal_type": "analyst_judgment",
    "attribution": "criticality_scorer",
    "uncertainty": "low",
}


def test_append_decision_with_high_uncertainty_emits_advisor_event(tmp_path):
    """L6 — `tags.uncertainty='high'` is journalled AND advisor-queued.

    The journal records the decision was made; the advisor queue flags
    it for human review. Both fire on the same `append_decision` call.
    """
    append_decision(
        "materiality_downgrade",
        article_id="art_risky",
        toulmin={
            "claim": "exposure may exceed reported figure",
            "grounds": ["primitive cascade only", "no article line"],
            "warrant": "engine estimate, β confidence=medium",
            "qualifier": "assuming cascade base rate holds",
        },
        tags={**VALID_TAGS, "uncertainty": "high"},
        base_data_dir=tmp_path,
    )
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "high_uncertainty_decision"
    assert ev["article_id"] == "art_risky"
    assert ev["tags"]["uncertainty"] == "high"
    assert ev["source_decision_type"] == "materiality_downgrade"


def test_append_decision_with_low_uncertainty_no_advisor_event(tmp_path):
    """L6 — low/moderate uncertainty does NOT page the advisor."""
    append_decision(
        "materiality_downgrade",
        article_id="art_calm",
        toulmin={"claim": "x", "grounds": ["y"], "warrant": "z"},
        tags={**VALID_TAGS, "uncertainty": "low"},
        base_data_dir=tmp_path,
    )
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert events == []


def test_route_unverified_to_advisor_writes_to_queue(tmp_path):
    """L6 — `unverified` candidates routed via `route_unverified_to_advisor`
    instead of `append_decision` (which would raise per L3).
    """
    route_unverified_to_advisor(
        candidate_id="cand_001",
        category="materiality_revision",
        rationale="cascade β diverges 40% from peer benchmark — needs analyst",
        tags={**VALID_TAGS, "uncertainty": "unverified"},
        base_data_dir=tmp_path,
    )
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "unverified_candidate"
    assert ev["candidate_id"] == "cand_001"
    assert ev["category"] == "materiality_revision"
    assert ev["tags"]["uncertainty"] == "unverified"


def test_route_unverified_to_advisor_rejects_non_unverified(tmp_path):
    """L6 — `route_unverified_to_advisor` requires the candidate to actually
    be unverified. Catches the mis-routing case (low-uncertainty entries
    belong in the journal, not the advisor queue)."""
    with pytest.raises(ValueError, match="unverified"):
        route_unverified_to_advisor(
            candidate_id="cand_misrouted",
            category="materiality_revision",
            rationale="x",
            tags=VALID_TAGS,  # uncertainty=low
            base_data_dir=tmp_path,
        )


def test_advisor_queue_file_is_jsonl(tmp_path):
    """L6 — advisor_queue uses the same append-only JSONL format as the
    other audit logs so existing readers / cron tooling work unchanged."""
    append_decision(
        "materiality_downgrade",
        article_id="a",
        toulmin={"claim": "x", "grounds": ["y"], "warrant": "z", "qualifier": "q"},
        tags={**VALID_TAGS, "uncertainty": "high"},
        base_data_dir=tmp_path,
    )
    queue_path = tmp_path / "audit" / ADVISOR_QUEUE
    assert queue_path.exists()
    raw = queue_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    parsed = json.loads(raw[0])  # must parse cleanly
    assert "ts" in parsed
    assert "event_type" in parsed


def test_advisor_queue_filename_constant():
    """L6 — Filename is `advisor_queue.jsonl`, a sibling of the existing
    JSONL logs in `data/audit/`."""
    assert ADVISOR_QUEUE == "advisor_queue.jsonl"


def test_multiple_high_uncertainty_decisions_accumulate(tmp_path):
    """L6 — Queue is append-only; multiple events accumulate in order."""
    for i in range(3):
        append_decision(
            "materiality_downgrade",
            article_id=f"art_{i}",
            toulmin={"claim": f"c{i}", "grounds": [f"g{i}"], "warrant": "w", "qualifier": "q"},
            tags={**VALID_TAGS, "uncertainty": "high"},
            base_data_dir=tmp_path,
        )
    events = list(read_advisor_queue(base_data_dir=tmp_path))
    assert len(events) == 3
    assert [e["article_id"] for e in events] == ["art_0", "art_1", "art_2"]
