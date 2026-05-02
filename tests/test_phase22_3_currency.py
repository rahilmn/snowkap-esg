"""Phase 22.3 — Currency-aware financial fetcher + cap-tier classification.

Pre-fix BASF's €4.84B market cap was fed into `_infer_cap_tier` as raw
INR (€-denominated), classifying it as "Small Cap" at 4840 ₹ Cr. Fix:
yfinance `financialCurrency` is read at fetch time and ALL `_cr` figures
are FX-converted to ₹ Cr before downstream consumers see them.
"""

from __future__ import annotations

from engine.ingestion.financial_fetcher import _to_inr_cr, _FX_TO_INR
from engine.ingestion.company_onboarder import _infer_cap_tier


def test_inr_passthrough():
    """₹100 Cr in INR → ₹100 Cr (no FX conversion)."""
    assert _to_inr_cr(100 * 1e7, "INR") == 100.0


def test_eur_to_inr_basf_market_cap():
    """BASF €40B → ~₹3.6L Cr at €1 = ₹90."""
    eur_market_cap = 40_000_000_000  # €40B (raw EUR units)
    cr = _to_inr_cr(eur_market_cap, "EUR")
    assert 350_000 < cr < 400_000  # ~3.6L Cr ± 5%


def test_gbp_to_inr_lloyds():
    """£100M revenue → ~₹1050 Cr at £1 = ₹105."""
    cr = _to_inr_cr(100_000_000, "GBP")
    assert 1000 < cr < 1100


def test_usd_to_inr_apple():
    """$1B → ~₹8300 Cr at $1 = ₹83."""
    cr = _to_inr_cr(1_000_000_000, "USD")
    assert 8000 < cr < 8500


def test_unknown_currency_warns_and_passes_through():
    """Unknown currency uses 1.0 multiplier (best-effort, surfaces as wrong-but-finite)."""
    cr = _to_inr_cr(100 * 1e7, "ZZZ")
    assert cr == 100.0  # fallback: 1.0 multiplier


def test_none_returns_zero():
    assert _to_inr_cr(None, "EUR") == 0.0
    assert _to_inr_cr(0, "EUR") == 0.0


def test_basf_post_conversion_market_cap_classifies_large_cap():
    """BASF €40B → ₹3.6L Cr → 'Large Cap' (≥ ₹20K Cr)."""
    cr = _to_inr_cr(40_000_000_000, "EUR")
    assert _infer_cap_tier(cr * 1e7) == "Large Cap"


def test_pre_fix_basf_would_have_misclassified():
    """Smoke — confirm the original behaviour we're fixing.

    Raw €40B passed as if INR is 4000 Cr → 'Small Cap' (the bug).
    """
    raw_eur_as_if_inr = 40_000_000_000 / 1e7  # 4000 ₹ Cr
    assert _infer_cap_tier(raw_eur_as_if_inr * 1e7) == "Small Cap"


def test_fx_table_covers_major_currencies():
    """Sanity — every common reporting currency has a rate."""
    for cur in ("INR", "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "CNY", "SGD"):
        assert cur in _FX_TO_INR
