"""Phase 56.C — composed ESG-material vocabulary (STRUCTURE; content authored separately).

The 2nd-fetch ESG vocab is now composed per company as
``_ESG_HARM_BASE ∪ sector ∪ jurisdiction ∪ override``, keyed off
``_sasb_sector_for`` (NOT the stored ``sasb_category``, which is the literal
"Unknown" in prod). Both overlay dicts ship EMPTY on purpose — these tests prove
the STRUCTURE: base-only fallback, a LOUD/observable miss when a sector or region
is unseeded (so the silent ``.get(sector, ())`` starvation bug can't reappear),
and override-adds-to-base. The content of the two overlay dicts is authored
separately; `test_ather_sector_key_is_seeded` activates the moment it lands.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import engine.ingestion.news_fetcher as nf


def _ather():
    # Mirrors Ather's prod row: sasb_category is the literal "Unknown"; the real
    # sector is recovered via _sasb_sector_for's industry fallback -> "Automobiles".
    return SimpleNamespace(
        slug="ather-energy", industry="Automotive",
        sasb_category="Unknown", framework_region="INDIA",
        primitive_calibration={},
    )


def test_unknown_sasb_category_recovers_real_sector():
    """The key-source decision: the stored field is 'Unknown'; the helper recovers
    the real sector. _compose_esg_material MUST key off the helper, not the field."""
    assert _ather().sasb_category == "Unknown"
    assert nf._sasb_sector_for(_ather()) == "Automobiles"


def test_base_only_when_overlays_empty_and_fires_loud_miss(caplog):
    """With both overlay dicts empty, Ather composes to BASE ONLY and the miss is
    OBSERVABLE for sector=Automobiles region=INDIA — proving the miss path works
    before any seed can hide it."""
    with caplog.at_level(logging.WARNING):
        vocab = nf._compose_esg_material(_ather(), None)

    assert set(vocab) == set(nf._ESG_HARM_BASE)
    assert "penalty" in vocab and "recall" in vocab and "lawsuit" in vocab

    assert "coverage_assertion.esg_overlay_miss" in caplog.text
    assert "sector=Automobiles" in caplog.text
    assert "region=INDIA" in caplog.text


def test_override_adds_to_base_never_replaces():
    """A tenant override ADDS to the base — it must not strip penalty/fine/etc."""
    vocab = nf._compose_esg_material(_ather(), ["battery fire", "penalty"])
    for base_term in nf._ESG_HARM_BASE:
        assert base_term in vocab          # base survives
    assert "battery fire" in vocab          # override term added
    assert list(vocab).count("penalty") == 1  # override re-stating a base term de-dups


def test_overlay_dicts_ship_empty():
    """Guard the 'structure only, no content' contract — content is authored separately."""
    assert nf._SECTOR_ESG_VOCAB == {}
    assert nf._JURISDICTION_REGULATORS == {}


@pytest.mark.skipif(
    not nf._SECTOR_ESG_VOCAB,
    reason="overlay dicts ship EMPTY by design; this activates + goes green the "
           "moment 'Automobiles' is seeded, and fails loudly on any key-space drift",
)
def test_ather_sector_key_is_seeded():
    """Catches key-space drift the moment vocab lands: the key the lane actually
    looks up (`_sasb_sector_for`) must exist in `_SECTOR_ESG_VOCAB`."""
    assert nf._sasb_sector_for(_ather()) in nf._SECTOR_ESG_VOCAB
