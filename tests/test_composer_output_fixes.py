"""Three output-bug fixes found on live adani-power decks (2026-06-23):

  1. Perplexity 400 — response_format=json_object stripped for perplexity/* (it
     only accepts text/json_schema/regex); the forecaster parser now digs the
     JSON out of fenced/prose text.
  2. Empty-body lede leak — the model's "the body is empty" refusal must never
     be stored as a lede.
  3. Composer truncation — garbled "~₹180." / "exposed to a modeled." are caught
     so they don't ship (or get rejected by the approval gate).
"""
from __future__ import annotations

import json

from engine.llm.client import _normalize_params_for_model
from engine.analysis.forecaster import _extract_json_object, _parse_horizons
from engine.analysis.lede_writer import _looks_like_refusal
from engine.analysis.unified_analysis import _looks_garbled


# --------------------------------------------------------------------------- #
# Bug 1 — Perplexity response_format + robust parsing
# --------------------------------------------------------------------------- #
def test_perplexity_response_format_stripped():
    out = _normalize_params_for_model(
        {"model": "perplexity/sonar-pro", "response_format": {"type": "json_object"}, "max_tokens": 100})
    assert "response_format" not in out
    # non-Perplexity models keep their response_format untouched
    keep = _normalize_params_for_model(
        {"model": "openai/gpt-4.1", "response_format": {"type": "json_object"}})
    assert keep.get("response_format") == {"type": "json_object"}


def test_extract_json_from_fenced_or_prose():
    assert json.loads(_extract_json_object('```json\n{"horizons": {"3m": {}}}\n```')) == {"horizons": {"3m": {}}}
    prose = 'Here is the forecast:\n{"horizons": {"3m": {"direction": "up"}}} — done.'
    assert json.loads(_extract_json_object(prose))["horizons"]["3m"]["direction"] == "up"


def test_parse_horizons_handles_fenced_response():
    raw = '```json\n{"horizons": {' \
          '"3m": {"direction": "improving", "confidence": "high"}, ' \
          '"6m": {"direction": "stable", "confidence": "moderate"}, ' \
          '"12m": {"direction": "declining", "confidence": "low"}}}\n```'
    parsed = _parse_horizons(raw)
    assert parsed is not None and set(parsed) == {"3m", "6m", "12m"}


# --------------------------------------------------------------------------- #
# Bug 2 — model refusal never stored as a lede
# --------------------------------------------------------------------------- #
def test_refusal_lede_detected():
    refusal = ("The article body excerpt is empty. With no facts to ground a lede, "
               "fabricating context would violate the hard grounding rules. A "
               "deterministic fallback is the correct output here: Tripura ...")
    assert _looks_like_refusal(refusal) is True
    assert _looks_like_refusal(
        "Adani Power targets 42 GW by FY32 on 95% PPA coverage and 40% EBITDA margin.") is False


# --------------------------------------------------------------------------- #
# Bug 3 — garbled truncation caught; complete prose is not
# --------------------------------------------------------------------------- #
def test_garbled_truncation_caught():
    assert _looks_garbled("Your company faces ~₹180.")                 # figure cut, no unit
    assert _looks_garbled("Your company is exposed to a modeled.")     # hanging adjective
    assert _looks_garbled("Your company is exposed to a sector-wide.")
    assert _looks_garbled("...opens a window to issue a.")             # existing case still works


def test_complete_text_not_flagged_garbled():
    assert not _looks_garbled("IDFC First Bank faces a ~₹180 Cr modeled exposure under SEBI LODR.")
    assert not _looks_garbled("The bank disclosed a ₹503 crore penalty this quarter.")
    assert not _looks_garbled("Adani Power targets 42 GW by FY32 on 95% PPA coverage.")
