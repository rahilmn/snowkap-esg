"""Phase 4 §6.3 + §6.2 — email quality verifier tests.

Validates:
  - Bullet verifier: numbers / dates / peers / action verbs pass; empty,
    rambling, hedged, tautological bullets fail.
  - Subject verifier: ≤90 chars, no provenance noise, must have ₹ or
    competitive verb.
"""
from __future__ import annotations

from engine.output.insight_verifier import (
    BulletVerdict,
    MAX_HEDGE_TOKENS,
    MAX_WORDS_PER_BULLET,
    SUBJECT_MAX_LEN,
    SubjectVerdict,
    verify_bullet,
    verify_bullets,
    verify_subject,
)


# ---------------------------------------------------------------------------
# Bullet verifier
# ---------------------------------------------------------------------------


def test_bullet_with_rupee_passes():
    v = verify_bullet("Margin compresses ₹500 Cr on Q4 imports.")
    assert v.passed
    assert v.has_number


def test_bullet_with_percent_passes():
    v = verify_bullet("Free cash flow drops 12% on coal cost spike.")
    assert v.passed
    assert v.has_number


def test_bullet_with_bps_passes():
    v = verify_bullet("Net interest margin widens 38 bps QoQ.")
    assert v.passed
    assert v.has_number


def test_bullet_with_date_passes():
    v = verify_bullet("CSRD assurance deadline FY27 looms for 5,000+ disclosures.")
    assert v.passed
    assert v.has_date


def test_bullet_with_peer_passes():
    v = verify_bullet("Tata Power's SECI auction win precedent suggests upside.")
    assert v.passed
    assert v.has_peer


def test_bullet_with_action_verb_passes():
    v = verify_bullet("SEBI sanctioned the firm under Reg 13 of LODR.")
    assert v.passed
    assert v.has_action_verb


def test_bullet_pure_prose_fails():
    """No number, no date, no peer, no action verb → fails."""
    v = verify_bullet("Sustainability has become an imperative for all stakeholders.")
    assert not v.passed
    assert any("no concrete signal" in r for r in v.reasons)


def test_bullet_too_long_fails():
    """A bullet over 35 words must fail the length gate."""
    text = " ".join([
        "This",
        "is",
        "a",
        "bullet",
        "that",
        "rambles",
        "on",
        "about",
        "₹500",
        "Cr",
        "exposure",
    ] + ["filler"] * 30)
    v = verify_bullet(text)
    assert not v.passed
    assert any("too long" in r for r in v.reasons)
    assert v.word_count > MAX_WORDS_PER_BULLET


def test_bullet_hedging_stack_fails():
    """3+ hedge tokens in one sentence triggers the hedging fail."""
    v = verify_bullet(
        "₹500 Cr may potentially possibly indicate an impact that could perhaps materialise.",
    )
    assert not v.passed
    assert any("hedging" in r for r in v.reasons)
    assert v.hedge_count > MAX_HEDGE_TOKENS


def test_bullet_one_hedge_passes_with_concrete_signal():
    """A SINGLE hedge in an otherwise concrete bullet still passes."""
    v = verify_bullet("Margin may compress ₹500 Cr if coal prices stay above $145/t.")
    assert v.passed


def test_bullet_tautology_fails():
    v = verify_bullet("Risk that the SECI contract win does not recur next year.")
    assert not v.passed
    assert any("tautology" in r for r in v.reasons)


def test_bullet_empty_fails():
    v = verify_bullet("")
    assert not v.passed
    v2 = verify_bullet("   ")
    assert not v2.passed


def test_verify_bullets_returns_one_verdict_per_input():
    out = verify_bullets([
        "₹500 Cr exposure",
        "no concrete signal here",
        "SEBI fined the firm Q4 FY26",
    ])
    assert len(out) == 3
    assert all(isinstance(v, BulletVerdict) for v in out)
    assert out[0].passed
    assert not out[1].passed
    assert out[2].passed


def test_verify_bullets_accepts_extra_peer_names():
    """Caller can extend the peer set with article-specific competitors."""
    v = verify_bullet(
        "ZeitGeist Capital wins the bid.",
        peer_names=frozenset({"zeitgeist capital"}),
    )
    assert v.passed
    assert v.has_peer


# ---------------------------------------------------------------------------
# Subject verifier
# ---------------------------------------------------------------------------


def test_subject_with_rupee_passes():
    s = "Adani Power Q4: ₹56 Cr tax-gain bump — non-recurring, watch FY27 base"
    v = verify_subject(s)
    assert v.passed
    assert v.has_rupee
    assert v.char_count <= SUBJECT_MAX_LEN


def test_subject_with_competitive_verb_passes():
    s = "Tata Power overtakes Adani Green on Khavda commissioning lead"
    v = verify_subject(s)
    assert v.passed
    assert v.has_competitive_verb


def test_subject_too_long_fails():
    plan_example = (
        "Adani Power's Q4 profit surges by ₹56.1 Cr (engine estimate) "
        "on one-time tax gain, boosting short-term P&L and investor sentiment"
    )
    v = verify_subject(plan_example)
    assert not v.passed
    # Should fail BOTH on length AND on provenance noise
    assert any("too long" in r for r in v.reasons)
    assert any("provenance noise" in r for r in v.reasons)


def test_subject_with_provenance_noise_fails_even_if_short():
    s = "Adani Q4 ₹56 Cr (engine estimate)"
    v = verify_subject(s)
    assert not v.passed
    assert any("provenance noise" in r for r in v.reasons)


def test_subject_without_rupee_or_competitive_verb_fails():
    s = "Some news about Adani Power this quarter"
    v = verify_subject(s)
    assert not v.passed
    assert any("missing concrete hook" in r for r in v.reasons)


def test_subject_empty_fails():
    v = verify_subject("")
    assert not v.passed


def test_subject_max_len_constant_is_90():
    """Locked at iPhone preview cap per plan §6.2."""
    assert SUBJECT_MAX_LEN == 90


def test_max_words_per_bullet_constant_is_35():
    """Locked at plan §6.3."""
    assert MAX_WORDS_PER_BULLET == 35
