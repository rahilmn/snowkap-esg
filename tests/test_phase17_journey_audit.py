"""Phase 17 — User Journey Audit regression tests.

Each test corresponds to a row in `docs/USER_JOURNEY_AUDIT.md` so a
breakage immediately points at the documented audit issue.
"""

from __future__ import annotations

import pytest

from engine.analysis.recommendation_archetypes import (
    get_archetypes_for_event,
    get_archetypes_for_theme,
    is_ambiguous_event,
    is_positive_event,
)


# ---------------------------------------------------------------------------
# Issue A — Sentiment-aware polarity for ambiguous events
# ---------------------------------------------------------------------------


class TestSentimentAwarePolarity:
    """Quarterly results / dividend / M&A / rating / climate disclosure
    should route by sentiment, not default to negative."""

    def test_quarterly_results_with_positive_sentiment_is_positive(self):
        # IDFC First Bank Q4 calendar live-fail: profit +48% YoY, sentiment +1
        assert is_positive_event("event_quarterly_results", sentiment=1) is True

    def test_quarterly_results_with_negative_sentiment_is_negative(self):
        # Infosys Q4 weak earnings: sentiment -1
        assert is_positive_event("event_quarterly_results", sentiment=-1) is False

    def test_quarterly_results_with_neutral_sentiment_is_negative_default(self):
        # Conservative default — neutral earnings preview stays on the
        # legacy defensive path until sentiment is +1 or -1.
        assert is_positive_event("event_quarterly_results", sentiment=0) is False

    def test_dividend_policy_with_positive_sentiment_is_positive(self):
        assert is_positive_event("event_dividend_policy", sentiment=1) is True

    def test_ma_deal_with_positive_sentiment_is_positive(self):
        assert is_positive_event("event_ma_deal", sentiment=2) is True

    def test_esg_rating_change_with_positive_sentiment_is_positive(self):
        # Infosys MSCI A→AA upgrade has sentiment +1
        assert is_positive_event("event_esg_rating_change", sentiment=1) is True

    def test_esg_rating_change_with_negative_sentiment_is_negative(self):
        # Wells Fargo BBB→B downgrade has sentiment -1
        assert is_positive_event("event_esg_rating_change", sentiment=-1) is False

    def test_static_positive_event_always_positive_regardless_of_sentiment(self):
        # Contract win is unambiguously positive; sentiment shouldn't matter
        assert is_positive_event("event_contract_win", sentiment=0) is True
        assert is_positive_event("event_contract_win", sentiment=-1) is True
        assert is_positive_event("event_contract_win", sentiment=None) is True

    def test_unknown_event_id_returns_false(self):
        assert is_positive_event("event_made_up", sentiment=1) is False
        assert is_positive_event("", sentiment=1) is False
        assert is_positive_event(None, sentiment=1) is False  # type: ignore[arg-type]

    def test_back_compat_signature_without_sentiment(self):
        # Single-arg signature still works for callers that haven't been updated yet
        assert is_positive_event("event_contract_win") is True
        assert is_positive_event("event_quarterly_results") is False  # ambiguous → default false

    def test_is_ambiguous_event_membership(self):
        assert is_ambiguous_event("event_quarterly_results") is True
        assert is_ambiguous_event("event_dividend_policy") is True
        assert is_ambiguous_event("event_ma_deal") is True
        assert is_ambiguous_event("event_esg_rating_change") is True
        assert is_ambiguous_event("event_climate_disclosure_index") is True

    def test_static_positive_events_are_not_ambiguous(self):
        assert is_ambiguous_event("event_contract_win") is False
        assert is_ambiguous_event("event_capacity_addition") is False
        assert is_ambiguous_event("event_esg_certification") is False


# ---------------------------------------------------------------------------
# Issue H — Theme-driven archetype fallback when event_id is empty
# ---------------------------------------------------------------------------


class TestThemeFallbackArchetypes:
    """When event classification fails, archetypes fall back to themes
    so we don't ship the generic 5-rec disclosure template."""

    def test_event_archetype_lookup_returns_empty_for_empty_event(self):
        assert get_archetypes_for_event("") == []

    def test_theme_fallback_for_climate_change(self):
        archs = get_archetypes_for_theme("Climate Change")
        assert len(archs) > 0
        # Collapse label + description for keyword check (transition archetypes
        # include "Investor-day update — transition capex + abatement curve")
        full_text = " ".join(f"{label} {desc}" for label, desc in archs).lower()
        assert any(
            kw in full_text
            for kw in ("transition", "scope", "renewable", "capex", "sbti", "cdp")
        )

    def test_theme_fallback_for_supply_chain(self):
        archs = get_archetypes_for_theme("Supply Chain")
        assert len(archs) > 0

    def test_theme_fallback_case_insensitive(self):
        upper = get_archetypes_for_theme("CLIMATE CHANGE")
        lower = get_archetypes_for_theme("climate change")
        mixed = get_archetypes_for_theme("Climate Change")
        assert upper == lower == mixed
        assert len(upper) > 0

    def test_unknown_theme_returns_empty(self):
        assert get_archetypes_for_theme("nonsense theme") == []
        assert get_archetypes_for_theme("") == []

    def test_governance_themes_route_to_governance_archetypes(self):
        archs = get_archetypes_for_theme("Board & Leadership")
        assert len(archs) > 0


# ---------------------------------------------------------------------------
# Issue C — Calendar-announcement / earnings-preview filter
# ---------------------------------------------------------------------------


class TestCalendarPreviewFilter:
    """Forward-looking preview articles must be filtered before reaching
    the LLM. Live-fail: IDFC First Bank Q4 NDTV Profit (2026-04-24)."""

    def test_idfc_q4_calendar_live_fail_is_filtered(self):
        from engine.ingestion.news_fetcher import _is_calendar_preview

        title = "IDFC First Bank Q4 Results: Date, Time, Dividend News, Earnings Call Details And More"
        body = (
            "IDFC First Bank Ltd. is set to declare the financial results for the fourth quarter "
            "and the nine months of FY26 that ended on March 31. The company confirmed the schedule "
            "in a filing with the stock exchanges on April 22. In an exchange filing dated April 22, "
            "IDFC First Bank Ltd. said that a meeting of its Board of Directors is scheduled to be "
            "held on Saturday, April 25, to consider and approve the audited consolidated and "
            "standalone financial results of the Company for the quarter and the financial year "
            "ended on March 31, 2026."
        )
        assert _is_calendar_preview(title, body) is True

    def test_real_earnings_release_is_not_filtered(self):
        # An actual results-release article ≠ a calendar preview.
        # Title doesn't have "Date, Time, Dividend...And More" and body doesn't
        # have scheduling language.
        from engine.ingestion.news_fetcher import _is_calendar_preview

        title = "IDFC First Bank Q4 Profit Surges 40% On Lower Provisions"
        body = (
            "IDFC First Bank reported a 40% jump in net profit to Rs 700 crore in the fourth quarter, "
            "as provisions fell 15% year-on-year. The bank's Net Interest Income grew 14% to Rs 5,800 "
            "crore. Gross NPAs improved to 1.5%. The bank declared a dividend of Re 1 per share."
        )
        assert _is_calendar_preview(title, body) is False

    def test_unrelated_article_not_filtered(self):
        from engine.ingestion.news_fetcher import _is_calendar_preview

        title = "ICICI Bank Launches New Digital Banking Platform for SMEs"
        body = "ICICI Bank announced the launch of a new digital banking platform..."
        assert _is_calendar_preview(title, body) is False

    def test_weak_signal_with_calendar_phrase_only_in_body_is_not_filtered(self):
        # If the title isn't a calendar preview but the body happens to mention
        # "earnings call scheduled", we don't want a false positive.
        from engine.ingestion.news_fetcher import _is_calendar_preview

        title = "ICICI Bank Posts Record Q4 Results"
        body = "Profit jumped 40%. Earnings call scheduled for tomorrow." * 5
        assert _is_calendar_preview(title, body) is False

    def test_calendar_title_alone_is_not_enough(self):
        # Title alone doesn't qualify — body must have scheduling language.
        # This prevents an actual results-day article with a calendar-style
        # title from being dropped.
        from engine.ingestion.news_fetcher import _is_calendar_preview

        title = "Reliance Q4 Results: Date, Time, And Earnings Call Details"
        body = "Reliance reported revenue of Rs 250,000 crore..." * 10  # zero scheduling phrases
        assert _is_calendar_preview(title, body) is False


# ---------------------------------------------------------------------------
# Issue B — On-demand pipeline uses Phase 4 dedicated generators
# ---------------------------------------------------------------------------


class TestOnDemandPhase4Generators:
    """The on-demand path (triggered by user click) must use the same
    Phase 4 dedicated ESG Analyst + CEO generators as the ingest path —
    not the legacy `transform_for_perspective` for all three lenses."""

    def test_on_demand_imports_dedicated_generators(self):
        # Static check: the source file must import the Phase 4 generators
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "engine" / "analysis" / "on_demand.py"
        text = src.read_text(encoding="utf-8")
        assert "from engine.analysis.esg_analyst_generator import generate_esg_analyst_perspective" in text
        assert "from engine.analysis.ceo_narrative_generator import generate_ceo_narrative_perspective" in text

    def test_on_demand_calls_dedicated_generators_for_esg_analyst_and_ceo(self):
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "engine" / "analysis" / "on_demand.py"
        text = src.read_text(encoding="utf-8")
        # The on-demand function should call the dedicated generators (not just
        # the legacy transform) for ESG Analyst + CEO.
        assert "generate_esg_analyst_perspective(insight, result, company)" in text
        assert "generate_ceo_narrative_perspective(insight, result, company)" in text


# ---------------------------------------------------------------------------
# Issue E — Onboarded company gets industry-aware primitive_calibration
# ---------------------------------------------------------------------------


class TestOnboardedCompanyCalibration:
    """An onboarded company must get industry-aware `primitive_calibration`
    defaults so the cascade engine produces meaningful ₹ figures even for
    industries outside the original 7 target companies."""

    def test_industry_defaults_table_covers_target_industries(self):
        from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS

        # All 7 target-company industries must be present so onboarding a
        # peer in the same industry gets the same calibration shape.
        for ind in (
            "Financials/Banking", "Asset Management", "Power/Energy",
            "Renewable Energy",
        ):
            assert ind in _INDUSTRY_CALIBRATION_DEFAULTS

    def test_industry_defaults_have_required_keys(self):
        from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS

        required_keys = {
            "energy_share", "labor_share", "freight_intensity",
            "water_intensity", "commodity_exposure", "key_exposure",
        }
        for industry, calib in _INDUSTRY_CALIBRATION_DEFAULTS.items():
            assert required_keys.issubset(calib.keys()), \
                f"{industry} missing keys: {required_keys - set(calib.keys())}"

    def test_power_industry_has_high_energy_share(self):
        # Power/Energy companies have ~40% energy share of opex
        from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS
        assert _INDUSTRY_CALIBRATION_DEFAULTS["Power/Energy"]["energy_share"] >= 0.30

    def test_banking_industry_has_low_energy_share_high_labor(self):
        from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS
        bank = _INDUSTRY_CALIBRATION_DEFAULTS["Financials/Banking"]
        assert bank["energy_share"] < 0.05
        assert bank["labor_share"] >= 0.25

    def test_fallback_present_for_unknown_industry(self):
        from engine.ingestion.company_onboarder import _INDUSTRY_CALIBRATION_DEFAULTS
        assert "__fallback__" in _INDUSTRY_CALIBRATION_DEFAULTS
        # Fallback must be conservative (not industry-dominating)
        fb = _INDUSTRY_CALIBRATION_DEFAULTS["__fallback__"]
        assert 0.0 < fb["energy_share"] <= 0.20
        assert 0.0 < fb["labor_share"] <= 0.40


# ---------------------------------------------------------------------------
# Cross-cutting — verifier still fires coherence check on ambiguous events
# ---------------------------------------------------------------------------


class TestVerifierAmbiguousEventCoherence:
    """Phase 12.4 narrative-coherence check must fire on ambiguous events
    when sentiment + insight polarity diverge — pre-Phase 17 the check
    only fired for static positive/negative events."""

    def test_quarterly_results_positive_sentiment_negative_insight_downgrades(self):
        from engine.analysis.output_verifier import verify_narrative_coherence

        # Simulated IDFC live-fail: event_quarterly_results, sentiment +1,
        # but the LLM emitted CRITICAL materiality + heavy key_risk
        # (defensive framing on a positive earnings preview).
        deep_insight = {
            "decision_summary": {
                "materiality": "CRITICAL",
                "key_risk": "190.5 bps margin compression and ₹500 Cr exposure on regulatory front",
                "top_opportunity": "",
            }
        }
        out, report = verify_narrative_coherence(
            deep_insight, event_id="event_quarterly_results", nlp_sentiment=1
        )
        # Should detect the polarity mismatch and downgrade materiality
        assert out["decision_summary"]["materiality"] == "HIGH"
        assert any("coherence mismatch" in c.lower() for c in report.corrections)

    def test_quarterly_results_negative_sentiment_negative_insight_no_action(self):
        from engine.analysis.output_verifier import verify_narrative_coherence

        # Negative quarterly results + negative insight = consistent. No downgrade.
        deep_insight = {
            "decision_summary": {
                "materiality": "HIGH",
                "key_risk": "Profit dropped 30%, NPAs widened",
                "top_opportunity": "",
            }
        }
        out, report = verify_narrative_coherence(
            deep_insight, event_id="event_quarterly_results", nlp_sentiment=-1
        )
        assert out["decision_summary"]["materiality"] == "HIGH"  # unchanged
