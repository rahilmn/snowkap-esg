"""Phase 53 (B) — the INDUSTRY / THEMATIC ESG fetch lane.

Most companies have no company-NAMED ESG event in a window; their material ESG
news is sector/regulatory/thematic (RBI climate norms, financed-emissions, a
sector controversy) where the company is NOT named. This lane fetches that on
the company's industry + SASB material topics, with a TITLE-locked sector+ESG
query (precision) and no company-identity clause.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import engine.ingestion.news_fetcher as nf


def _company(industry="Financials/Banking", sasb="Commercial Banks", region="INDIA", cal=None):
    return SimpleNamespace(
        slug="test-co", name="Test Co", industry=industry, sasb_category=sasb,
        framework_region=region, primitive_calibration=cal or {},
    )


def test_sasb_sector_falls_back_on_unknown():
    # SBI carries literal "Unknown" in the DB — must fall back to the industry map.
    assert nf._sasb_sector_for(_company(sasb="Unknown")) == "Commercial Banks"
    assert nf._sasb_sector_for(_company(sasb="")) == "Commercial Banks"
    assert nf._sasb_sector_for(_company(sasb="Commercial Banks")) == "Commercial Banks"


def test_build_thematic_terms_bank():
    sector, esg = nf._build_thematic_terms(_company())
    assert "RBI" in sector and "banks" in sector
    # ESG terms come from the bank's top SASB material topics (climate/data_privacy/...)
    assert any("climate" in t for t in esg)
    assert any("data" in t for t in esg)
    # lean enough for EventRegistry's 80-word plan limit
    assert len(sector) <= 6 and len(esg) <= 14


def test_thematic_query_title_locked_tagged_and_region_scoped():
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["body"] = json
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"articles": {"results": [{
            "url": "https://x.test/rbi-climate", "title": "RBI tightens climate norms for banks",
            "body": "The RBI issued new climate-risk disclosure norms...", "dateTime": "2026-06-18T00:00:00Z",
            "source": {"title": "Mint"},
        }]}}
        return r

    with patch.dict("os.environ", {"NEWSAPI_AI_KEY": "k"}), \
         patch.object(nf._SESSION, "post", side_effect=_fake_post):
        arts = nf.fetch_industry_thematic_for_company(_company(), max_results=8)

    # tagged as the thematic lane
    assert arts and arts[0]["source_type"] == "industry_thematic"
    # query: title-locked sector $or AND title-locked ESG $or AND lang AND India location
    qand = captured["body"]["query"]["$query"]["$and"]
    sector_clause = qand[0]["$or"]
    esg_clause = qand[1]["$or"]
    assert all(k.get("keywordLoc") == "title" for k in sector_clause), "sector must be title-locked"
    assert all(k.get("keywordLoc") == "title" for k in esg_clause), "ESG must be title-locked"
    assert any(c.get("sourceLocationUri", "").endswith("/India") for c in qand), "India region scope"
    # NO company-identity clause (the whole point — company not named)
    flat = str(captured["body"])
    assert "keywordLoc" in flat and "Test Co" not in flat


def test_thematic_off_switch():
    assert nf._thematic_fetch_enabled(_company(cal={"industry_thematic_fetch": "off"})) is False
    assert nf._thematic_fetch_enabled(_company(cal={"industry_thematic_fetch": "on"})) is True
