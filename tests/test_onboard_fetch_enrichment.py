"""Phase 56.B — onboard fetch enrichment.

A newly-onboarded company used to fetch far less ESG news than the 7 canonical
companies, so its deck was thin and its recs read "Monitor — no action":
  * max_per_query=3 (vs the default 18) — 6x too few articles per lane;
  * the ESG-material 2nd lane + the industry-thematic (sector ESG) lane left on
    the budget-gated "auto" default;
  * only fresh[:item_limit] in raw FETCH order was analysed (company-named market
    news first → the valuable thematic/material articles crowded out).

The onboard path now forces both ESG lanes ON, inherits the full fetch depth, and
RANKS the fetched set (free Stage-4 scoring) before analysis.
"""
from __future__ import annotations

import inspect


class _FakeInfo:
    slug = "acme-inc"
    canonical_name = "Acme Inc"
    industry = "Automotive"
    sasb_category = "Automobiles"
    market_cap_tier = "mid"
    primary_ticker = None
    framework_region = "INDIA"
    headquarter_city = "Pune"
    headquarter_country = "India"
    inferred_painpoints = ["supply chain compliance"]
    inferred_kpis = ["emissions intensity"]
    default_reader_role = "esg_analyst"


class _FakeArticle:
    def __init__(self, i: int):
        self.id = f"a{i}"
        self.title = f"title {i}"
        self.content = "body"
        self.summary = "s"
        self.source = "src"
        self.url = f"http://x/{i}"
        self.published_at = "2026-06-24"
        self.metadata = {}


def test_onboard_forces_lanes_full_depth_and_ranks(monkeypatch):
    from api.routes import profile
    import engine.ingestion.llm_company_resolver as resolver
    import engine.models.companies_store as cstore
    import engine.config as cfg
    import engine.ingestion.news_fetcher as news_fetcher
    import engine.index.sqlite_index as sidx
    import api.routes.onboard_v3 as ov3
    import engine.analysis.article_selector as selector
    from engine.models import onboarding_status

    cap: dict = {}

    monkeypatch.setattr(resolver, "resolve_company_from_domain", lambda *a, **k: _FakeInfo())
    monkeypatch.setattr(
        cstore, "upsert",
        lambda **k: cap.__setitem__("upsert_cal", k.get("primitive_calibration")),
    )
    monkeypatch.setattr(cfg, "invalidate_companies_cache", lambda: None)
    monkeypatch.setattr(sidx, "register_alias", lambda *a, **k: None)
    monkeypatch.setattr(ov3, "_run_full_pipeline_for_article", lambda ad, co: {"rejected": False})
    monkeypatch.setattr(onboarding_status, "upsert", lambda *a, **k: None)

    def _fake_fetch(*args, **kwargs):
        cap["company"] = args[0] if args else kwargs.get("company")
        cap["fetch_kwargs"] = kwargs
        return [_FakeArticle(i) for i in range(5)]
    monkeypatch.setattr(news_fetcher, "fetch_for_company", _fake_fetch)

    def _fake_selector(articles, **kwargs):
        cap["selector_n"] = kwargs.get("n")
        cap["selector_slug"] = kwargs.get("company_slug")
        cap["selector_industry"] = kwargs.get("primary_industry")
        return list(articles)[: kwargs.get("n", 3)]
    monkeypatch.setattr(selector, "select_top_n_for_pipeline", _fake_selector)

    profile._run_v3_for_me_onboard(
        domain="acme.com", expected_slug="acme", item_limit=10, caller_email="p@acme.com",
    )

    # 1. Both ESG lanes forced ON — in the persisted row AND the Company dataclass
    co = cap["company"]
    assert co.primitive_calibration["esg_second_fetch"] == "on"
    assert co.primitive_calibration["industry_thematic_fetch"] == "on"
    assert cap["upsert_cal"]["esg_second_fetch"] == "on"
    assert cap["upsert_cal"]["industry_thematic_fetch"] == "on"

    # 2. Full fetch depth — the old max_per_query=3 cap is gone (inherits default 18)
    assert cap["fetch_kwargs"].get("max_per_query") != 3

    # 3. The fetched set is RANKED before analysis (not raw fetch order)
    assert cap["selector_n"] == 10
    assert cap["selector_slug"] == "acme-inc"
    assert cap["selector_industry"] == "Automotive"


def test_onboard_source_has_no_hardcoded_cap():
    """Backstop: the 3-cap must not creep back, and ranking must stay wired."""
    from api.routes import profile

    src = inspect.getsource(profile._run_v3_for_me_onboard)
    assert "max_per_query=3" not in src
    assert "select_top_n_for_pipeline" in src
    assert '"esg_second_fetch": "on"' in inspect.getsource(profile)
    assert '"industry_thematic_fetch": "on"' in inspect.getsource(profile)
