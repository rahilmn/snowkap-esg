"""Phase 25 W9 — personal stakes generator + perspective dedup tests.

Two layers:

  A. ``engine.analysis.personal_stakes_generator`` — deterministic
     revenue-pct computation + LLM call (mocked).
  B. ``engine.analysis.perspective_dedup`` — n-gram overlap detector +
     regen-instruction generator.

The LLM call is mocked across all W9.A tests so we don't burn OpenAI
quota during CI. Real-LLM end-to-end smoke is deferred to the
manual QA walkthrough in Section 7.9 of the plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fake Company stand-in (avoids importing the heavy engine.config module)
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompany:
    name: str = "Test Co"
    slug: str = "test-co"
    industry: str = "Power/Energy"
    framework_region: str = "INDIA"
    primitive_calibration: dict | None = None

    @property
    def revenue_cr(self) -> float:
        return float((self.primitive_calibration or {}).get("revenue_cr", 0))


# ---------------------------------------------------------------------------
# A. personal_stakes_generator — deterministic revenue %
# ---------------------------------------------------------------------------


class TestComputeRevenuePctAtStake:
    def test_basic_percentage_computation(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={"revenue_cr": 50000})
        parsed = {
            "decision_summary": {"financial_exposure": "₹500 Cr (engine estimate)"}
        }
        # 500 / 50000 × 100 = 1.0%
        assert _compute_revenue_pct_at_stake(parsed, company) == 1.0

    def test_lakh_unit_normalised(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={"revenue_cr": 100})  # 100 Cr
        parsed = {
            "decision_summary": {"financial_exposure": "₹500 Lakh (from article)"}
        }
        # 500 Lakh = 5 Cr; 5 / 100 × 100 = 5.0%
        assert _compute_revenue_pct_at_stake(parsed, company) == 5.0

    def test_returns_none_when_revenue_missing(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={})
        parsed = {"decision_summary": {"financial_exposure": "₹500 Cr"}}
        assert _compute_revenue_pct_at_stake(parsed, company) is None

    def test_returns_none_when_exposure_missing(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={"revenue_cr": 50000})
        parsed = {"decision_summary": {"financial_exposure": ""}}
        assert _compute_revenue_pct_at_stake(parsed, company) is None

    def test_returns_none_when_exposure_has_no_rupee_figure(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={"revenue_cr": 50000})
        parsed = {"decision_summary": {"financial_exposure": "significant exposure"}}
        assert _compute_revenue_pct_at_stake(parsed, company) is None

    def test_handles_comma_separated_amount(self):
        from engine.analysis.personal_stakes_generator import _compute_revenue_pct_at_stake
        company = _FakeCompany(primitive_calibration={"revenue_cr": 50000})
        parsed = {"decision_summary": {"financial_exposure": "₹2,500 Cr (engine estimate)"}}
        # 2500 / 50000 × 100 = 5.0%
        assert _compute_revenue_pct_at_stake(parsed, company) == 5.0


# ---------------------------------------------------------------------------
# A2. personal_stakes_generator — LLM call (mocked)
# ---------------------------------------------------------------------------


class TestGeneratePersonalStakes:
    @pytest.fixture
    def mock_openai_response(self):
        """Mock OpenAI to return a valid JSON response."""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = (
            '{"personal_stakes_paragraph": "Your company derives 35% of revenue from coal generation; '
            'this RBI Climate Stress Test consultation affects 18% of that segment, estimated ₹2,000-3,000 Cr '
            'CAGR provisioning impact over 3 years.", '
            '"peer_action_summary": "Tata Power filed BRSR P6 disclosure 90 days early.", '
            '"do_nothing_risk_paragraph": "Inaction risks ESG fund divestment and MSCI downgrade in 6 months."}'
        )
        return mock_resp

    def test_full_block_returned_on_successful_llm_call(self, mock_openai_response):
        from engine.analysis import personal_stakes_generator as gen
        company = _FakeCompany(primitive_calibration={"revenue_cr": 45000})
        parsed = {
            "decision_summary": {
                "financial_exposure": "₹2,500 Cr (engine estimate)",
                "verdict": "ACT",
            },
            "headline": "RBI Climate Stress Test consultation",
            "core_mechanism": "Regulatory cascade",
        }
        with patch("openai.OpenAI") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_openai_response
            mock_client_cls.return_value = mock_client
            with patch("engine.config.get_openai_api_key", return_value="sk-test"):
                result = gen.generate_personal_stakes(
                    parsed,
                    company=company,
                    event_polarity="negative",
                )
        assert "personal_stakes_paragraph" in result
        assert len(result["personal_stakes_paragraph"]) > 50
        assert "peer_action_summary" in result
        assert "do_nothing_risk_paragraph" in result
        # Deterministic field — computed not LLM'd
        assert result["revenue_pct_at_stake"] is not None
        assert abs(result["revenue_pct_at_stake"] - 5.56) < 0.01  # 2500/45000

    def test_empty_dict_on_llm_failure(self):
        from engine.analysis import personal_stakes_generator as gen
        company = _FakeCompany(primitive_calibration={"revenue_cr": 45000})
        parsed = {"decision_summary": {"financial_exposure": "₹500 Cr"}}
        with patch("openai.OpenAI") as mock_client_cls:
            mock_client_cls.side_effect = Exception("simulated LLM outage")
            with patch("engine.config.get_openai_api_key", return_value="sk-test"):
                result = gen.generate_personal_stakes(
                    parsed,
                    company=company,
                    event_polarity="negative",
                )
        # Fail-safe: empty dict, never raises
        assert result == {}

    def test_polarity_routes_to_correct_directive(self):
        """Both polarity branches must execute without crashing."""
        from engine.analysis import personal_stakes_generator as gen
        company = _FakeCompany(primitive_calibration={"revenue_cr": 1000})
        parsed = {"decision_summary": {"financial_exposure": "₹50 Cr"}}
        for polarity in ("positive", "negative", "neutral"):
            with patch("openai.OpenAI") as mock_client_cls:
                mock_resp = MagicMock()
                mock_resp.choices[0].message.content = '{"personal_stakes_paragraph": "x", "peer_action_summary": "y", "do_nothing_risk_paragraph": "z"}'
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = mock_resp
                mock_client_cls.return_value = mock_client
                with patch("engine.config.get_openai_api_key", return_value="sk-test"):
                    result = gen.generate_personal_stakes(
                        parsed, company=company, event_polarity=polarity,
                    )
                assert "personal_stakes_paragraph" in result

    def test_skips_call_on_dict_response_format_failure(self):
        """If the LLM returns a non-dict (e.g. a list, a number),
        return empty dict rather than crashing on key access."""
        from engine.analysis import personal_stakes_generator as gen
        company = _FakeCompany(primitive_calibration={"revenue_cr": 1000})
        parsed = {"decision_summary": {"financial_exposure": "₹50 Cr"}}
        with patch("openai.OpenAI") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = '["not", "a", "dict"]'
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_resp
            mock_client_cls.return_value = mock_client
            with patch("engine.config.get_openai_api_key", return_value="sk-test"):
                result = gen.generate_personal_stakes(
                    parsed, company=company, event_polarity="negative",
                )
        assert result == {}


# ---------------------------------------------------------------------------
# A3. DeepInsight dataclass — new field
# ---------------------------------------------------------------------------


class TestDeepInsightStakesField:
    def test_dataclass_has_stakes_for_company_field(self):
        from engine.analysis.insight_generator import DeepInsight
        di = DeepInsight(
            headline="x",
            impact_score=5.0,
            core_mechanism="x",
            profitability_connection="x",
            translation="x",
        )
        assert di.stakes_for_company == {}
        # Mutable default works
        di.stakes_for_company = {"personal_stakes_paragraph": "Y"}
        assert di.stakes_for_company["personal_stakes_paragraph"] == "Y"

    def test_to_dict_serialises_stakes_field(self):
        from engine.analysis.insight_generator import DeepInsight
        di = DeepInsight(
            headline="x", impact_score=5.0, core_mechanism="x",
            profitability_connection="x", translation="x",
            stakes_for_company={"revenue_pct_at_stake": 6.3},
        )
        d = di.to_dict()
        assert "stakes_for_company" in d
        assert d["stakes_for_company"]["revenue_pct_at_stake"] == 6.3


# ---------------------------------------------------------------------------
# B. perspective_dedup — n-gram overlap
# ---------------------------------------------------------------------------


class TestComputeOverlap:
    def test_identical_texts_score_one(self):
        from engine.analysis.perspective_dedup import compute_overlap
        text = "regulatory penalty cascade affects credit rating significantly"
        assert compute_overlap(text, text) == 1.0

    def test_disjoint_texts_score_zero(self):
        from engine.analysis.perspective_dedup import compute_overlap
        a = "supplier audits children labour ethics framework"
        b = "earnings call quarterly results dividend policy"
        assert compute_overlap(a, b) == 0.0

    def test_partial_overlap_intermediate_score(self):
        from engine.analysis.perspective_dedup import compute_overlap
        a = "regulatory penalty cascade affects credit rating significantly today"
        b = "regulatory penalty cascade affects supplier ratings significantly over time"
        score = compute_overlap(a, b)
        # Some shared trigrams ("regulatory penalty cascade", "penalty cascade affects")
        # but not identical → 0 < score < 1
        assert 0.1 < score < 0.9

    def test_short_texts_handled_gracefully(self):
        from engine.analysis.perspective_dedup import compute_overlap
        # Below n=3 token count → returns 0.0
        assert compute_overlap("hi", "bye") == 0.0
        assert compute_overlap("", "anything here") == 0.0

    def test_stop_words_dropped(self):
        from engine.analysis.perspective_dedup import compute_overlap
        # Sentences with shared content + extra stop words should overlap
        # (exact threshold depends on tokenisation; assert non-zero rather
        # than a specific value)
        a = "regulatory penalty affects credit rating downgrade severely"
        b = "the regulatory penalty affects the credit rating downgrade severely"
        # After stop word removal, both reduce to nearly-identical token sequences
        assert compute_overlap(a, b) > 0.5


class TestVerifyPerspectivesDistinct:
    def test_truly_distinct_perspectives_pass(self):
        from engine.analysis.perspective_dedup import verify_perspectives_distinct
        perspectives = {
            "cfo": {
                "headline": "Five hundred crore exposure on margin pressure cascade",
                "what_matters": ["P&L hit estimated 200 bps", "Cost of capital up 50 bps"],
            },
            "ceo": {
                "headline": "Strategic positioning shift required for board narrative",
                "what_matters": ["Board engagement pivotal", "Competitive momentum at risk"],
            },
            "esg-analyst": {
                "headline": "GRI 207 BRSR P1 disclosure deadline triggers compliance window",
                "what_matters": ["Audit timeline compressed", "Framework gap analysis needed"],
            },
        }
        warnings = verify_perspectives_distinct(perspectives)
        assert warnings == []

    def test_overlapping_perspectives_flagged(self):
        from engine.analysis.perspective_dedup import verify_perspectives_distinct
        # Identical content across CFO + CEO → must flag
        same_content = {
            "headline": "regulatory penalty cascade affects margin and credit",
            "what_matters": ["margin pressure cascade", "credit rating cascade", "regulatory cascade impact"],
        }
        perspectives = {
            "cfo": same_content,
            "ceo": same_content,
            "esg-analyst": {
                "headline": "framework deep dive section codes deadlines audit",
                "what_matters": ["BRSR P1", "GRI 207 anti-corruption"],
            },
        }
        warnings = verify_perspectives_distinct(perspectives)
        assert len(warnings) >= 1
        # CFO + CEO pair flagged
        flagged_pairs = {(w["perspective_a"], w["perspective_b"]) for w in warnings}
        assert ("cfo", "ceo") in flagged_pairs
        # Regen instruction names CFO and references the overlap
        cfo_warn = next(w for w in warnings if w["perspective_a"] == "cfo")
        assert "CFO" in cfo_warn["regen_instruction"]
        assert "%" in cfo_warn["regen_instruction"]

    def test_threshold_respected(self):
        from engine.analysis.perspective_dedup import verify_perspectives_distinct
        # Same content → 100% overlap → flagged at any threshold ≤1
        same_content = {
            "headline": "regulatory penalty cascade affects margin and credit",
            "what_matters": ["margin pressure cascade", "credit rating cascade", "regulatory cascade impact"],
        }
        perspectives = {
            "cfo": same_content, "ceo": same_content, "esg-analyst": {},
        }
        # Strict 0.99 threshold still flags 100% overlap
        assert len(verify_perspectives_distinct(perspectives, threshold=0.99)) >= 1
        # Loose 0.99 still flags 100% overlap
        assert len(verify_perspectives_distinct(perspectives, threshold=0.5)) >= 1

    def test_missing_perspective_skipped_gracefully(self):
        from engine.analysis.perspective_dedup import verify_perspectives_distinct
        # Only CFO present → no pairs to compare → no warnings
        perspectives = {"cfo": {"headline": "x"}}
        assert verify_perspectives_distinct(perspectives) == []

    def test_dataclass_perspective_supported(self):
        from engine.analysis.perspective_dedup import verify_perspectives_distinct

        @dataclass
        class _Persp:
            headline: str = ""
            what_matters: list[str] | None = None

        perspectives = {
            "cfo": _Persp(headline="margin pressure cascade across credit"),
            "ceo": _Persp(headline="board strategy competitive position"),
            "esg-analyst": _Persp(headline="framework BRSR GRI section codes"),
        }
        # Should not raise on dataclass inputs
        warnings = verify_perspectives_distinct(perspectives)
        assert isinstance(warnings, list)


class TestAllPerspectivesDistinctHelper:
    def test_returns_true_when_distinct(self):
        from engine.analysis.perspective_dedup import all_perspectives_distinct
        perspectives = {
            "cfo": {"headline": "margin pressure cascade", "what_matters": ["x"]},
            "ceo": {"headline": "competitive board strategy", "what_matters": ["y"]},
            "esg-analyst": {"headline": "framework BRSR GRI sections", "what_matters": ["z"]},
        }
        assert all_perspectives_distinct(perspectives) is True

    def test_returns_false_when_overlap(self):
        from engine.analysis.perspective_dedup import all_perspectives_distinct
        same_content = {
            "headline": "regulatory penalty cascade",
            "what_matters": ["margin cascade", "credit cascade", "compliance cascade"],
        }
        perspectives = {
            "cfo": same_content, "ceo": same_content, "esg-analyst": {},
        }
        assert all_perspectives_distinct(perspectives) is False
