"""Phase 23 reviewer fix — explicit framework_region wins over country/region heuristic.

Pre-fix a UK bank routed via the "europ" substring match in
`_region_key("United Kingdom", "Europe")` got tagged as EU and
inherited CSRD / ESRS mandatory rules it shouldn't. Fix: optional
`framework_region` parameter explicitly sets the jurisdiction key,
falling back to the legacy heuristic when None.
"""

from __future__ import annotations

from engine.analysis.framework_matcher import _region_key, _VALID_FRAMEWORK_REGIONS


def test_explicit_framework_region_wins_over_country():
    """A UK bank with explicit framework_region='UK' must NOT be EU."""
    assert _region_key("United Kingdom", "Europe", "UK") == "UK"


def test_explicit_us_overrides_eu_label():
    """Override path: a US-listed European subsidiary tagged for SEC reporting."""
    assert _region_key("Germany", "Europe", "US") == "US"


def test_invalid_framework_region_falls_back_to_heuristic():
    """Unknown framework_region values fall back to the country/region heuristic."""
    assert _region_key("India", "Asia-Pacific", "MARS") == "INDIA"


def test_none_framework_region_uses_heuristic_uk_now_separate():
    """Without explicit override, UK is now a distinct bucket (post-fix)."""
    assert _region_key("United Kingdom", "Europe", None) == "UK"


def test_none_framework_region_eu_still_routes_to_eu():
    """Germany with no framework_region still routes to EU via heuristic."""
    assert _region_key("Germany", "Europe", None) == "EU"


def test_valid_region_set_complete():
    """Sanity — the validation set covers every documented jurisdiction."""
    assert _VALID_FRAMEWORK_REGIONS == {"INDIA", "EU", "UK", "US", "APAC", "GLOBAL"}
