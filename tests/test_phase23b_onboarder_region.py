"""Phase 23B — country → region mapping + region-aware query flavour.

Pre-Phase 23B every onboarded company received Indian regulator queries
("BRSR filing", "SEBI penalty", …) regardless of HQ country, and the
``hq_region`` field was a binary ``Asia-Pacific | Other`` so the framework
matcher couldn't pick CSRD / SEC mandatory rules for non-Indian companies.

These tests pin the region map + the query-flavour split so a regression
that re-Indianises the onboarder fails CI rather than silently shipping.
"""

from __future__ import annotations

import pytest

from engine.ingestion.company_onboarder import (
    _REGIONAL_QUERIES,
    _UNIVERSAL_QUERIES,
    _build_queries,
    _region_for_country,
)


# -- region mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    "country,expected_region",
    [
        ("India", "INDIA"),
        ("United States", "US"),
        ("USA", "US"),
        ("United Kingdom", "UK"),
        ("Germany", "EU"),
        ("France", "EU"),
        ("Netherlands", "EU"),
        ("Singapore", "APAC"),
        ("Japan", "APAC"),
    ],
)
def test_region_for_country_known(country, expected_region):
    assert _region_for_country(country) == expected_region


@pytest.mark.parametrize("missing", [None, "", "   ", "Atlantis"])
def test_region_for_country_unknown_falls_back_to_global(missing):
    assert _region_for_country(missing) == "GLOBAL"


def test_region_for_country_strips_whitespace():
    assert _region_for_country("  Germany  ") == "EU"


# -- query flavour ---------------------------------------------------------


def test_indian_company_queries_include_brsr_and_sebi():
    qs = _build_queries("Tata Chemicals", "Chemicals", region="INDIA")
    assert any("BRSR" in q for q in qs)
    assert any("SEBI" in q for q in qs)
    # Universal terms still present
    assert any("ESG rating" in q for q in qs)


def test_us_company_queries_include_sec_and_exclude_brsr():
    qs = _build_queries("Apple Inc.", "Information Technology", region="US")
    assert any("SEC climate" in q for q in qs)
    assert any("EPA" in q for q in qs)
    # Critical: an American company should NOT get Indian regulator queries.
    assert not any("SEBI" in q for q in qs), (
        "US company should not get SEBI queries; saw: "
        f"{[q for q in qs if 'SEBI' in q]}"
    )
    assert not any("BRSR" in q for q in qs), (
        "US company should not get BRSR filing queries"
    )


def test_eu_company_queries_include_csrd_and_esrs():
    qs = _build_queries("Siemens AG", "Power/Energy", region="EU")
    assert any("CSRD" in q for q in qs)
    assert any("ESRS" in q for q in qs)
    assert not any("SEBI" in q for q in qs)


def test_uk_company_queries_include_fca_and_modern_slavery():
    qs = _build_queries("Barclays plc", "Financials/Banking", region="UK")
    assert any("FCA" in q for q in qs)
    assert any("Modern Slavery" in q for q in qs)
    assert not any("SEBI" in q for q in qs)


def test_unknown_region_falls_back_to_global_flavour():
    qs = _build_queries("Some Co", "Other", region="ATLANTIS")
    # GLOBAL flavour pulls a broad mix of CSRD + SEC + CDP
    assert any("CSRD" in q for q in qs)
    assert any("CDP" in q for q in qs)


def test_universal_queries_present_in_every_region():
    """The labour / climate / biodiversity universal terms must appear no
    matter the region — regression guard against accidentally folding
    them into the regional bucket and dropping them for new regions."""
    needles = ["forced labour", "child labour", "biodiversity", "climate disclosure"]
    for region in ("INDIA", "US", "EU", "UK", "APAC", "GLOBAL"):
        qs = _build_queries("Acme", "Other", region=region)
        for needle in needles:
            assert any(needle in q for q in qs), (
                f"region={region} missing universal term {needle!r}"
            )


def test_back_compat_alias_common_queries_still_importable():
    """Some older modules import ``_COMMON_QUERIES`` directly. Keep the
    alias in place so they don't break."""
    from engine.ingestion.company_onboarder import _COMMON_QUERIES

    assert isinstance(_COMMON_QUERIES, list)
    assert any("BRSR" in q for q in _COMMON_QUERIES)


# -- structural sanity ---------------------------------------------------------


def test_region_buckets_non_empty():
    for region, qs in _REGIONAL_QUERIES.items():
        assert qs, f"region {region!r} has empty query list"


def test_universal_template_uses_company_placeholder():
    for q in _UNIVERSAL_QUERIES:
        assert "{company}" in q, f"universal query missing placeholder: {q}"
