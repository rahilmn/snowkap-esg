"""Phase 23A — Google News locale per HQ country.

Verifies that the locale helper returns the right ``(hl, gl, ceid)`` tuple
for the countries supported by the launch + falls back to English-US for
unknown / missing countries (the deliberate departure from the previous
India-only default that blocked any non-Indian onboarding).
"""

from __future__ import annotations

import pytest

from engine.ingestion.news_fetcher import (
    GOOGLE_NEWS_URL,
    _GOOGLE_NEWS_DEFAULT_LOCALE,
    _locale_for_country,
)


@pytest.mark.parametrize(
    "country,expected",
    [
        ("India", ("en-IN", "IN", "IN:en")),
        ("United States", ("en-US", "US", "US:en")),
        ("United Kingdom", ("en-GB", "GB", "GB:en")),
        ("Germany", ("de", "DE", "DE:de")),
        ("France", ("fr", "FR", "FR:fr")),
        ("Singapore", ("en-SG", "SG", "SG:en")),
    ],
)
def test_known_country_returns_locale(country, expected):
    assert _locale_for_country(country) == expected


@pytest.mark.parametrize("missing", [None, "", "   ", "Atlantis", "Westeros"])
def test_unknown_or_missing_falls_back_to_us_english(missing):
    assert _locale_for_country(missing) == _GOOGLE_NEWS_DEFAULT_LOCALE
    assert _GOOGLE_NEWS_DEFAULT_LOCALE == ("en", "US", "US:en")


def test_country_is_whitespace_stripped():
    assert _locale_for_country("  Germany  ") == ("de", "DE", "DE:de")


def test_url_template_has_locale_placeholders():
    assert "{hl}" in GOOGLE_NEWS_URL
    assert "{gl}" in GOOGLE_NEWS_URL
    assert "{ceid}" in GOOGLE_NEWS_URL
    # Sanity: the old hardcoded India locale is gone.
    assert "hl=en-IN" not in GOOGLE_NEWS_URL
    assert "gl=IN" not in GOOGLE_NEWS_URL


def test_fetch_google_news_signature_accepts_country():
    """Regression: ``fetch_google_news`` must accept a ``country`` kwarg so
    ``fetch_for_company`` can pass HQ country through without a TypeError."""
    import inspect

    from engine.ingestion.news_fetcher import fetch_google_news

    sig = inspect.signature(fetch_google_news)
    assert "country" in sig.parameters
    assert sig.parameters["country"].default is None
