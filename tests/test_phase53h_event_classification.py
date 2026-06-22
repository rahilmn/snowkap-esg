"""Phase 53 (H) — stop theme-fallback events from inverting the deck.

The live gpt-5 audit caught the tier inversion red-handed: YES Bank's routine
ESOP share-allotment was classified event_labour_strike (via THEME FALLBACK, no
keyword match), earned actionability 0.8 + a severity floor, and outranked the
genuine ₹1,000cr Sudhir Valia loan-fraud — which itself only THEME-fell-back to
criminal_indictment because the fraud keyword set required ≥2 hits and the
headline had one ("fraud").

Two-part fix:
  1. Enrich the criminal_indictment keyword set (fraud case / loan fraud / bail /
     money mule / raids / …) so genuine fraud/governance headlines keyword-match
     and are NOT theme-fallbacks.
  2. A theme-fallback event (matched_keywords == ['[theme_fallback]']) earns
     NEITHER actionability NOR a severity floor (_scoring_event → (None, None)),
     so a routine filing can no longer outrank a real keyword-matched event.
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.nlp.event_classifier import classify_event
from engine.analysis.criticality_integration import _is_theme_fallback, _scoring_event


def _ev(event_id, kws, floor=8):
    return SimpleNamespace(event_id=event_id, matched_keywords=kws, score_floor=floor)


# --- the scoring-event neutralisation ---------------------------------------

def test_theme_fallback_yields_no_actionability_or_floor():
    fb = _ev("event_labour_strike", ["[theme_fallback]"], floor=5)
    assert _is_theme_fallback(fb) is True
    assert _scoring_event(fb) == (None, None)


def test_keyword_matched_event_keeps_id_and_severity():
    real = _ev("event_criminal_indictment", ["cbi", "scam"], floor=8)
    assert _is_theme_fallback(real) is False
    eid, sev = _scoring_event(real)
    assert eid == "event_criminal_indictment" and sev == 0.8


def test_none_event_is_neutral():
    assert _scoring_event(None) == (None, None)


# --- the keyword enrichment (genuine fraud now keyword-matches) -------------

def test_genuine_fraud_headlines_keyword_match_not_fallback():
    for title in (
        "Mumbai court rejects bail for Sudhir Valia in Rs 1,000 crore fraud case",
        "CBI files chargesheet in Rs 83-crore IDFC First Bank CREST fund scam",
        "ED raids premises in bank loan fraud and money laundering probe",
    ):
        e = classify_event(title, title, theme="Ethics & Compliance")
        assert e.event_id == "event_criminal_indictment", title
        assert e.matched_keywords != ["[theme_fallback]"], f"should keyword-match: {title}"


def test_routine_filing_is_theme_fallback_and_neutralised():
    # ESOP allotment is not a labour strike — it only theme-falls-back.
    e = classify_event(
        "YES Bank Allots Over 6.58 Lakh Shares Under Employee Stock Option Plans",
        "YES Bank Allots Over 6.58 Lakh Shares Under Employee Stock Option Plans",
        theme="Human Capital",
    )
    assert e.matched_keywords == ["[theme_fallback]"]
    assert _scoring_event(e) == (None, None)


# --- end-to-end: genuine fraud now outscores the routine filing -------------

def test_fraud_outscores_routine_filing_via_scoring_event():
    from engine.analysis import criticality_scorer as cs
    fraud_eid, fraud_sev = _scoring_event(
        classify_event("Mumbai court rejects bail for Sudhir Valia in Rs 1,000 crore fraud case",
                       "fraud case", theme="Ethics & Compliance"))
    esop_eid, esop_sev = _scoring_event(
        classify_event("YES Bank Allots Over 6.58 Lakh Shares Under Employee Stock Option Plans",
                       "esop", theme="Human Capital"))
    common = dict(relevance_total=5, cascade_total_cr=0.0, company_revenue_cr=None,
                  published_at="2026-06-18T00:00:00Z", source="Mint")
    fraud = cs.score(event_id=fraud_eid, event_severity=fraud_sev, **common)
    esop = cs.score(event_id=esop_eid, event_severity=esop_sev, **common)
    assert fraud.score > esop.score
    assert fraud.components.actionability == 0.8
    assert esop.components.actionability == 0.2
