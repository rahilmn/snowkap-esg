"""Phase 56.D — deterministic theme→BRSR-principle mapping + broadened mandate.

The framework-hit feature shows, on the mobile swipe-up, which BRSR principle a
news event touches. That principle must be DETERMINISTIC — driven by an authored
``snowkap:mapsToBRSRPrinciple`` ontology edge, not the old brittle title-keyword
overlap (a "Water"/"Emissions" theme never matched the *title* "Principle 6 —
Environmental Protection"). These tests pin:

  1. ``query_brsr_principles_for_theme`` returns the authored principle per theme,
     and the Stage-2 tagger's free-text aliases ("GHG Emissions" → "Emissions")
     still resolve via the canonical alias layer.
  2. The broadened BRSR mandate — SEBI's mandate is the top-1000 listed by market
     cap (Large AND Mid Cap), so the rule reads ``cap_tier "ALL"`` and a Mid Cap
     India company correctly reads BRSR mandatory.
  3. ``framework_matcher`` wires the deterministic query for BRSR, so the BRSR
     match's ``triggered_sections`` is the principle (not empty / not a wrong
     title-overlap section).
"""
from __future__ import annotations

import pytest

from engine.nlp.theme_tagger import ESGThemeTags
from engine.analysis.framework_matcher import match_frameworks
from engine.ontology.intelligence import (
    query_brsr_principles_for_theme,
    query_mandatory_rules,
)


def _codes(theme: str) -> list[str]:
    return [code for code, _title in query_brsr_principles_for_theme(theme)]


# ---------------------------------------------------------------------------
# 1. Theme → principle mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "theme, expected",
    [
        ("Emissions", "BRSR:P6"),
        ("Climate Change", "BRSR:P6"),
        ("Water", "BRSR:P6"),
        ("Waste", "BRSR:P6"),
        ("Ethics & Compliance", "BRSR:P1"),
        ("Board Leadership", "BRSR:P1"),
        ("Community", "BRSR:P8"),
        ("Health & Safety", "BRSR:P3"),
        ("Human Capital", "BRSR:P3"),
        ("Data Privacy", "BRSR:P9"),
        ("Product Safety", "BRSR:P9"),
        ("DEI", "BRSR:P5"),
    ],
)
def test_theme_maps_to_expected_principle(theme: str, expected: str) -> None:
    codes = _codes(theme)
    assert expected in codes, f"{theme!r} should map to {expected}, got {codes}"


def test_principle_title_is_populated() -> None:
    rows = query_brsr_principles_for_theme("Emissions")
    assert rows, "Emissions must resolve to at least one principle"
    code, title = rows[0]
    assert code == "BRSR:P6"
    assert "Environmental" in title  # "Principle 6 — Environmental Protection"


def test_tagger_freetext_alias_still_resolves() -> None:
    """The Stage-2 LLM emits near-misses ("GHG Emissions" vs "Emissions"); the
    canonical alias layer must normalize them so the principle still fires."""
    assert "BRSR:P6" in _codes("GHG Emissions")


def test_unmapped_theme_returns_empty_never_fabricates() -> None:
    """A theme with no authored principle edge returns [] — the caller must not
    invent a principle."""
    assert query_brsr_principles_for_theme("Totally Unknown Theme XYZ") == []
    assert query_brsr_principles_for_theme("") == []


# ---------------------------------------------------------------------------
# 2. Broadened BRSR mandate (cap_tier "ALL")
# ---------------------------------------------------------------------------


def test_india_brsr_mandate_is_all_tier() -> None:
    rules = query_mandatory_rules("INDIA")
    brsr = [r for r in rules if r.framework_id == "BRSR"]
    assert brsr, "INDIA must have a BRSR mandatory rule"
    assert brsr[0].cap_tier == "ALL", (
        "BRSR mandate must be broadened to ALL (SEBI top-1000 spans Large+Mid "
        f"Cap); got {brsr[0].cap_tier!r}"
    )


@pytest.mark.parametrize("market_cap", ["Mid Cap", "Small Cap", "Large Cap", ""])
def test_brsr_mandatory_for_any_india_cap_tier(market_cap: str) -> None:
    tags = ESGThemeTags(
        primary_theme="Emissions", primary_pillar="E", primary_sub_metrics=[],
        secondary_themes=[], confidence=0.9, method="llm",
    )
    matches, _ = match_frameworks(
        tags, "Automobiles", "India", "Asia", market_cap, framework_region="INDIA",
    )
    brsr = [m for m in matches if m.framework_id == "BRSR"]
    assert brsr, "Emissions on an India company must collect a BRSR match"
    assert brsr[0].is_mandatory is True, (
        f"BRSR must be mandatory for India market_cap={market_cap!r}"
    )


# ---------------------------------------------------------------------------
# 3. framework_matcher wires the deterministic principle into triggered_sections
# ---------------------------------------------------------------------------


def test_brsr_triggered_sections_is_deterministic_principle() -> None:
    tags = ESGThemeTags(
        primary_theme="Emissions", primary_pillar="E", primary_sub_metrics=[],
        secondary_themes=[], confidence=0.9, method="llm",
    )
    matches, _ = match_frameworks(
        tags, "Automobiles", "India", "Asia", "Large Cap", framework_region="INDIA",
    )
    brsr = next(m for m in matches if m.framework_id == "BRSR")
    secs = brsr.triggered_sections
    assert secs, "BRSR triggered_sections must not be empty for an Emissions event"
    first = secs[0]
    assert isinstance(first, dict), "BRSR sections are {'code','title'} dicts"
    assert first.get("code") == "BRSR:P6"
