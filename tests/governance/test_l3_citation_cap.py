"""L3 — Citation cap + verbatim sign-off.

Data-layer enforcement of the Toulmin citation discipline:
  - Every Toulmin grounds[] list is capped at MAX_GROUNDS (5)
  - Grounds entries must be non-empty strings
  - "Verbatim sign-off" — entries with `tags.uncertainty == "unverified"`
    cannot be persisted via `append_decision` (must be sanitised first).
    This forces the engine to label every persisted claim as low/moderate/
    high but never `unverified` — that label is reserved for in-flight
    candidates, not journal entries.
  - Citation-cap violation is a hard error (raises), NOT advisory. The
    whole point is to prevent prompt-bloat hallucinations.
"""
from __future__ import annotations

import pytest

from engine.audit import (
    MAX_TOULMIN_GROUNDS,
    append_decision,
    enforce_citation_cap,
    make_toulmin,
)


VALID_TAGS = {
    "scope": "tenant",
    "signal_type": "analyst_judgment",
    "attribution": "criticality_scorer",
    "uncertainty": "low",
}


def test_max_grounds_constant_is_five():
    """L3 — Cap is exactly 5 per the v2 plan (matches GPT-4.1's
    effective working-memory for citations in a single insight)."""
    assert MAX_TOULMIN_GROUNDS == 5


def test_enforce_citation_cap_accepts_five():
    """L3 — Five grounds is the legal maximum, not a violation."""
    grounds = [f"line {i}" for i in range(5)]
    out = enforce_citation_cap({"claim": "x", "grounds": grounds, "warrant": "y"})
    assert out["grounds"] == grounds


def test_enforce_citation_cap_rejects_six():
    """L3 — Six is a violation. Hard error, not silent truncation."""
    grounds = [f"line {i}" for i in range(6)]
    with pytest.raises(ValueError, match="citation cap"):
        enforce_citation_cap({"claim": "x", "grounds": grounds, "warrant": "y"})


def test_enforce_citation_cap_rejects_empty_string_grounds():
    """L3 — Empty/whitespace grounds are noise; reject before they
    waste capacity within the 5-cap."""
    with pytest.raises(ValueError, match="empty"):
        enforce_citation_cap({
            "claim": "x",
            "grounds": ["valid", "  ", "another"],
            "warrant": "y",
        })


def test_enforce_citation_cap_tolerates_no_toulmin():
    """L3 — Decisions without Toulmin (e.g. low-stakes engine moves)
    don't trip the cap. None / empty dict passes through."""
    assert enforce_citation_cap(None) is None
    assert enforce_citation_cap({}) == {}


def test_append_decision_enforces_citation_cap(tmp_path):
    """L3 — `append_decision` MUST run the cap before writing."""
    with pytest.raises(ValueError, match="citation cap"):
        append_decision(
            "materiality_downgrade",
            article_id="art_bloated",
            toulmin={"claim": "x", "grounds": [f"g{i}" for i in range(7)], "warrant": "y"},
            tags=VALID_TAGS,
            base_data_dir=tmp_path,
        )


def test_append_decision_rejects_unverified_uncertainty(tmp_path):
    """L3 — Verbatim sign-off: `unverified` is in-flight only, never
    journal-worthy. Forces the caller to take a position before append.
    """
    with pytest.raises(ValueError, match="unverified"):
        append_decision(
            "materiality_downgrade",
            article_id="art_unverified",
            toulmin=make_toulmin("x", ["g"], "w"),
            tags={**VALID_TAGS, "uncertainty": "unverified"},
            base_data_dir=tmp_path,
        )


def test_append_decision_accepts_legal_grounds_count(tmp_path):
    """L3 — Happy path: 3 grounds, low uncertainty, passes through cleanly."""
    append_decision(
        "materiality_downgrade",
        article_id="art_good",
        toulmin=make_toulmin("claim", ["g1", "g2", "g3"], "w"),
        tags=VALID_TAGS,
        base_data_dir=tmp_path,
    )
    # No exception = pass. Read it back to confirm grounds preserved.
    from engine.audit import read_decision_log
    entries = list(read_decision_log(base_data_dir=tmp_path))
    assert entries[0]["toulmin"]["grounds"] == ["g1", "g2", "g3"]
