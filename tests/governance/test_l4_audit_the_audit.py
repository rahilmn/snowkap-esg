"""L4 — Toulmin audit-the-audit gate.

A meta-verifier that scans recent audit entries and asserts the
discipline contract holds:
  - Every analyst_judgment entry carries a complete Toulmin block
  - Every Toulmin block has non-empty claim, grounds, warrant
  - `manual:<email>` attribution ↔ automated=False (one implies the other)
  - High-uncertainty entries must carry a non-empty `qualifier` so the
    rebuttal path is explicit (catches "we know we don't know" cases)

This is the smallest-pure-addition layer in v2 (1d). It locks the L2 tag
schema down so L3-L7 can rely on it.
"""
from __future__ import annotations

import pytest

from engine.audit import (
    append_decision,
    audit_the_audit,
    make_toulmin,
)


VALID_TAGS = {
    "scope": "tenant",
    "signal_type": "analyst_judgment",
    "attribution": "criticality_scorer",
    "uncertainty": "low",
}


def _good_toulmin():
    return make_toulmin(
        claim="Material risk identified",
        grounds=["article line 14", "primitive cascade EP→OX"],
        warrant="cascade β > 0.2 over 3 hops",
        qualifier="assuming no offsetting hedge",
        rebuttal="hedge in place would invert this",
    )


def test_audit_the_audit_passes_on_clean_log(tmp_path):
    """L4 — happy path: a single fully-formed entry passes audit."""
    append_decision(
        "materiality_downgrade",
        article_id="art_clean",
        toulmin=_good_toulmin(),
        tags=VALID_TAGS,
        automated=True,
        base_data_dir=tmp_path,
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True
    assert report["violations"] == []
    assert report["scanned"] == 1


def test_audit_the_audit_flags_analyst_judgment_without_toulmin(tmp_path):
    """L4 — analyst_judgment MUST carry a Toulmin block."""
    append_decision(
        "materiality_downgrade",
        article_id="art_bad",
        tags=VALID_TAGS,  # signal_type=analyst_judgment
        base_data_dir=tmp_path,
        # NB: no toulmin=
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is False
    assert any(v["rule"] == "analyst_judgment_requires_toulmin" for v in report["violations"])


def test_audit_the_audit_flags_incomplete_toulmin(tmp_path):
    """L4 — Toulmin must have non-empty claim, grounds, warrant."""
    append_decision(
        "materiality_downgrade",
        article_id="art_partial",
        toulmin={"claim": "x", "grounds": [], "warrant": ""},  # empty
        tags=VALID_TAGS,
        base_data_dir=tmp_path,
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is False
    rules = {v["rule"] for v in report["violations"]}
    assert "toulmin_missing_grounds" in rules
    assert "toulmin_missing_warrant" in rules


def test_audit_the_audit_flags_manual_attribution_with_automated_true(tmp_path):
    """L4 — `manual:<email>` attribution implies automated=False.

    Catches the failure mode where a human-emitted entry is mistakenly
    flagged as engine-emitted (or vice-versa).
    """
    append_decision(
        "materiality_downgrade",
        article_id="art_mislabel",
        toulmin=_good_toulmin(),
        tags={**VALID_TAGS, "attribution": "manual:alice@snowkap.com"},
        automated=True,  # contradicts manual:
        base_data_dir=tmp_path,
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is False
    assert any(v["rule"] == "attribution_automation_mismatch" for v in report["violations"])


def test_audit_the_audit_flags_module_attribution_with_automated_false(tmp_path):
    """L4 — module-slug attribution implies automated=True (inverse case)."""
    append_decision(
        "materiality_downgrade",
        article_id="art_mislabel2",
        toulmin=_good_toulmin(),
        tags={**VALID_TAGS, "attribution": "criticality_scorer"},
        automated=False,  # contradicts module slug
        base_data_dir=tmp_path,
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is False
    assert any(v["rule"] == "attribution_automation_mismatch" for v in report["violations"])


def test_audit_the_audit_high_uncertainty_requires_qualifier(tmp_path):
    """L4 — high/unverified entries must carry a Toulmin qualifier.

    The whole point of high-uncertainty audit is to encode "we know we
    don't know X" — a qualifier-less high-uncertainty entry is a bug.
    """
    append_decision(
        "materiality_downgrade",
        article_id="art_unqualified",
        toulmin={"claim": "x", "grounds": ["y"], "warrant": "z"},  # no qualifier
        tags={**VALID_TAGS, "uncertainty": "high"},
        base_data_dir=tmp_path,
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is False
    assert any(v["rule"] == "high_uncertainty_requires_qualifier" for v in report["violations"])


def test_audit_the_audit_window_respects_limit(tmp_path):
    """L4 — only the last N entries scanned (default 100)."""
    for i in range(5):
        append_decision(
            "materiality_downgrade",
            article_id=f"art_{i}",
            toulmin=_good_toulmin(),
            tags=VALID_TAGS,
            base_data_dir=tmp_path,
        )
    report = audit_the_audit(base_data_dir=tmp_path, window=3)
    assert report["scanned"] == 3
    assert report["pass"] is True


def test_audit_the_audit_skips_untagged_legacy_entries(tmp_path):
    """L4 — untagged legacy entries (no tags) are SKIPPED, not flagged.

    Phase 26's 1411 existing tests + 6 production callers don't pass
    tags. L4 must not break them — it only audits what's actually been
    tagged via L2.
    """
    append_decision(
        "materiality_downgrade",
        article_id="art_legacy",
        base_data_dir=tmp_path,
        # no tags, no toulmin
    )
    report = audit_the_audit(base_data_dir=tmp_path)
    assert report["pass"] is True
    assert report["scanned"] == 0           # nothing audited (no tags to audit)
    assert report["skipped_untagged"] == 1  # one legacy entry skipped, NOT a violation
