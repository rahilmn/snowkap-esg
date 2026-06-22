"""Phase 52 — ESG-aware second fetch (body-matched ESG-material query).

The cron's primary NewsAPI.ai query title-locks on the company name, so
market-dominated names (power/renewable) get only stock/growth coverage and 0
critical ESG events. A gated SECOND query drops the title-lock and uses a
curated ESG-MATERIAL/harm vocabulary so substantive ESG/negative stories that
mention the company in the BODY enter the feed. Mocks `_SESSION.post` — no real
HTTP fires.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import engine.ingestion.news_fetcher as nf
from engine.ingestion.news_fetcher import _ESG_KEYWORDS_MATERIAL

_RECENT = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def _company(slug="adani-power", name="Adani Power", industry="Power/Energy", calib=None):
    return SimpleNamespace(
        slug=slug, name=name, industry=industry,
        primitive_calibration=calib or {},
    )


def _resp(results):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"articles": {"results": results}}
    return r


def _article(i, title, body):
    return {
        "url": f"https://ex.com/esg/{i}", "title": title, "body": body,
        "source": {"title": "Stub Source"}, "dateTime": _RECENT,
        "image": "", "concepts": [], "sentiment": 0.0,
    }


# ---------------------------------------------------------------------------
# 1. Query shape — the override reaches the request, body-matched, ESG-material
# ---------------------------------------------------------------------------


class TestSecondQueryShape:
    def test_material_vocab_body_matched(self):
        captured = {}

        def fake_post(url, **kw):
            captured["body"] = kw.get("json")
            return _resp([])

        with patch.dict(os.environ, {"NEWSAPI_AI_KEY": "k"}), \
             patch.object(nf._SESSION, "post", side_effect=fake_post):
            nf.fetch_newsapi_ai_for_company(
                _company(), strict_title=False, esg_keywords=_ESG_KEYWORDS_MATERIAL,
            )
        body = json.dumps(captured["body"])
        assert "emission norms" in body and "penalty" in body   # material vocab reached
        assert "sustainability" not in body                      # NOT the generic ESG net
        assert "keywordLoc" not in body                          # body-matched, no title-lock

    def test_strict_primary_unchanged(self):
        captured = {}

        def fake_post(url, **kw):
            captured["body"] = kw.get("json")
            return _resp([])

        with patch.dict(os.environ, {"NEWSAPI_AI_KEY": "k"}), \
             patch.object(nf._SESSION, "post", side_effect=fake_post):
            nf.fetch_newsapi_ai_for_company(_company(), strict_title=True)
        body = json.dumps(captured["body"])
        assert "keywordLoc" in body        # still title-locked
        assert "sustainability" in body    # strict generic ESG vocab unchanged


# ---------------------------------------------------------------------------
# 2. Gating — off / on / auto budget-floor
# ---------------------------------------------------------------------------


class TestGating:
    def test_off(self):
        assert nf._esg_second_fetch_enabled(_company(calib={"esg_second_fetch": "off"})) is False

    def test_on(self):
        assert nf._esg_second_fetch_enabled(_company(calib={"esg_second_fetch": "on"})) is True

    def test_auto_budget_floor(self, monkeypatch):
        from engine.ingestion.news_router import reset_router
        reset_router()  # fresh budget → remaining == monthly_cap
        monkeypatch.setenv("SNOWKAP_ESG_FETCH_MIN_REMAINING", "999999999")
        assert nf._esg_second_fetch_enabled(_company()) is False  # below floor
        monkeypatch.setenv("SNOWKAP_ESG_FETCH_MIN_REMAINING", "0")
        assert nf._esg_second_fetch_enabled(_company()) is True   # ample headroom
        reset_router()


# ---------------------------------------------------------------------------
# 3. fetch_for_company — 2 POSTs, merge + URL dedup, budget, body-mention guard
# ---------------------------------------------------------------------------


class TestFetchForCompany:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        from engine.ingestion.news_router import reset_router
        reset_router()
        monkeypatch.setenv("NEWSAPI_AI_KEY", "k")
        monkeypatch.setenv("SNOWKAP_ESG_FETCH_MIN_REMAINING", "0")  # auto-gate passes
        # Isolate the ESG-second-fetch lane under test from the Phase 53 thematic
        # lane (a separate 3rd fetch) — push its auto budget-gate out of reach so
        # only the primary + ESG-second queries fire and the POST counts hold.
        monkeypatch.setenv("SNOWKAP_THEMATIC_FETCH_MIN_REMAINING", "99999999")
        monkeypatch.setattr(nf, "_load_processed", lambda: set())
        monkeypatch.setattr(nf, "_save_processed", lambda *a, **k: None, raising=False)
        yield
        reset_router()

    def test_two_posts_merge_dedup_and_budget(self):
        from engine.ingestion.news_router import get_router
        n = "Adani Power"
        calls = []

        def fake_post(url, **kw):
            calls.append(kw.get("json"))
            if len(calls) == 1:  # primary
                return _resp([
                    _article("a", f"{n} ESG climate disclosure update", f"{n} reports transition progress."),
                    _article("b", f"{n} shares rise on rising demand", f"{n} stock gains in trade."),
                ])
            return _resp([  # secondary — ESG-material
                _article("b", f"{n} shares rise on rising demand", f"{n} stock gains in trade."),  # dup url b
                _article("c", f"{n} fined for emission norms violation", f"NGT penalty on {n}'s Mundra coal plant."),
            ])

        before = get_router().budget.spent_this_month  # restored-from-DB baseline
        with patch.object(nf._SESSION, "post", side_effect=fake_post):
            out = nf.fetch_for_company(_company(), max_per_query=10, persist=False)

        assert len(calls) == 2                       # both queries fired
        titles = " | ".join(a.title for a in out)
        assert "emission norms violation" in titles  # the ESG-material story surfaced
        assert len(out) == 3                          # a, b, c — dup url b collapsed once
        # each fetch records its own spend = articles RETURNED (2 + 2), pre-dedup
        assert get_router().budget.spent_this_month - before == 4

    def test_body_mention_survives_sibling_dropped(self):
        n = "Adani Power"
        calls = []

        def fake_post(url, **kw):
            calls.append(1)
            if len(calls) == 1:
                return _resp([])  # primary: market-only, nothing passes
            return _resp([
                _article("body", "New emission norms hit thermal power plants",
                         f"The tribunal flagged several coal plants including {n}'s Mundra facility for an emission violation."),
                _article("sibling", "Adani Green commissions new solar park",
                         "Adani Green Energy added 300 MW of solar capacity in Rajasthan."),
            ])

        with patch.object(nf._SESSION, "post", side_effect=fake_post):
            out = nf.fetch_for_company(_company(), max_per_query=10, persist=False)
        titles = " | ".join(a.title for a in out)
        assert "emission norms" in titles      # body-mention of the company survived
        assert "Adani Green" not in titles      # sibling-company story dropped by the relevance guard

    def test_gate_off_fires_single_query(self):
        calls = []

        def fake_post(url, **kw):
            calls.append(1)
            return _resp([_article("a", "Adani Power ESG update", "Adani Power net zero plan.")])

        with patch.object(nf._SESSION, "post", side_effect=fake_post):
            nf.fetch_for_company(
                _company(calib={"esg_second_fetch": "off"}), max_per_query=10, persist=False,
            )
        assert len(calls) == 1  # only the primary; 2nd query suppressed — primary path unchanged
