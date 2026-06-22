"""Phase 53 (E) — the deck fills its 3 critical slots with genuinely-material
articles, not macro filler.

The deck builder already fills-to-3 by a band-dominated composite rank
(``_rank_composite`` = band×10 + negativity + score, ``critical_floor`` off).
The Phase 53 chain makes that promote the RIGHT articles:

  * Phase 53.B feeds the candidate pool with sector-ESG articles (company not
    named) so genuinely-material candidates EXIST for every company.
  * Phase 53.C scores them CRITICAL/HIGH and caps market-commentary noise at
    LOW, so the band-dominated rank places the material articles on top.

These tests pin the ranking invariant: a thematic-critical always outranks
market noise, so the 3 critical slots fill with ESG-material articles and noise
falls to the light tier — without forcing macro signals into "critical".
"""
from __future__ import annotations

from types import SimpleNamespace

from engine.analysis.deck_builder import _rank_composite


def _candidate(band, score, sentiment=-1):
    return SimpleNamespace(
        criticality={"band": band, "score": score},
        nlp=SimpleNamespace(sentiment=sentiment),
    )


def test_thematic_critical_outranks_market_noise():
    thematic_critical = _candidate("CRITICAL", 0.78)       # bank-climate (Phase 53.C)
    macro_low = _candidate("LOW", 0.34, sentiment=0)       # "X vs Y better bet" listicle
    assert _rank_composite(thematic_critical) > _rank_composite(macro_low)


def test_band_dominates_score():
    # A HIGH-band thematic article outranks a LOW-band article even if the LOW
    # one has a numerically higher raw score — band is the first-order key.
    high_band_low_score = _candidate("HIGH", 0.10)
    low_band_high_score = _candidate("LOW", 0.99, sentiment=0)
    assert _rank_composite(high_band_low_score) > _rank_composite(low_band_high_score)


def test_fill_to_three_orders_material_first():
    # A realistic pool: 1 company-named critical + 2 thematic + a tail of macro
    # noise. After ranking, the top 3 are the material ones; macro noise sorts
    # to the bottom (→ light tier), so no macro filler reaches "critical".
    pool = [
        _candidate("LOW", 0.30, sentiment=0),         # macro: stock comparison
        _candidate("CRITICAL", 0.80),                 # company-named fraud
        _candidate("LOW", 0.25, sentiment=0),         # macro: analyst target
        _candidate("HIGH", 0.66),                     # thematic: RBI climate norms
        _candidate("CRITICAL", 0.77),                 # thematic: financed emissions
    ]
    ranked = sorted(pool, key=_rank_composite, reverse=True)
    top3_bands = [c.criticality["band"] for c in ranked[:3]]
    assert top3_bands == ["CRITICAL", "CRITICAL", "HIGH"]
    # the macro-LOW noise is last → demoted to the light tier
    assert all(c.criticality["band"] == "LOW" for c in ranked[3:])
