"""Live company financial fetcher for primitive β calibration.

Replaces hardcoded primitive_calibration values in config/companies.json with
live data from yfinance (primary) or EODHD (secondary, when plan upgraded).

Phase 2 — Production Readiness Plan.

The FinancialData schema maps directly onto the fields primitive_engine.py
consumes for company-specific cascade calibration. Only the TOP-LINE
financials are fetched live:

  revenue_cr, opex_cr, capex_cr, debt_to_equity, cost_of_capital_pct

The SHARE ratios (energy_share_of_opex, labor_share_of_opex, freight_intensity,
water_intensity, commodity_exposure) remain hardcoded industry benchmarks —
financial statements don't disclose them by nature. They carry their own
`_source: "industry_benchmark"` audit flag.

Freshness: re-fetch if cached `_fetched_at` is older than 90 days. Until then,
cached values are used and the FY year stamp is preserved for audit.

Fallback chain:
  1. yfinance (free, works for Indian NSE tickers with `.NS` suffix)
  2. EODHD (scaffolded; returns None unless plan upgraded to include India)
  3. Hardcoded values from companies.json (always works; flagged `_source: "hardcoded"`)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FinancialData:
    ticker: str
    source: str  # "yfinance" | "eodhd" | "hardcoded"
    fetched_at: str  # ISO-8601 UTC
    fy_year: str  # "FY25"

    revenue_cr: float
    opex_cr: float
    capex_cr: float
    debt_to_equity: float
    cost_of_capital_pct: float

    # Derived / diagnostic
    ebitda_cr: float | None = None
    operating_margin_pct: float | None = None
    gross_margin_pct: float | None = None
    market_cap_cr: float | None = None

    def to_calibration_dict(
        self,
        base_calibration: dict,
    ) -> dict:
        """Merge live data into an existing calibration dict, preserving share ratios."""
        merged = dict(base_calibration)  # start with hardcoded (keeps share ratios)
        merged.update({
            "revenue_cr": round(self.revenue_cr, 1),
            "opex_cr": round(self.opex_cr, 1),
            "capex_cr": round(self.capex_cr, 1),
            "debt_to_equity": round(self.debt_to_equity, 3),
            "cost_of_capital_pct": round(self.cost_of_capital_pct, 2),
            "fy_year": self.fy_year,
            "_source": self.source,
            "_fetched_at": self.fetched_at,
            "_ticker": self.ticker,
        })
        if self.ebitda_cr is not None:
            merged["ebitda_cr"] = round(self.ebitda_cr, 1)
        if self.operating_margin_pct is not None:
            merged["operating_margin_pct"] = round(self.operating_margin_pct, 2)
        if self.gross_margin_pct is not None:
            merged["gross_margin_pct"] = round(self.gross_margin_pct, 2)
        if self.market_cap_cr is not None:
            merged["market_cap_cr"] = round(self.market_cap_cr, 1)
        return merged


# ---------------------------------------------------------------------------
# yfinance fetcher (primary for Indian listed)
# ---------------------------------------------------------------------------


# 1 INR Crore = 10^7 INR. yfinance reports all figures in raw INR for .NS tickers.
_INR_PER_CR = 1e7


def _cr(raw: Any) -> float:
    """Convert raw INR (or None) to Cr. Returns 0.0 for missing."""
    if raw is None:
        return 0.0
    try:
        return float(raw) / _INR_PER_CR
    except (TypeError, ValueError):
        return 0.0


def _fy_label(period: Any) -> str:
    """yfinance reports period end as 'YYYY-MM-DD'. India FY ends March 31 → FY25, FY26…"""
    try:
        d = period.date() if hasattr(period, "date") else period
        year = d.year if hasattr(d, "year") else int(str(d)[:4])
        # Indian FY ending March YYYY = FY(YY)
        return f"FY{str(year)[-2:]}"
    except Exception:  # noqa: BLE001 — defensive, any parse issue defaults
        return "unknown"


def fetch_yfinance_financials(ticker: str) -> FinancialData | None:
    """Fetch top-line financials via yfinance. Returns None on any error."""
    try:
        import yfinance as yf  # imported lazily so the module is importable w/o yf
    except ImportError:
        logger.warning("yfinance not installed — financial fetch skipped")
        return None

    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        inc = tk.income_stmt  # DataFrame; may be empty
        cf = tk.cashflow
    except Exception as exc:  # noqa: BLE001 — yf raises many things
        logger.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return None

    # Waaree-style gotcha: some Indian small/mid caps have longName=None but
    # shortName populated. Use revenue + any name field as the validity signal.
    name = info.get("longName") or info.get("shortName") or ""
    if not info or (not name and not info.get("totalRevenue")):
        logger.warning("yfinance returned no useful info for %s — ticker may be invalid", ticker)
        return None

    # Revenue — prefer income statement (audited FY) over info.totalRevenue (TTM)
    revenue_raw = None
    fy_year = "unknown"
    if inc is not None and not inc.empty:
        try:
            col = inc.columns[0]  # most recent fiscal period
            fy_year = _fy_label(col)
            if "Total Revenue" in inc.index:
                revenue_raw = inc.loc["Total Revenue", col]
        except Exception:  # noqa: BLE001
            pass
    if revenue_raw is None:
        revenue_raw = info.get("totalRevenue")

    # Opex extraction — banks and non-banks have different income statement shapes.
    # Non-bank: "Total Expenses" (COGS + SG&A + D&A) works directly.
    # Bank: no Total Expenses row. Build opex from SG&A + D&A + other operating
    # expenses. Interest Expense is deliberately EXCLUDED — it's a bank's cost
    # of funds (analogous to COGS for a manufacturer) and tracks with rates,
    # not with the energy/labor/freight cascades our primitive engine models.
    opex_raw = None
    if inc is not None and not inc.empty:
        try:
            col = inc.columns[0]
            if "Total Expenses" in inc.index:
                v = inc.loc["Total Expenses", col]
                if v is not None and float(v) > 0:
                    opex_raw = float(v)

            if opex_raw is None or opex_raw == 0:
                # Bank / alt-IS fallback — sum SG&A, D&A, provisions, other op ex.
                total = 0.0
                hits = 0
                for key in (
                    "Selling General And Administration",
                    "Depreciation Income Statement",
                    "Other Operating Expenses",
                    "Provision For Loan Lease And Other Losses",
                    "Other G And A",
                ):
                    if key in inc.index:
                        v = inc.loc[key, col]
                        if v is not None:
                            try:
                                fv = float(v)
                                if fv > 0:
                                    total += fv
                                    hits += 1
                            except (TypeError, ValueError):
                                pass
                if hits > 0:
                    opex_raw = total

            # Last resort for manufacturers: COGS + Operating Expense
            if opex_raw is None or opex_raw == 0:
                if "Cost Of Revenue" in inc.index:
                    cogs = inc.loc["Cost Of Revenue", col] or 0
                    op_ex = inc.loc["Operating Expense", col] if "Operating Expense" in inc.index else 0
                    opex_raw = float(cogs) + float(op_ex or 0)
        except Exception:  # noqa: BLE001
            pass

    # Capex — from cash flow statement (always negative in yfinance, so we abs)
    capex_raw = None
    if cf is not None and not cf.empty:
        try:
            col = cf.columns[0]
            if "Capital Expenditure" in cf.index:
                capex_raw = abs(cf.loc["Capital Expenditure", col] or 0)
        except Exception:  # noqa: BLE001
            pass

    # Debt to equity — yfinance info.debtToEquity is a percentage; convert to ratio.
    de_raw = info.get("debtToEquity") or 0
    # If value > 10, it's almost certainly a percentage (e.g., 81.25 → 0.8125)
    debt_to_equity = de_raw / 100 if de_raw > 10 else de_raw

    # Cost of capital — compute rough WACC if we have beta + market cap + debt
    # Formula: WACC = (E/V × Re) + (D/V × Rd × (1-T))
    # Re (cost of equity) ≈ risk-free + beta × equity-risk-premium
    #   India: risk-free 7% (10-yr G-sec), ERP 7% → Re = 7 + beta × 7
    # Rd ≈ 9% (typical Indian corporate borrowing), T = 25%
    beta = info.get("beta") or 1.0
    market_cap_raw = info.get("marketCap") or 0
    total_debt_raw = info.get("totalDebt") or 0
    re_pct = 7.0 + beta * 7.0
    rd_after_tax = 9.0 * (1 - 0.25)
    total_cap = market_cap_raw + total_debt_raw
    if total_cap > 0:
        e_weight = market_cap_raw / total_cap
        d_weight = total_debt_raw / total_cap
        wacc = e_weight * re_pct + d_weight * rd_after_tax
    else:
        wacc = re_pct  # fallback to cost of equity

    return FinancialData(
        ticker=ticker,
        source="yfinance",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        fy_year=fy_year,
        revenue_cr=_cr(revenue_raw),
        opex_cr=_cr(opex_raw),
        capex_cr=_cr(capex_raw),
        debt_to_equity=debt_to_equity,
        cost_of_capital_pct=wacc,
        ebitda_cr=_cr(info.get("ebitda")),
        operating_margin_pct=(info.get("operatingMargins") or 0) * 100,
        gross_margin_pct=(info.get("grossMargins") or 0) * 100,
        market_cap_cr=_cr(market_cap_raw),
    )


# ---------------------------------------------------------------------------
# EODHD fetcher (scaffolded; activate when plan upgrades to cover India)
# ---------------------------------------------------------------------------


def fetch_eodhd_financials(ticker: str) -> FinancialData | None:
    """Fetch via EODHD. Returns None if plan doesn't include the ticker's exchange.

    Expected ticker format: `<CODE>.NSE` or `<CODE>.BSE` for India, `<CODE>.US` for US.
    Currently (2026-04) the Snowkap plan covers US only — India calls return 404.
    Kept as a scaffold so a plan upgrade requires zero code changes.
    """
    import requests

    api_key = os.environ.get("EODHD_API_KEY", "")
    if not api_key:
        return None

    url = f"https://eodhistoricaldata.com/api/fundamentals/{ticker}"
    try:
        r = requests.get(url, params={"api_token": api_key, "fmt": "json"}, timeout=15)
        if r.status_code == 404:
            logger.info("EODHD: %s not in plan (404) — falling back", ticker)
            return None
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("EODHD fetch failed for %s: %s", ticker, exc)
        return None

    general = data.get("General", {})
    highlights = data.get("Highlights", {})
    valuation = data.get("Valuation", {})
    income_yr = data.get("Financials", {}).get("Income_Statement", {}).get("yearly", {})
    cf_yr = data.get("Financials", {}).get("Cash_Flow", {}).get("yearly", {})
    bs_yr = data.get("Financials", {}).get("Balance_Sheet", {}).get("yearly", {})

    if not income_yr:
        return None

    latest_yr = sorted(income_yr.keys())[-1]
    income_latest = income_yr[latest_yr]
    revenue_raw = float(income_latest.get("totalRevenue") or 0)
    opex_raw = float(income_latest.get("totalOperatingExpenses") or 0)
    # EODHD returns already-denominated (USD for US, INR for India)

    capex_raw = 0.0
    if cf_yr:
        cf_latest = cf_yr[sorted(cf_yr.keys())[-1]]
        capex_raw = abs(float(cf_latest.get("capitalExpenditures") or 0))

    debt_to_equity = 0.0
    if bs_yr:
        bs_latest = bs_yr[sorted(bs_yr.keys())[-1]]
        debt = float(bs_latest.get("shortLongTermDebtTotal") or 0)
        equity = float(bs_latest.get("totalStockholderEquity") or 1)
        debt_to_equity = debt / equity if equity else 0.0

    # Prefer AnalystRatingsAndTargetPrice / Highlights WACC if available; else compute
    beta = float(highlights.get("Beta") or 1.0)
    wacc = 7.0 + beta * 7.0  # simplified, same as yfinance path

    return FinancialData(
        ticker=ticker,
        source="eodhd",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        fy_year=f"FY{latest_yr[-2:]}",
        revenue_cr=revenue_raw / _INR_PER_CR if ticker.endswith(".NSE") or ticker.endswith(".BSE") else revenue_raw,
        opex_cr=opex_raw / _INR_PER_CR if ticker.endswith(".NSE") or ticker.endswith(".BSE") else opex_raw,
        capex_cr=capex_raw / _INR_PER_CR if ticker.endswith(".NSE") or ticker.endswith(".BSE") else capex_raw,
        debt_to_equity=debt_to_equity,
        cost_of_capital_pct=wacc,
        ebitda_cr=float(highlights.get("EBITDA") or 0) / _INR_PER_CR,
        market_cap_cr=float(highlights.get("MarketCapitalization") or 0) / _INR_PER_CR,
    )


# ---------------------------------------------------------------------------
# Orchestration — freshness + fallback chain
# ---------------------------------------------------------------------------


def _needs_refresh(calibration: dict, max_age_days: int = 90) -> bool:
    fetched_at = calibration.get("_fetched_at")
    if not fetched_at:
        return True  # no timestamp → hardcoded → re-fetch
    try:
        ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts) > timedelta(days=max_age_days)
    except (TypeError, ValueError):
        return True


def enrich_calibration(
    base_calibration: dict,
    yfinance_ticker: str | None,
    eodhd_ticker: str | None,
    force_refresh: bool = False,
    max_age_days: int = 90,
) -> dict:
    """Return an updated calibration dict using the best available live source.

    Fallback order: EODHD → yfinance → existing (hardcoded). Each attempt logs
    its outcome so the audit trail is preserved.

    If a previous fetch is still fresh (< max_age_days old) and not forced,
    returns the cached calibration unchanged.
    """
    if not force_refresh and not _needs_refresh(base_calibration, max_age_days):
        logger.debug("calibration cached; skipping fetch (source=%s fetched_at=%s)",
                     base_calibration.get("_source"), base_calibration.get("_fetched_at"))
        return base_calibration

    # Try EODHD first (more rigorous, audited data) — returns None if plan gaps
    if eodhd_ticker:
        data = fetch_eodhd_financials(eodhd_ticker)
        if data is not None and data.revenue_cr > 0:
            logger.info("calibration refreshed from EODHD: %s", eodhd_ticker)
            return data.to_calibration_dict(base_calibration)

    # Fallback to yfinance
    if yfinance_ticker:
        data = fetch_yfinance_financials(yfinance_ticker)
        if data is not None and data.revenue_cr > 0:
            logger.info("calibration refreshed from yfinance: %s", yfinance_ticker)
            return data.to_calibration_dict(base_calibration)

    # Both paths failed — mark hardcoded and stamp
    logger.info("calibration fetchers all returned None — keeping hardcoded values")
    merged = dict(base_calibration)
    merged.setdefault("_source", "hardcoded")
    merged.setdefault("_fetched_at", datetime.now(timezone.utc).isoformat())
    return merged
