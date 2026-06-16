"""Phase 51.D — ESG-relevance tightening.

1. A criticality FLOOR for the critical tier so market/financial noise (low
   criticality) is demoted to light instead of forced into "critical".
2. Acronym aliases are title-matched even under strict_title, so SBI-style
   acronym headlines ("SBI ...") aren't missed by the name-only keyword.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engine.analysis import deck_builder as db
from engine.ingestion import news_fetcher as nf


def _result(aid, score, band):
    return SimpleNamespace(
        article_id=aid, title=f"art {aid}", rejected=False,
        criticality={"score": score, "band": band},
        nlp=SimpleNamespace(sentiment=-0.1),
    )


def _build(candidates, floor):
    with patch.dict(os.environ, {"SNOWKAP_CRITICAL_FLOOR": str(floor)}), \
         patch("engine.analysis.article_selector.select_top_n_for_pipeline",
               side_effect=lambda c, **k: list(c)), \
         patch.object(db, "_run_stages_1_to_9", side_effect=lambda a, company: a), \
         patch.object(db, "_publish_critical", return_value="published"), \
         patch.object(db, "_publish_light", return_value="published"):
        return db.build_company_deck(
            SimpleNamespace(slug="test-co"), candidates, n_critical=3, n_total=10)


def test_critical_floor_demotes_low_criticality_to_light() -> None:
    cands = [_result("a", 0.7, "HIGH"), _result("b", 0.6, "HIGH"),
             _result("c", 0.25, "LOW"), _result("d", 0.1, "LOW"), _result("e", 0.05, "LOW")]
    s = _build(cands, floor=0.30)
    assert s.critical_published == 2          # only a, b clear the 0.30 floor
    light_ids = {i["article_id"] for i in s.published_items if i["tier"] == "light"}
    assert {"c", "d", "e"} <= light_ids        # market-noise demoted, not dropped


def test_no_floor_keeps_legacy_top3() -> None:
    cands = [_result("a", 0.7, "HIGH"), _result("b", 0.25, "LOW"),
             _result("c", 0.1, "LOW"), _result("d", 0.05, "LOW")]
    s = _build(cands, floor=0.0)
    assert s.critical_published == 3          # legacy: top-3 regardless of score


def test_acronym_aliases_title_locked_even_when_strict() -> None:
    captured = {}

    def fake_post(url, **kw):
        captured["body"] = kw.get("json")
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"articles": {"results": []}}
        return r

    company = SimpleNamespace(
        name="State Bank of India", slug="state-bank-of-india",
        industry="Financials/Banking",
        primitive_calibration={"news_aliases": ["State Bank of India", "SBI"]},
    )
    with patch.dict(os.environ, {"NEWSAPI_AI_KEY": "test-key"}), \
         patch.object(nf._SESSION, "post", side_effect=fake_post):
        nf.fetch_newsapi_ai_for_company(company, strict_title=True)

    body = json.dumps(captured.get("body") or {})
    assert "SBI" in body          # acronym alias reached the query (old code dropped it under strict)
    assert "keywordLoc" in body   # still title-locked → precision preserved
