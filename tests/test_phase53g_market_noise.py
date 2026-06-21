"""Phase 53 (G) — cap power-sector market noise so it stops crowding the deck.

The first live Power/Energy rebuild surfaced the failure: market listicles
("Five power grid stocks riding…", "Macquarie's top power picks", "Bernstein
raises target price", "turns bullish", "shares gain… losing streak") scored
HIGH and filled the critical tier, while genuine sector-ESG (a green-transition
roadmap, a 500GW renewable conference) fell to light. Two causes, both fixed:

  1. The comparison-marker set missed broker/price-target/stock-listicle speak.
  2. The actionable-event short-circuit let a listicle mis-classified as
     event_quarterly_results bypass the commentary cap entirely.

is_market_commentary now flags these (→ criticality hard-capped LOW + rec
monitor-only) while leaving genuine ESG and genuine quarterly/penalty events
untouched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.analysis.signal_classifiers import is_market_commentary, comparison_framing


def _r(title, event_id="event_default"):
    return SimpleNamespace(title=title, event=SimpleNamespace(event_id=event_id))


# The exact noise titles from the live adani-power rebuild + their (mis)events.
_NOISE = [
    ("Five power grid stocks riding India's energy transition | Stock Market News", "event_quarterly_results"),
    ("Adani Power vs NTPC? Which is a better bet? 7 factors that investors should watch", "event_quarterly_results"),
    ("Bernstein raises Adani Power target price to Rs 220, sees long-term boom", "event_analyst_outlook"),
    ("Global brokerage Jefferies turns bullish on Adani Green, Adani Power and Adani Energy", "event_analyst_outlook"),
    ("Adani Power shares gain 3%, snap two-day losing streak. Why are Jefferies bullish?", "event_analyst_outlook"),
    ("Macquarie's top power picks: NTPC leads, eyes 40% rally in Power Grid", "event_analyst_outlook"),
]

# Genuine ESG / genuine events that must NOT be capped.
_GENUINE = [
    ("Tata Power Unveils Roadmap for Mumbai's Green Energy Transition", "event_transition_announcement"),
    ("CII Green Power 2026 Spotlights India's Renewable Energy Roadmap to 500 GW", "event_transition_announcement"),
    ("TELANGANA EYES WIND POWER SURGE, BAGS NATIONAL RENEWABLE ENERGY AWARD", "event_award_recognition"),
    ("CBI conducts searches in Rs 661 crore IDFC First Bank fraud case", "event_criminal_indictment"),
    ("NTPC fined Rs 50 crore by CPCB for emission norm violation", "event_heavy_penalty"),
    # genuine quarterly result WITHOUT market framing — soft event, keeps actionability
    ("Adani Power Q4 results: net profit rises 15% on higher merchant sales", "event_quarterly_results"),
]


@pytest.mark.parametrize("title,event_id", _NOISE)
def test_market_noise_flagged(title, event_id):
    assert is_market_commentary(_r(title, event_id)) is True, title


@pytest.mark.parametrize("title,event_id", _GENUINE)
def test_genuine_not_flagged(title, event_id):
    assert is_market_commentary(_r(title, event_id)) is False, title


def test_soft_event_does_not_shield_market_framing():
    # event_quarterly_results is actionable, but a market-framed headline on it
    # is still commentary (the Phase 53.G soft-event exclusion).
    assert is_market_commentary(_r("Adani Power vs NTPC — better bet?", "event_quarterly_results")) is True
    # …while the same soft event with a plain headline is NOT commentary.
    assert is_market_commentary(_r("Adani Power Q4 net profit up 15%", "event_quarterly_results")) is False


def test_hard_event_always_protected():
    # A hard actionable event is never commentary even with market words in title.
    assert is_market_commentary(
        _r("NTPC shares fall after Rs 50 cr CPCB penalty for emissions", "event_heavy_penalty")) is False


def test_new_markers_present():
    for m in ("target price", "top picks", "turns bullish", "shares gain",
              "stock market news", "stocks riding", "brokerage"):
        assert comparison_framing(f"Something {m} something")
