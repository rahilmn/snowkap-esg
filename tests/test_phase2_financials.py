"""Phase 2 financial fetcher tests: freshness, fallback chain, merge semantics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from engine.ingestion.financial_fetcher import (
    FinancialData,
    _needs_refresh,
    enrich_calibration,
)


# ---------------------------------------------------------------------------
# FinancialData.to_calibration_dict
# ---------------------------------------------------------------------------


def _sample_data(source: str = "yfinance") -> FinancialData:
    return FinancialData(
        ticker="ADANIPOWER.NS",
        source=source,
        fetched_at="2026-04-22T10:00:00+00:00",
        fy_year="FY25",
        revenue_cr=56132.0,
        opex_cr=38377.0,
        capex_cr=11559.0,
        debt_to_equity=0.86,
        cost_of_capital_pct=11.2,
        ebitda_cr=20037.0,
        operating_margin_pct=24.9,
        gross_margin_pct=43.2,
        market_cap_cr=415874.0,
    )


def test_merge_preserves_share_ratios():
    """Share ratios (energy, labor, etc.) must NOT be overwritten by live data."""
    base = {
        "revenue_cr": 45000,
        "opex_cr": 35000,
        "capex_cr": 8000,
        "energy_share_of_opex": 0.40,
        "labor_share_of_opex": 0.08,
        "freight_intensity": 0.06,
        "water_intensity": 0.03,
        "commodity_exposure": {"coal": 0.60, "natural_gas": 0.05},
        "debt_to_equity": 2.5,
        "cost_of_capital_pct": 10.5,
        "fy_year": "FY25",
        "_source": "hardcoded",
    }
    data = _sample_data()
    merged = data.to_calibration_dict(base)

    # Live fields replaced
    assert merged["revenue_cr"] == 56132.0
    assert merged["opex_cr"] == 38377.0
    assert merged["capex_cr"] == 11559.0
    assert merged["debt_to_equity"] == 0.86
    assert merged["cost_of_capital_pct"] == 11.2
    assert merged["_source"] == "yfinance"
    assert merged["_fetched_at"] == "2026-04-22T10:00:00+00:00"
    assert merged["_ticker"] == "ADANIPOWER.NS"

    # Share ratios preserved
    assert merged["energy_share_of_opex"] == 0.40
    assert merged["labor_share_of_opex"] == 0.08
    assert merged["freight_intensity"] == 0.06
    assert merged["water_intensity"] == 0.03
    assert merged["commodity_exposure"] == {"coal": 0.60, "natural_gas": 0.05}


def test_merge_adds_diagnostic_fields():
    base = {"revenue_cr": 1000, "opex_cr": 800}
    data = _sample_data()
    merged = data.to_calibration_dict(base)
    assert merged["ebitda_cr"] == 20037.0
    assert merged["operating_margin_pct"] == 24.9
    assert merged["gross_margin_pct"] == 43.2
    assert merged["market_cap_cr"] == 415874.0


# ---------------------------------------------------------------------------
# Freshness check
# ---------------------------------------------------------------------------


def test_needs_refresh_missing_timestamp():
    assert _needs_refresh({}) is True
    assert _needs_refresh({"_fetched_at": None}) is True
    assert _needs_refresh({"_fetched_at": ""}) is True


def test_needs_refresh_stale():
    old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    assert _needs_refresh({"_fetched_at": old_ts}, max_age_days=90) is True


def test_needs_refresh_fresh():
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert _needs_refresh({"_fetched_at": recent_ts}, max_age_days=90) is False


def test_needs_refresh_unparseable_timestamp():
    assert _needs_refresh({"_fetched_at": "garbage"}) is True


# ---------------------------------------------------------------------------
# enrich_calibration orchestration
# ---------------------------------------------------------------------------


def test_enrich_uses_yfinance_when_eodhd_fails():
    base = {"revenue_cr": 45000, "opex_cr": 35000, "_source": "hardcoded"}

    with patch("engine.ingestion.financial_fetcher.fetch_eodhd_financials", return_value=None):
        with patch(
            "engine.ingestion.financial_fetcher.fetch_yfinance_financials",
            return_value=_sample_data("yfinance"),
        ):
            merged = enrich_calibration(
                base,
                yfinance_ticker="ADANIPOWER.NS",
                eodhd_ticker="ADANIPOWER.NSE",
                force_refresh=True,
            )

    assert merged["_source"] == "yfinance"
    assert merged["revenue_cr"] == 56132.0


def test_enrich_prefers_eodhd_when_both_work():
    base = {"revenue_cr": 45000, "opex_cr": 35000, "_source": "hardcoded"}

    eodhd_data = FinancialData(
        ticker="ADANIPOWER.NSE",
        source="eodhd",
        fetched_at="2026-04-22T10:00:00+00:00",
        fy_year="FY25",
        revenue_cr=56000.0,
        opex_cr=38000.0,
        capex_cr=11500.0,
        debt_to_equity=0.85,
        cost_of_capital_pct=11.0,
    )

    with patch(
        "engine.ingestion.financial_fetcher.fetch_eodhd_financials",
        return_value=eodhd_data,
    ):
        with patch(
            "engine.ingestion.financial_fetcher.fetch_yfinance_financials",
            return_value=_sample_data("yfinance"),
        ):
            merged = enrich_calibration(
                base,
                yfinance_ticker="ADANIPOWER.NS",
                eodhd_ticker="ADANIPOWER.NSE",
                force_refresh=True,
            )

    assert merged["_source"] == "eodhd"


def test_enrich_falls_back_to_hardcoded_when_all_fail():
    base = {
        "revenue_cr": 45000,
        "opex_cr": 35000,
        "_source": "hardcoded",
    }

    with patch("engine.ingestion.financial_fetcher.fetch_eodhd_financials", return_value=None):
        with patch(
            "engine.ingestion.financial_fetcher.fetch_yfinance_financials",
            return_value=None,
        ):
            merged = enrich_calibration(
                base,
                yfinance_ticker="ADANIPOWER.NS",
                eodhd_ticker="ADANIPOWER.NSE",
                force_refresh=True,
            )

    assert merged["_source"] == "hardcoded"
    # Values unchanged
    assert merged["revenue_cr"] == 45000


def test_enrich_skips_fetch_when_cache_fresh():
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    base = {
        "revenue_cr": 45000,
        "opex_cr": 35000,
        "_source": "yfinance",
        "_fetched_at": recent_ts,
    }

    with patch(
        "engine.ingestion.financial_fetcher.fetch_eodhd_financials"
    ) as mock_eodhd, patch(
        "engine.ingestion.financial_fetcher.fetch_yfinance_financials"
    ) as mock_yf:
        merged = enrich_calibration(
            base,
            yfinance_ticker="ADANIPOWER.NS",
            eodhd_ticker="ADANIPOWER.NSE",
            force_refresh=False,
        )

    # No fetcher called — cache is fresh
    mock_eodhd.assert_not_called()
    mock_yf.assert_not_called()
    assert merged is base  # same object returned


def test_enrich_handles_none_tickers():
    """Singularity AMC — unlisted, both tickers None, must not crash."""
    base = {"revenue_cr": 200, "opex_cr": 150, "_source": "hardcoded"}

    merged = enrich_calibration(
        base,
        yfinance_ticker=None,
        eodhd_ticker=None,
        force_refresh=True,
    )

    assert merged["_source"] == "hardcoded"
    assert merged["revenue_cr"] == 200


def test_enrich_rejects_zero_revenue_response():
    """A fetcher returning FinancialData with revenue=0 must not be used."""
    base = {"revenue_cr": 45000, "_source": "hardcoded"}
    bad_data = FinancialData(
        ticker="X", source="yfinance",
        fetched_at="2026-04-22T10:00:00+00:00", fy_year="FY25",
        revenue_cr=0.0, opex_cr=0.0, capex_cr=0.0,
        debt_to_equity=0.0, cost_of_capital_pct=0.0,
    )

    with patch("engine.ingestion.financial_fetcher.fetch_eodhd_financials", return_value=None):
        with patch(
            "engine.ingestion.financial_fetcher.fetch_yfinance_financials",
            return_value=bad_data,
        ):
            merged = enrich_calibration(
                base,
                yfinance_ticker="X.NS",
                eodhd_ticker=None,
                force_refresh=True,
            )

    # Should have fallen through to hardcoded
    assert merged["_source"] == "hardcoded"
    assert merged["revenue_cr"] == 45000
