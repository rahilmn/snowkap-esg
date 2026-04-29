"""Phase 8 tests: company onboarder + brief generator."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.ingestion.company_onboarder import (
    _build_queries,
    _infer_cap_tier,
    _infer_our_industry,
    _slugify,
    onboard_company,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert _slugify("Tata Steel") == "tata-steel"
    assert _slugify("ICICI Bank") == "icici-bank"
    assert _slugify("IDFC First Bank") == "idfc-first-bank"


def test_slugify_removes_special_chars():
    assert _slugify("Reliance Industries Ltd.") == "reliance-industries-ltd"
    assert _slugify("HDFC Bank (HDFCBANK)") == "hdfc-bank-hdfcbank"


def test_infer_industry_bank():
    assert _infer_our_industry("Banks - Regional", "Financial Services") == "Financials/Banking"
    assert _infer_our_industry("", "Banks - Diversified") == "Financials/Banking"


def test_infer_industry_renewable():
    assert _infer_our_industry("Solar", "Technology") == "Renewable Energy"
    assert _infer_our_industry("Renewable Utilities", "Utilities") == "Renewable Energy"


def test_infer_industry_power():
    assert _infer_our_industry("Utilities - Regulated Electric", "Utilities") == "Power/Energy"


def test_infer_industry_steel():
    assert _infer_our_industry("Steel", "Basic Materials") == "Steel"


def test_infer_industry_fallback():
    assert _infer_our_industry("Widgets", "") == "Widgets"
    assert _infer_our_industry("", "") == "Other"


def test_cap_tier_boundaries():
    # ₹20,000 Cr threshold = 20,000 * 1e7 INR
    assert _infer_cap_tier(200_000_000_000_000) == "Large Cap"  # 20 lakh Cr
    assert _infer_cap_tier(200_000_000_000) == "Large Cap"  # 20K Cr
    assert _infer_cap_tier(50_000_000_000) == "Mid Cap"  # 5K Cr
    assert _infer_cap_tier(10_000_000_000) == "Small Cap"  # 1K Cr


def test_queries_has_25_common_plus_industry_specific():
    q_banking = _build_queries("ICICI Bank", "Financials/Banking")
    assert len(q_banking) >= 25
    # Banking-specific suffix should appear
    assert any("fossil fuel financing" in q for q in q_banking)

    q_renewable = _build_queries("Waaree Energies", "Renewable Energy")
    assert any("Xinjiang polysilicon" in q for q in q_renewable)


def test_queries_dedup():
    """No duplicate queries in output."""
    q = _build_queries("Test Co", "Financials/Banking")
    assert len(q) == len(set(q))


# ---------------------------------------------------------------------------
# Onboarder end-to-end (mocked yfinance + fs)
# ---------------------------------------------------------------------------


def test_onboard_unresolvable_returns_none():
    """yfinance can't resolve the ticker → function returns None."""
    with patch("engine.ingestion.company_onboarder._resolve_yfinance_ticker", return_value=None):
        result = onboard_company("NonExistent Corp", ticker_hint="NOPE.NS")
    assert result is None


def test_onboard_writes_company_entry():
    """Happy path — resolves, writes to a temp companies.json, clears cache."""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / "config"
        config_dir.mkdir()
        companies_json = config_dir / "companies.json"
        companies_json.write_text(json.dumps({"companies": []}), encoding="utf-8")

        fake_info = {
            "longName": "Tata Power Co Ltd",
            "shortName": "Tata Power",
            "industry": "Utilities - Regulated Electric",
            "sector": "Utilities",
            "marketCap": 1_000_000_000_000_000,  # 1 lakh crore
            "totalRevenue": 500_000_000_000_000,
            "city": "Mumbai",
            "country": "India",
            "website": "https://www.tatapower.com/",
        }
        fake_financial = MagicMock()
        fake_financial.to_calibration_dict = lambda base: {
            **base,
            "revenue_cr": 50000,
            "opex_cr": 40000,
            "_source": "yfinance",
            "_fetched_at": "2026-04-22T00:00:00Z",
        }

        with patch("engine.ingestion.company_onboarder.CONFIG_DIR", config_dir):
            with patch(
                "engine.ingestion.company_onboarder._resolve_yfinance_ticker",
                return_value=("TATAPOWER.NS", fake_info),
            ):
                with patch(
                    "engine.ingestion.financial_fetcher.fetch_yfinance_financials",
                    return_value=fake_financial,
                ):
                    result = onboard_company("Tata Power", ticker_hint="TATAPOWER.NS")

        assert result is not None
        assert result.added_to_config is True
        assert result.slug == "tata-power"
        assert result.ticker == "TATAPOWER.NS"
        assert result.industry == "Power/Energy"
        assert result.market_cap == "Large Cap"
        assert result.queries >= 25

        # Verify written
        data = json.loads(companies_json.read_text(encoding="utf-8"))
        assert len(data["companies"]) == 1
        entry = data["companies"][0]
        assert entry["slug"] == "tata-power"
        assert entry["industry"] == "Power/Energy"
        assert entry["yfinance_ticker"] == "TATAPOWER.NS"
        assert entry["eodhd_ticker"] == "TATAPOWER.NSE"  # auto-mapped
        assert len(entry["news_queries"]) >= 25
        assert entry["primitive_calibration"]["revenue_cr"] == 50000
        assert entry["primitive_calibration"]["_source"] == "yfinance"


def test_onboard_existing_no_force_returns_existing():
    """If company exists and force=False, don't overwrite."""
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / "config"
        config_dir.mkdir()
        existing_entry = {
            "name": "Existing Co",
            "slug": "existing-co",
            "industry": "Power/Energy",
            "market_cap": "Mid Cap",
            "news_queries": ["existing query"],
            "yfinance_ticker": "EXISTING.NS",
        }
        (config_dir / "companies.json").write_text(
            json.dumps({"companies": [existing_entry]}), encoding="utf-8",
        )

        with patch("engine.ingestion.company_onboarder.CONFIG_DIR", config_dir):
            result = onboard_company("existing co", force=False)
        assert result is not None
        assert result.already_existed is True
        assert result.added_to_config is False
        assert result.queries == 1


def test_onboard_dry_run_does_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp) / "config"
        config_dir.mkdir()
        companies_json = config_dir / "companies.json"
        companies_json.write_text(json.dumps({"companies": []}), encoding="utf-8")

        fake_info = {"longName": "Test Co", "industry": "Steel", "sector": "Basic Materials",
                     "marketCap": 10_000_000_000_000, "totalRevenue": 1_000_000_000,
                     "city": "Mumbai", "country": "India", "website": "testco.com"}
        fake_fin = MagicMock()
        fake_fin.to_calibration_dict = lambda base: {**base, "revenue_cr": 5000, "_source": "yfinance"}

        with patch("engine.ingestion.company_onboarder.CONFIG_DIR", config_dir):
            with patch("engine.ingestion.company_onboarder._resolve_yfinance_ticker",
                       return_value=("TEST.NS", fake_info)):
                with patch("engine.ingestion.financial_fetcher.fetch_yfinance_financials",
                           return_value=fake_fin):
                    result = onboard_company("Test Co", dry_run=True)

        assert result is not None
        assert result.added_to_config is False  # dry-run
        # File is unchanged
        data = json.loads(companies_json.read_text(encoding="utf-8"))
        assert data["companies"] == []


# ---------------------------------------------------------------------------
# Brief generator (long + email formats)
# ---------------------------------------------------------------------------


def _make_sample_payload():
    """Realistic insight payload matching our output schema."""
    return {
        "article": {
            "id": "abc123",
            "title": "SEBI imposes ₹275 Cr penalty on Adani Power",
            "url": "https://example.com/article",
            "source": "Mint",
        },
        "insight": {
            "headline": "SEBI penalty ₹275 Cr triggers governance materiality review",
            "decision_summary": {
                "materiality": "CRITICAL",
                "action": "ACT",
                "financial_exposure": "₹275 Cr (from article) + ₹19.2 Cr (engine estimate)",
                "key_risk": "SEBI enforcement escalation",
                "top_opportunity": "Implement RPT controls",
                "timeline": "within 4 weeks",
            },
        },
        "recommendations": {
            "recommendations": [
                {
                    "title": "Implement RPT Controls",
                    "type": "compliance",
                    "priority": "CRITICAL",
                    "roi_percentage": 500.0,
                    "roi_capped": True,
                    "peer_benchmark": "Tata Power post-2019",
                }
            ]
        }
    }


def _make_sample_ceo():
    return {
        "headline": "Board action required on SEBI order",
        "board_paragraph": "SEBI has imposed ₹275 Cr penalty. The board must approve remediation plan within 4 weeks to avoid further escalation.",
        "stakeholder_map": [
            {"stakeholder": "SEBI", "stance": "Enforcement escalation within 60 days", "precedent": "Vedanta 2020"},
            {"stakeholder": "ISS", "stance": "AGAINST vote at next AGM", "precedent": "Infosys 2017"},
        ],
        "analogous_precedent": {
            "case_name": "Vedanta Konkola",
            "company": "Vedanta Resources",
            "year": "2020",
            "cost": "₹450 Cr",
            "duration": "24 months",
            "outcome": "MSCI downgrade; recovered after audit",
            "applicability": "Similar scale + governance triggers",
        },
        "three_year_trajectory": {
            "do_nothing": "FY28 +60 bps cost of capital sustained",
            "act_now": "FY27 stabilise; FY28 recover with ₹25-30 Cr remediation investment",
        },
        "qna_drafts": {
            "earnings_call": "We are challenging the order via SAT.",
            "press_statement": "Adani Power takes compliance seriously.",
            "board_qa": "Q: max exposure? A: ₹275 Cr immediate.",
            "regulator_qa": "We commit to remediation within 60 days.",
        },
    }


def _make_sample_esg():
    return {
        "headline": "Governance event triggers ESG review",
        "kpi_table": [
            {"kpi_name": "Regulatory Incidents",
             "company_value": "7",
             "unit": "count",
             "peer_quartile": "P75 (peer median 3)",
             "data_source": "BRSR FY24"},
        ],
        "confidence_bounds": [
            {"figure": "₹294 Cr total exposure", "source_type": "engine_estimate",
             "confidence": "medium", "beta_range": "0.15-0.40", "lag": "2-8 quarters",
             "functional_form": "linear"},
        ],
        "double_materiality": {
            "financial_impact": "₹275 Cr direct + ₹700 Cr market cap",
            "impact_on_world": "SDG 16.6 accountable institutions undermined",
        },
        "tcfd_scenarios": {
            "1_5c": "Regulatory intensification",
            "2c": "Gradual pressure",
            "4c": "Litigation and insurance costs compound",
        },
        "sdg_targets": [
            {"code": "16.6", "title": "Accountable institutions", "applicability": "direct"},
        ],
        "framework_citations": [
            {"code": "BRSR:P6:Q14", "rationale": "Mandatory for Large Cap",
             "region": "India", "deadline": "2026-05-30"},
        ],
    }


def _make_sample_cfo():
    return {
        "headline": "P&L exposure ₹275 Cr",
        "what_matters": [
            "₹275 Cr penalty (from article) + ₹19.2 Cr (engine estimate)",
            "49 bps margin pressure",
            "Framework BRSR:P6:Q14 triggered",
        ],
    }


def test_render_long_format_includes_all_sections():
    from scripts.generate_brief import _render_long_format
    out = _render_long_format(
        "Adani Power",
        _make_sample_payload(),
        _make_sample_esg(),
        _make_sample_ceo(),
        _make_sample_cfo(),
        _make_sample_payload()["recommendations"]["recommendations"],
    )
    # Has all three persona sections
    assert "# Adani Power ESG Brief" in out
    assert "For the CFO" in out
    assert "For the CEO" in out
    assert "For the ESG Analyst" in out
    # Has stakeholder table
    assert "Stakeholder map" in out
    assert "Vedanta" in out
    # Has Q&A
    assert "Q&A drafts" in out
    # Has recommendations table
    assert "Recommended actions" in out
    # KPIs + framework citations present
    assert "BRSR:P6:Q14" in out
    # ROI cap shown
    assert "capped" in out


def test_render_email_format_is_short():
    from scripts.generate_brief import _render_email_format
    out = _render_email_format("Adani Power", _make_sample_payload(), _make_sample_ceo())
    words = len(out.split())
    assert 30 <= words <= 300  # drip-email size
    assert "Adani Power ESG pulse" in out
    assert "Vedanta" in out


def test_render_long_handles_missing_perspectives():
    """Brief still renders even if CFO/CEO/ESG outputs are missing."""
    from scripts.generate_brief import _render_long_format
    out = _render_long_format("TestCo", _make_sample_payload(), None, None, None, [])
    assert "TestCo ESG Brief" in out
    assert "For the CFO" not in out  # absent section omitted
    assert "For the CEO" not in out
