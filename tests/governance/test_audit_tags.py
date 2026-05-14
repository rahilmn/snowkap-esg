"""L2 — Universal 4-tag governance schema for audit entries.

Verifies the v2 plan's L2 deliverable:
  - `_validate_tags` accepts a dict with the 4 required keys + allowed values
  - Adversarial tests (Tasks 3+) added incrementally per TDD cadence

Snowkap-specific 4-tag schema (NOT a copy of Base Version's B2B vocabulary):
  - scope:        global | tenant | article | industry
  - signal_type:  analyst_judgment | model_extraction | cascade_computation
                  | regulatory_change | peer_event
  - attribution:  module name (criticality_scorer, output_verifier, ...)
                  or `manual:<analyst_email>`
  - uncertainty:  low | moderate | high | unverified

Note: pytest must run with `-s` (capture=no) to avoid the Python 3.14 +
pytest 9.x I/O capture bug. See L0 commit 074c25c + L1 validation-infra
test docstring for context.
"""

from __future__ import annotations

import pytest

from engine.audit import (
    _strict_tags_required,
    _validate_tags,
    append_decision,
    read_decision_log,
)


VALID = {
    "scope": "tenant",
    "signal_type": "analyst_judgment",
    "attribution": "discovery_promoter",
    "uncertainty": "low",
}


def test_validate_tags_accepts_valid():
    """L2 Task 1 — `_validate_tags` returns the input dict unchanged on valid input."""
    assert _validate_tags(VALID) == VALID


def test_validate_tags_rejects_missing_keys():
    """L2 Task 2 — every one of the 4 required keys must be present."""
    for missing_key in ("scope", "signal_type", "attribution", "uncertainty"):
        broken = {k: v for k, v in VALID.items() if k != missing_key}
        with pytest.raises(ValueError, match="missing required keys"):
            _validate_tags(broken)


def test_validate_tags_rejects_extra_keys():
    """L2 Task 3 — schema is closed; unknown keys raise (catches typos like `scopes`)."""
    bad = {**VALID, "scopes": "tenant"}  # plural typo
    with pytest.raises(ValueError, match="unexpected keys"):
        _validate_tags(bad)


def test_validate_tags_rejects_out_of_enum_values():
    """L2 Task 4 — every enum slot rejects values outside its allowed set.

    Pillar of the schema: typos like 'analyst_judgement' (BE spelling) or
    'medium' (wrong synonym for 'moderate') must fail loudly, not silently.
    """
    cases = [
        ("scope", "tenant_global"),                  # plausible-looking junk
        ("signal_type", "analyst_judgement"),        # BE spelling
        ("uncertainty", "medium"),                   # wrong synonym
        ("uncertainty", ""),                         # empty
    ]
    for key, val in cases:
        bad = {**VALID, key: val}
        with pytest.raises(ValueError, match=f"tags.{key}"):
            _validate_tags(bad)


def test_validate_tags_attribution_rules():
    """L2 Task 5 — attribution accepts module slugs or `manual:<value>`."""
    # Accepted forms
    for ok in ("criticality_scorer", "output_verifier", "manual:alice@snowkap.com"):
        _validate_tags({**VALID, "attribution": ok})  # no raise

    # Rejected forms
    rejects = [
        "",                          # empty
        "   ",                       # whitespace-only
        "with spaces",               # whitespace inside slug
        "manual:",                   # manual: prefix with empty body
        "manual:   ",                # manual: prefix with whitespace body
        "tenant:something",          # reserved colon-prefix collision
    ]
    for bad in rejects:
        with pytest.raises(ValueError, match="tags.attribution"):
            _validate_tags({**VALID, "attribution": bad})


def test_validate_tags_rejects_non_dict():
    """L2 Task 6 — defensive: validator must reject non-dict inputs early."""
    for bad in (None, "tags", 42, [("scope", "tenant")]):
        with pytest.raises(ValueError, match="must be dict"):
            _validate_tags(bad)  # type: ignore[arg-type]


def test_append_decision_stamps_tags_when_provided(tmp_path, monkeypatch):
    """L2 Task 7 — when callers pass tags, `append_decision` validates +
    stamps them onto the JSONL entry. Phase 26's existing callers (which
    don't pass tags) MUST stay green: see `test_append_decision_advisory_when_omitted`.
    """
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    append_decision(
        "materiality_downgrade",
        article_id="art_1",
        before="HIGH",
        after="MODERATE",
        tags=VALID,
        base_data_dir=tmp_path,
    )
    entries = list(read_decision_log(base_data_dir=tmp_path))
    assert len(entries) == 1
    assert entries[0]["tags"] == VALID


def test_append_decision_raises_on_malformed_tags_even_in_advisory_mode(tmp_path, monkeypatch):
    """L2 Task 8 — callers passing tags MUST get them right; advisory mode
    only relaxes the *requirement* to pass tags, never the *correctness*."""
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    assert _strict_tags_required() is False  # confirm advisory mode
    with pytest.raises(ValueError):
        append_decision(
            "materiality_downgrade",
            article_id="art_1",
            tags={"scope": "tenant"},  # missing 3 keys
            base_data_dir=tmp_path,
        )


def test_append_decision_advisory_when_omitted(tmp_path, monkeypatch):
    """L2 Task 9 — Phase 26 back-compat: omitting `tags` MUST NOT raise.

    Without this, every one of the 1411 existing tests would fail the
    moment L2 ships. This is THE most load-bearing test in the L2 suite.
    """
    monkeypatch.delenv("SNOWKAP_AUDIT_REQUIRE_TAGS", raising=False)
    append_decision(
        "materiality_downgrade",
        article_id="art_legacy",
        base_data_dir=tmp_path,
    )
    entries = list(read_decision_log(base_data_dir=tmp_path))
    assert len(entries) == 1
    assert "tags" not in entries[0]  # nothing stamped when nothing passed


def test_append_decision_strict_mode_requires_tags(tmp_path, monkeypatch):
    """L2 Task 10 — when `SNOWKAP_AUDIT_REQUIRE_TAGS=1`, every append MUST
    carry tags. This is the future-state enforcement that will flip on
    once the codebase is fully tagged (out-of-scope for L2)."""
    monkeypatch.setenv("SNOWKAP_AUDIT_REQUIRE_TAGS", "1")
    assert _strict_tags_required() is True
    with pytest.raises(ValueError, match="tags required"):
        append_decision(
            "materiality_downgrade",
            article_id="art_strict",
            base_data_dir=tmp_path,
        )
    # With valid tags it still works in strict mode
    append_decision(
        "materiality_downgrade",
        article_id="art_strict2",
        tags=VALID,
        base_data_dir=tmp_path,
    )
    entries = list(read_decision_log(base_data_dir=tmp_path))
    assert len(entries) == 1
    assert entries[0]["tags"] == VALID
