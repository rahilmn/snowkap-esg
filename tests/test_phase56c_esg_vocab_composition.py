"""Phase 56.C — composed ESG-material vocabulary (overlays SEEDED).

The 2nd-fetch ESG vocab is composed per company as
``_ESG_HARM_BASE ∪ sector ∪ jurisdiction ∪ override``, keyed off
``_sasb_sector_for`` (NOT the stored ``sasb_category``, which is the literal
"Unknown" in prod). The sector + jurisdiction overlays are now seeded; these tests
prove: the right per-sector terms compose (EV → FAME/PM E-DRIVE/rare earth; heavy
industry → coal/effluent + NGT/CPCB), the override ADDS to the base, an UNSEEDED
sector/region still fires the loud-miss (the miss path survives seeding), and every
seeded sector key matches the real key space (catches em-dash / label drift).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import engine.ingestion.news_fetcher as nf
from engine.ingestion.llm_company_resolver import INDUSTRY_TO_SASB_DEFAULT


def _co(slug, industry, region="INDIA", sasb="Unknown"):
    return SimpleNamespace(
        slug=slug, industry=industry, sasb_category=sasb,
        framework_region=region, primitive_calibration={},
    )


def _ather():
    # Mirrors Ather's prod row: sasb_category is the literal "Unknown"; the real
    # sector is recovered via _sasb_sector_for's industry fallback -> "Automobiles".
    return _co("ather-energy", "Automotive")


# --------------------------------------------------------------------------- #
# Key source + key-space integrity
# --------------------------------------------------------------------------- #
def test_unknown_sasb_category_recovers_real_sector():
    assert _ather().sasb_category == "Unknown"
    assert nf._sasb_sector_for(_ather()) == "Automobiles"


def test_seeded_sector_keys_are_in_taxonomy():
    """Every _SECTOR_ESG_VOCAB key must be a real SASB label (a value of
    INDUSTRY_TO_SASB_DEFAULT) — catches em-dash / label drift that would make the
    lane silently miss (e.g. the "Oil & Gas — Exploration & Production" key)."""
    valid = set(INDUSTRY_TO_SASB_DEFAULT.values())
    for key in nf._SECTOR_ESG_VOCAB:
        assert key in valid, f"sector key {key!r} not a value of INDUSTRY_TO_SASB_DEFAULT"


def test_ather_sector_key_is_seeded():
    """The key the lane actually looks up (_sasb_sector_for) is seeded — was the
    skipif'd drift guard; now live since the overlay shipped content."""
    assert nf._sasb_sector_for(_ather()) in nf._SECTOR_ESG_VOCAB


# --------------------------------------------------------------------------- #
# Composition — EV (Ather) and heavy industry
# --------------------------------------------------------------------------- #
def test_ather_composes_sector_and_jurisdiction_no_miss(caplog):
    with caplog.at_level(logging.WARNING):
        vocab = nf._compose_esg_material(_ather(), None)
    assert "penalty" in vocab and "recall" in vocab                 # base survives
    assert "FAME-II" in vocab and "PM E-DRIVE" in vocab and "rare earth" in vocab  # EV overlay
    assert "NGT" in vocab and "CPCB" in vocab                       # INDIA overlay
    assert "esg_overlay_miss" not in caplog.text                    # both seeded -> no miss


def test_heavy_industry_recovers_its_terms_no_miss(caplog):
    co = _co("adani-power", "Power/Energy")  # -> "Electric Utilities & Power Generators"
    assert nf._sasb_sector_for(co) == "Electric Utilities & Power Generators"
    with caplog.at_level(logging.WARNING):
        vocab = nf._compose_esg_material(co, None)
    assert "coal" in vocab and "emission norms" in vocab            # sector overlay
    assert "NGT" in vocab and "CPCB" in vocab                       # INDIA overlay
    assert "penalty" in vocab and "show cause" in vocab             # base enforcement
    assert "esg_overlay_miss" not in caplog.text                    # parity: no miss


# --------------------------------------------------------------------------- #
# The loud-miss path must survive seeding
# --------------------------------------------------------------------------- #
def test_unseeded_sector_region_still_fires_loud_miss(caplog):
    co = _co("acme-pharma", "Pharmaceuticals", region="US")  # neither overlay seeded
    with caplog.at_level(logging.WARNING):
        vocab = nf._compose_esg_material(co, None)
    assert set(vocab) == set(nf._ESG_HARM_BASE)                     # base-only
    assert "coverage_assertion.esg_overlay_miss" in caplog.text
    assert "sector=Pharmaceuticals" in caplog.text
    assert "region=US" in caplog.text


# --------------------------------------------------------------------------- #
# Override adds to base; base excludes industrial noise
# --------------------------------------------------------------------------- #
def test_override_adds_to_base_never_replaces():
    vocab = nf._compose_esg_material(_ather(), ["tenant-custom-term", "penalty"])
    for base_term in nf._ESG_HARM_BASE:
        assert base_term in vocab                # base survives
    assert "tenant-custom-term" in vocab          # override-only term added
    assert list(vocab).count("penalty") == 1      # override re-stating a base term de-dups


def test_base_excludes_industrial_noise():
    # emissions/pollution/coal must NOT be in base (noise for non-industrial names);
    # they live in the heavy-sector overlays only.
    for noisy in ("emissions", "pollution", "coal"):
        assert noisy not in nf._ESG_HARM_BASE
