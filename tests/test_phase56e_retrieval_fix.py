"""Phase 56.E — retrieval fixes that un-starve thin decks.

Two defects collapsed a company's deck to ~3 positive cards (diagnosed live on
Maruti Suzuki, which had 2,600+ source articles incl. an unfetched product
recall):

  1. The fetch title-locks on the company keyword, but ``_company_keyword``
     stripped only legal suffixes — NOT the trailing national-arm token. So a
     "<Brand> India" tenant title-locked on the full 3-token name and demanded
     all three words in the headline, collapsing the lane (~20 → 3).
  2. The strict primary lane's ESG vocab was positive-only ("ESG", "net zero",
     "BRSR"...), so a company-TITLED negative event ("<Company> Recalled",
     "<Company> fined") matched no term and was invisible — the deck skewed
     positive and genuine criticals never surfaced.

These tests pin the fixes WITHOUT hitting the network (pure name/vocab logic):
  - ``_strip_geo_suffix`` / ``_company_keyword`` strip the national-arm token,
    but never gut a 2-token name ("Coal India") or a prepositional one
    ("State Bank of India").
  - ``_company_name_variants`` exposes the geo-stripped form so the relevance
    guard accepts bare-brand headlines.
  - ``_ESG_KEYWORDS_FULL`` (strict primary vocab) is a superset of the positive
    set plus the harm base, and stays under the EventRegistry 80-word plan cap.
"""
from __future__ import annotations

import pytest

from engine.config import Company
from engine.ingestion import news_fetcher as nf


def _mk(name: str) -> Company:
    return Company(
        name=name, slug=name.lower().replace(" ", "-"), domain="x.com",
        industry="Automotive", sasb_category="Unknown", market_cap="Large Cap",
        listing_exchange="NSE", headquarter_city="", headquarter_country="India",
        headquarter_region="Asia", news_queries=[], framework_region="INDIA",
    )


# ---------------------------------------------------------------------------
# Geographic-suffix stripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Maruti Suzuki India", "Maruti Suzuki"),     # national-arm suffix → stripped
        ("Hyundai Motor India", "Hyundai Motor"),
        ("Bajaj Auto India", "Bajaj Auto"),
        ("State Bank of India", "State Bank of India"),  # prepositional → kept
        ("Bank of India", "Bank of India"),
        ("Steel Authority of India", "Steel Authority of India"),
        ("Coal India", "Coal India"),                  # 2-token, country IS brand → kept
        ("IDFC First Bank", "IDFC First Bank"),         # no geo token
        ("Adani Power", "Adani Power"),
        ("Tata Motors", "Tata Motors"),
    ],
)
def test_strip_geo_suffix(name: str, expected: str) -> None:
    assert nf._strip_geo_suffix(name) == expected


def test_company_keyword_strips_geo_after_legal_suffix() -> None:
    # legal suffix THEN national-arm token both come off the search keyword.
    assert nf._company_keyword(_mk("Maruti Suzuki India Limited")) == "Maruti Suzuki"
    # existing tenants unchanged
    assert nf._company_keyword(_mk("State Bank of India")) == "State Bank of India"
    assert nf._company_keyword(_mk("Adani Power")) == "Adani Power"


def test_name_variants_expose_geo_stripped_form() -> None:
    variants = nf._company_name_variants("Maruti Suzuki India Limited")
    assert "maruti suzuki india limited" in variants   # canonical
    assert "maruti suzuki" in variants                  # geo+legal stripped → bare brand
    # prepositional name: no spurious geo-strip
    assert nf._company_name_variants("State Bank of India") == ["state bank of india"]


# ---------------------------------------------------------------------------
# Full-spectrum strict vocab (positive ∪ harm)
# ---------------------------------------------------------------------------


def test_full_vocab_is_superset_of_positive() -> None:
    pos = set(nf._ESG_KEYWORDS)
    full = set(nf._ESG_KEYWORDS_FULL)
    assert pos.issubset(full), "full vocab must keep every positive disclosure term"


def test_full_vocab_includes_harm_terms() -> None:
    full_low = {t.lower() for t in nf._ESG_KEYWORDS_FULL}
    for harm in ("recall", "penalty", "fine", "violation", "lawsuit", "strike", "layoff"):
        assert harm in full_low, f"strict primary lane must be able to match '{harm}'"


def test_full_vocab_under_eventregistry_word_cap() -> None:
    # EventRegistry counts every WORD of a multi-word keyword toward the 80-word
    # plan cap; exceeding it makes the API reject the query and return ZERO.
    words = sum(len(term.split()) for term in nf._ESG_KEYWORDS_FULL)
    assert words <= 80, f"strict vocab is {words} keyword-words; must stay <= 80"


def test_full_vocab_deduped() -> None:
    terms = [t.lower() for t in nf._ESG_KEYWORDS_FULL]
    assert len(terms) == len(set(terms)), "no duplicate terms (human rights is in both bases)"


# ---------------------------------------------------------------------------
# Force refresh — bypass the processed-URL dedup
# ---------------------------------------------------------------------------


def test_force_refresh_bypasses_processed_dedup(monkeypatch) -> None:
    """An already-"processed" URL is normally hidden forever; force mode
    re-admits it so an orphaned/thin deck can be rebuilt from scratch."""
    from datetime import datetime, timezone

    url = "https://example.com/maruti-ignis-recall"
    body = "Maruti Suzuki has issued a recall for the Ignis. " * 30
    art = {
        "title": "Maruti Suzuki Ignis Recalled",
        "url": url, "body": body, "content": body,
        "summary": "Maruti Suzuki recalls the Ignis.",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source": "TestWire",
    }
    monkeypatch.setattr(nf, "fetch_newsapi_ai_for_company", lambda *a, **k: [dict(art)])
    monkeypatch.setattr(nf, "_load_processed", lambda: {nf._url_hash(url)})

    c = Company(
        name="Maruti Suzuki India Limited", slug="maruti-suzuki-india", domain="x.com",
        industry="Automotive", sasb_category="Unknown", market_cap="Large Cap",
        listing_exchange="NSE", headquarter_city="", headquarter_country="India",
        headquarter_region="Asia", news_queries=[], framework_region="INDIA",
        primitive_calibration={"esg_second_fetch": "off", "industry_thematic_fetch": "off"},
    )

    urls = lambda lst: [getattr(x, "url", "") for x in lst]
    # default: the processed URL stays hidden
    assert url not in urls(nf.fetch_for_company(c, persist=False, ignore_processed=False))
    # force: it is re-admitted
    assert url in urls(nf.fetch_for_company(c, persist=False, ignore_processed=True))
