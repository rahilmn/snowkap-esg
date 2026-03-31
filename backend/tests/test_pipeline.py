"""Unit tests for ESG scoring pipeline, article enrichment, and feed ranking.

Covers:
- Priority scoring engine (7+1 components, formula, thresholds, edge cases)
- Relevance scoring (5D dimensions, tier classification, clamping)
- Industry materiality weights (SASB-aligned adjustments)
- Regulatory calendar (deadline detection, proximity scoring)
- Source credibility (tier classification, word-boundary matching)
- Language detection (non-English heuristic)
- Risk taxonomy (P×E scoring, classification thresholds, aggregation)
- Event deduplication (title words, Jaccard similarity, clustering)
- Role-based curation (role profiles, relevance scoring, recency)
- Data model integrity (dataclass construction, property computation)

Run: cd snowkap-esg && python -m pytest backend/tests/test_pipeline.py -v
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest


# ══════════════════════════════════════════════
# PRIORITY SCORING ENGINE
# ══════════════════════════════════════════════


class TestPriorityScoring:
    """Tests for backend/services/priority_engine.py"""

    def _calc(self, **kwargs):
        from backend.services.priority_engine import calculate_priority_score

        defaults = {
            "sentiment_score": 0.0,
            "urgency": "medium",
            "impact_score": 50.0,
            "has_financial_signal": False,
            "reversibility": "moderate",
            "framework_count": 1,
            "role_multiplier": 1.0,
            "days_to_deadline": None,
        }
        defaults.update(kwargs)
        return calculate_priority_score(**defaults)

    # --- Happy paths ---

    def test_baseline_low_score(self):
        """Default inputs (neutral, medium urgency, moderate impact) → LOW."""
        score, level = self._calc()
        assert 20 <= score <= 40
        assert level == "LOW"

    def test_critical_scenario(self):
        """Strongly negative + critical urgency + financial + irreversible → CRITICAL."""
        score, level = self._calc(
            sentiment_score=-1.0,
            urgency="critical",
            impact_score=100.0,
            has_financial_signal=True,
            reversibility="irreversible",
            framework_count=5,
        )
        assert score >= 85
        assert level == "CRITICAL"

    def test_low_scenario(self):
        """Neutral + low urgency + no financial + easy reversibility → LOW."""
        score, level = self._calc(
            sentiment_score=0.0,
            urgency="low",
            impact_score=10.0,
            has_financial_signal=False,
            reversibility="easy",
            framework_count=0,
        )
        assert score < 40
        assert level == "LOW"

    # --- Component isolation ---

    def test_negative_sentiment_adds_up_to_25(self):
        """Sentiment of -1.0 should contribute 25 points."""
        score_neg, _ = self._calc(sentiment_score=-1.0, urgency="low", impact_score=0, framework_count=0, reversibility="easy")
        score_zero, _ = self._calc(sentiment_score=0.0, urgency="low", impact_score=0, framework_count=0, reversibility="easy")
        diff = score_neg - score_zero
        assert 24 <= diff <= 26  # ~25 points from sentiment

    def test_positive_sentiment_adds_up_to_10(self):
        """Positive sentiment should contribute up to 10 points (not 25)."""
        score_pos, _ = self._calc(sentiment_score=1.0, urgency="low", impact_score=0, framework_count=0, reversibility="easy")
        score_zero, _ = self._calc(sentiment_score=0.0, urgency="low", impact_score=0, framework_count=0, reversibility="easy")
        diff = score_pos - score_zero
        assert 9 <= diff <= 11  # ~10 points

    def test_financial_signal_adds_15(self):
        """Financial signal adds exactly 15 points."""
        score_with, _ = self._calc(has_financial_signal=True)
        score_without, _ = self._calc(has_financial_signal=False)
        assert abs((score_with - score_without) - 15.0) < 0.5

    def test_framework_breadth_caps_at_5(self):
        """Framework count beyond 5 should not add more points."""
        score_5, _ = self._calc(framework_count=5)
        score_10, _ = self._calc(framework_count=10)
        assert score_5 == score_10

    def test_regulatory_deadline_90_days_adds_20(self):
        """Deadline within 90 days adds 20 points."""
        score_with, _ = self._calc(days_to_deadline=60)
        score_without, _ = self._calc(days_to_deadline=None)
        assert abs((score_with - score_without) - 20.0) < 0.5

    def test_regulatory_deadline_180_days_adds_12(self):
        score_with, _ = self._calc(days_to_deadline=150)
        score_without, _ = self._calc(days_to_deadline=None)
        assert abs((score_with - score_without) - 12.0) < 0.5

    def test_regulatory_deadline_365_days_adds_5(self):
        score_with, _ = self._calc(days_to_deadline=300)
        score_without, _ = self._calc(days_to_deadline=None)
        assert abs((score_with - score_without) - 5.0) < 0.5

    def test_regulatory_deadline_beyond_365_adds_0(self):
        score_with, _ = self._calc(days_to_deadline=400)
        score_without, _ = self._calc(days_to_deadline=None)
        assert abs(score_with - score_without) < 0.5

    # --- Role multipliers ---

    def test_board_multiplier_increases_score(self):
        score_board, _ = self._calc(role_multiplier=1.3)
        score_normal, _ = self._calc(role_multiplier=1.0)
        assert score_board > score_normal

    def test_member_multiplier_decreases_score(self):
        score_member, _ = self._calc(role_multiplier=0.8)
        score_normal, _ = self._calc(role_multiplier=1.0)
        assert score_member < score_normal

    # --- Edge cases ---

    def test_score_never_exceeds_100(self):
        """Even with maximum inputs, score caps at 100."""
        score, _ = self._calc(
            sentiment_score=-1.0, urgency="critical", impact_score=100,
            has_financial_signal=True, reversibility="irreversible",
            framework_count=10, role_multiplier=1.3, days_to_deadline=1,
        )
        assert score <= 100.0

    def test_score_never_below_zero(self):
        score, _ = self._calc(
            sentiment_score=0, urgency="low", impact_score=0,
            has_financial_signal=False, reversibility="easy",
            framework_count=0, role_multiplier=0.1,
        )
        assert score >= 0.0

    def test_none_sentiment_treated_as_zero(self):
        score_none, _ = self._calc(sentiment_score=None)
        score_zero, _ = self._calc(sentiment_score=0.0)
        assert score_none == score_zero

    def test_none_urgency_uses_default(self):
        """None urgency should use default weight (low=3.0)."""
        score, _ = self._calc(urgency=None)
        assert isinstance(score, float)

    def test_unknown_urgency_uses_default(self):
        score, _ = self._calc(urgency="unknown_value")
        assert isinstance(score, float)

    def test_unknown_reversibility_uses_default(self):
        score, _ = self._calc(reversibility="banana")
        assert isinstance(score, float)

    # --- Threshold boundaries ---

    def test_threshold_boundary_critical(self):
        """Score of exactly 85.0 should be CRITICAL."""
        # We can't precisely control the score, so test the classification function
        from backend.services.priority_engine import PRIORITY_THRESHOLDS

        assert PRIORITY_THRESHOLDS[0] == (85.0, "CRITICAL")

    def test_threshold_boundary_high(self):
        from backend.services.priority_engine import PRIORITY_THRESHOLDS

        assert PRIORITY_THRESHOLDS[1] == (70.0, "HIGH")


# ══════════════════════════════════════════════
# RELEVANCE SCORING
# ══════════════════════════════════════════════


class TestRelevanceScoring:
    """Tests for backend/services/relevance_scorer.py"""

    def _score(self, **kwargs):
        from backend.services.relevance_scorer import RelevanceScore

        return RelevanceScore(**kwargs)

    # --- Total calculation ---

    def test_total_sums_all_dimensions(self):
        s = self._score(esg_correlation=2, financial_impact=2, compliance_risk=2, supply_chain_impact=2, people_impact=2)
        assert s.total == 10.0

    def test_total_zero_when_all_zero(self):
        s = self._score()
        assert s.total == 0.0

    def test_total_partial(self):
        s = self._score(esg_correlation=1, financial_impact=2)
        assert s.total == 3.0

    # --- Tier classification ---

    def test_home_tier_requires_7_and_esg(self):
        s = self._score(esg_correlation=2, financial_impact=2, compliance_risk=1, supply_chain_impact=1, people_impact=1)
        assert s.total == 7.0
        assert s.tier == "HOME"

    def test_home_tier_rejected_if_esg_zero(self):
        """Even with score 8, esg_correlation=0 means NOT HOME."""
        s = self._score(esg_correlation=0, financial_impact=2, compliance_risk=2, supply_chain_impact=2, people_impact=2)
        assert s.total == 8.0
        assert s.tier != "HOME"

    def test_secondary_tier(self):
        s = self._score(esg_correlation=1, financial_impact=1, compliance_risk=1, supply_chain_impact=1, people_impact=1)
        assert s.total == 5.0
        assert s.tier == "SECONDARY"

    def test_rejected_tier(self):
        s = self._score(esg_correlation=1, financial_impact=0, compliance_risk=0, supply_chain_impact=0, people_impact=0)
        assert s.total == 1.0
        assert s.tier == "REJECTED"

    # --- Parse from LLM ---

    def test_parse_relevance_valid(self):
        from backend.services.relevance_scorer import parse_relevance_from_llm

        data = {"relevance": {"esg_correlation": 2, "financial_impact": 1, "compliance_risk": 1, "supply_chain_impact": 1, "people_impact": 1}}
        result = parse_relevance_from_llm(data)
        assert result is not None
        assert result.total == 6.0

    def test_parse_relevance_clamps_values(self):
        from backend.services.relevance_scorer import parse_relevance_from_llm

        data = {"relevance": {"esg_correlation": 5, "financial_impact": -1, "compliance_risk": 100}}
        result = parse_relevance_from_llm(data)
        assert result is not None
        assert result.esg_correlation == 2  # Clamped from 5
        assert result.financial_impact == 0  # Clamped from -1
        assert result.compliance_risk == 2  # Clamped from 100

    def test_parse_relevance_none_on_empty(self):
        from backend.services.relevance_scorer import parse_relevance_from_llm

        assert parse_relevance_from_llm({}) is None
        assert parse_relevance_from_llm({"relevance": None}) is None


# ══════════════════════════════════════════════
# INDUSTRY MATERIALITY WEIGHTS
# ══════════════════════════════════════════════


class TestMaterialityMap:
    """Tests for backend/services/materiality_map.py"""

    def test_banking_emissions_low(self):
        from backend.services.materiality_map import get_materiality_weight

        w = get_materiality_weight("Banking", "Emissions")
        assert w <= 0.3  # Low materiality for banks

    def test_infrastructure_emissions_high(self):
        from backend.services.materiality_map import get_materiality_weight

        w = get_materiality_weight("Infrastructure", "Emissions")
        assert w >= 0.8  # High materiality for infrastructure

    def test_banking_ethics_high(self):
        from backend.services.materiality_map import get_materiality_weight

        w = get_materiality_weight("Banking", "Ethics & Compliance")
        assert w >= 0.8

    def test_unknown_industry_returns_fallback(self):
        from backend.services.materiality_map import get_materiality_weight

        w = get_materiality_weight("Alien Technology Corp", "Emissions")
        assert 0.0 <= w <= 1.0  # Returns some valid weight

    def test_unknown_theme_returns_default(self):
        from backend.services.materiality_map import get_materiality_weight

        w = get_materiality_weight("Banking", "Quantum Ethics")
        assert w == 0.5

    def test_case_insensitive_lookup(self):
        from backend.services.materiality_map import get_materiality_weight

        w1 = get_materiality_weight("banking", "emissions")
        w2 = get_materiality_weight("BANKING", "EMISSIONS")
        assert w1 == w2

    # --- Adjustment logic ---

    def test_high_materiality_no_reduction(self):
        from backend.services.materiality_map import apply_materiality_adjustment

        adjusted = apply_materiality_adjustment(7.0, "Infrastructure", "Emissions")
        assert adjusted == 7.0  # Weight >= 0.8, no change

    def test_moderate_materiality_slight_reduction(self):
        from backend.services.materiality_map import apply_materiality_adjustment

        adjusted = apply_materiality_adjustment(7.0, "Consumer Goods", "Emissions")
        # Weight ~0.7, so 7.0 * 0.85 = 5.95
        assert 5.5 <= adjusted <= 6.5

    def test_low_materiality_significant_reduction(self):
        from backend.services.materiality_map import apply_materiality_adjustment

        adjusted = apply_materiality_adjustment(7.0, "Banking", "Emissions")
        # Weight ~0.2, so 7.0 * 0.6 = 4.2
        assert adjusted <= 5.0

    def test_zero_score_stays_zero(self):
        from backend.services.materiality_map import apply_materiality_adjustment

        assert apply_materiality_adjustment(0.0, "Banking", "Emissions") == 0.0


# ══════════════════════════════════════════════
# REGULATORY CALENDAR
# ══════════════════════════════════════════════


class TestRegulatoryCalendar:
    """Tests for backend/services/regulatory_calendar.py"""

    def test_brsr_deadline_found(self):
        from backend.services.regulatory_calendar import find_nearest_deadline

        result = find_nearest_deadline(["BRSR:P6", "GRI:305"])
        assert result is not None
        assert "BRSR" in result["framework"]
        assert result["days_until"] >= 0

    def test_no_match_returns_none(self):
        from backend.services.regulatory_calendar import find_nearest_deadline

        result = find_nearest_deadline(["NONEXISTENT_FRAMEWORK"])
        assert result is None

    def test_empty_list_returns_none(self):
        from backend.services.regulatory_calendar import find_nearest_deadline

        assert find_nearest_deadline([]) is None

    def test_jurisdiction_filter_india(self):
        from backend.services.regulatory_calendar import find_nearest_deadline

        result = find_nearest_deadline(["BRSR:P1"], jurisdiction="INDIA")
        if result:
            assert result["jurisdiction"] == "INDIA"

    # --- Deadline language detection ---

    def test_detect_mandatory_by(self):
        from backend.services.regulatory_calendar import detect_deadline_language

        phrases = detect_deadline_language("This regulation is mandatory by January 2027")
        assert "mandatory by" in phrases

    def test_detect_compliance_deadline(self):
        from backend.services.regulatory_calendar import detect_deadline_language

        phrases = detect_deadline_language("The BRSR compliance deadline is approaching")
        assert "compliance deadline" in phrases

    def test_detect_comes_into_force(self):
        from backend.services.regulatory_calendar import detect_deadline_language

        phrases = detect_deadline_language("The CSDDD comes into force next year")
        assert "comes into force" in phrases

    def test_no_deadline_language(self):
        from backend.services.regulatory_calendar import detect_deadline_language

        phrases = detect_deadline_language("Nike reported strong quarterly earnings")
        assert len(phrases) == 0

    def test_multiple_phrases_detected(self):
        from backend.services.regulatory_calendar import detect_deadline_language

        phrases = detect_deadline_language(
            "The regulation is mandatory by 2027 and the compliance deadline is June. It comes into force immediately."
        )
        assert len(phrases) >= 3


# ══════════════════════════════════════════════
# SOURCE CREDIBILITY
# ══════════════════════════════════════════════


class TestSourceCredibility:
    """Tests for nlp_pipeline.py source tier classification."""

    def _assess(self, source):
        from backend.services.nlp_pipeline import assess_source_credibility

        return assess_source_credibility(source)

    # --- Tier 1: Institutional ---

    def test_sebi_tier_1(self):
        tier, _ = self._assess("SEBI")
        assert tier == 1

    def test_rbi_tier_1(self):
        tier, _ = self._assess("RBI")
        assert tier == 1

    def test_world_bank_tier_1(self):
        tier, _ = self._assess("World Bank")
        assert tier == 1

    # --- Tier 2: Established media ---

    def test_bloomberg_tier_2(self):
        tier, _ = self._assess("Bloomberg")
        assert tier == 2

    def test_moneycontrol_tier_2(self):
        tier, _ = self._assess("Moneycontrol")
        assert tier == 2

    def test_reuters_tier_2(self):
        tier, _ = self._assess("Reuters")
        assert tier == 2

    def test_economic_times_tier_2(self):
        tier, _ = self._assess("Economic Times")
        assert tier == 2

    # --- Tier 3: Secondary ---

    def test_whalesbook_tier_3(self):
        tier, _ = self._assess("Whalesbook")
        assert tier == 3

    def test_generic_trade_tier_3(self):
        tier, _ = self._assess("Industry Trade Journal")
        assert tier == 3

    # --- Edge cases ---

    def test_none_source_tier_4(self):
        tier, _ = self._assess(None)
        assert tier == 4

    def test_empty_source_tier_4(self):
        tier, _ = self._assess("")
        assert tier == 4

    def test_soccerbible_not_tier_1(self):
        """SoccerBible should NOT match 'sebi' via substring."""
        tier, _ = self._assess("SoccerBible")
        assert tier != 1

    def test_gov_domain_tier_1(self):
        tier, _ = self._assess("epa.gov")
        assert tier == 1


# ══════════════════════════════════════════════
# LANGUAGE DETECTION
# ══════════════════════════════════════════════


class TestLanguageDetection:
    """Tests for nlp_pipeline.py _is_non_english."""

    def _detect(self, text):
        from backend.services.nlp_pipeline import _is_non_english

        return _is_non_english(text)

    def test_english_text_returns_false(self):
        assert self._detect("Nike reports strong quarterly earnings in sustainability") is False

    def test_marathi_text_returns_true(self):
        assert self._detect("बंधन बँकेची ESG मध्ये मोठी झेप") is True

    def test_hindi_text_returns_true(self):
        assert self._detect("आरबीआई ने जलवायु जोखिम प्रकटीकरण रोका") is True

    def test_mixed_mostly_english_returns_false(self):
        assert self._detect("Nike Inc reported ₹500 Cr revenue growth in Q3 2026") is False

    def test_empty_string_returns_false(self):
        assert self._detect("") is False

    def test_numbers_only_returns_false(self):
        assert self._detect("12345 67890") is False


# ══════════════════════════════════════════════
# RISK TAXONOMY
# ══════════════════════════════════════════════


class TestRiskTaxonomy:
    """Tests for backend/services/risk_taxonomy.py"""

    def test_category_score_computation(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="test", category_name="Test Risk", probability=4, exposure=5)
        assert cat.risk_score == 20

    def test_category_score_min(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="test", category_name="Test", probability=1, exposure=1)
        assert cat.risk_score == 1

    def test_category_score_max(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="test", category_name="Test", probability=5, exposure=5)
        assert cat.risk_score == 25

    # --- Classification ---

    def test_critical_classification(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="t", category_name="T", probability=5, exposure=4)
        assert cat.classification == "CRITICAL"

    def test_high_classification(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="t", category_name="T", probability=4, exposure=3)
        assert cat.classification == "HIGH"

    def test_moderate_classification(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="t", category_name="T", probability=3, exposure=2)
        assert cat.classification == "MODERATE"

    def test_low_classification(self):
        from backend.services.risk_taxonomy import CategoryScore

        cat = CategoryScore(category_id="t", category_name="T", probability=1, exposure=2)
        assert cat.classification == "LOW"

    # --- Risk Assessment aggregate ---

    def test_aggregate_score_normalization(self):
        from backend.services.risk_taxonomy import CategoryScore, RiskAssessment

        cats = [CategoryScore(category_id=f"c{i}", category_name=f"Cat{i}", probability=5, exposure=5) for i in range(10)]
        ra = RiskAssessment(categories=cats)
        assert ra.total_score == 250
        assert ra.aggregate_score == 1.0

    def test_aggregate_score_empty(self):
        from backend.services.risk_taxonomy import RiskAssessment

        ra = RiskAssessment(categories=[])
        assert ra.total_score == 0
        assert ra.aggregate_score == 0.0

    def test_top_risks_returns_top_3(self):
        from backend.services.risk_taxonomy import CategoryScore, RiskAssessment

        cats = [
            CategoryScore("a", "A", 5, 5),  # 25
            CategoryScore("b", "B", 1, 1),  # 1
            CategoryScore("c", "C", 4, 4),  # 16
            CategoryScore("d", "D", 3, 3),  # 9
            CategoryScore("e", "E", 5, 4),  # 20
        ]
        ra = RiskAssessment(categories=cats)
        top = ra.top_risks
        assert len(top) == 3
        assert top[0].risk_score == 25
        assert top[1].risk_score == 20
        assert top[2].risk_score == 16


# ══════════════════════════════════════════════
# EVENT DEDUPLICATION
# ══════════════════════════════════════════════


class TestEventDeduplication:
    """Tests for backend/services/event_deduplication.py"""

    def test_title_words_extraction(self):
        from backend.services.event_deduplication import _word_set as _title_words

        words = _title_words("IDFC First Bank reports Rs 590 crore fraud at Chandigarh branch")
        assert "idfc" in words
        assert "bank" in words
        assert "fraud" in words
        assert "chandigarh" in words
        # Short words filtered out
        assert "rs" not in words
        assert "at" not in words

    def test_title_words_empty_string(self):
        from backend.services.event_deduplication import _word_set as _title_words

        assert _title_words("") == set()

    def test_jaccard_identical(self):
        from backend.services.event_deduplication import _jaccard_similarity

        s = {"fraud", "bank", "idfc"}
        assert _jaccard_similarity(s, s) == 1.0

    def test_jaccard_disjoint(self):
        from backend.services.event_deduplication import _jaccard_similarity

        assert _jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_partial(self):
        from backend.services.event_deduplication import _jaccard_similarity

        sim = _jaccard_similarity({"fraud", "bank", "idfc"}, {"fraud", "bank", "report"})
        assert 0.3 < sim < 0.7  # 2 shared out of 4 unique = 0.5

    def test_jaccard_empty_sets(self):
        from backend.services.event_deduplication import _jaccard_similarity

        assert _jaccard_similarity(set(), set()) == 0.0
        assert _jaccard_similarity({"a"}, set()) == 0.0

    def test_similar_fraud_articles_cluster(self):
        """Two articles about same fraud should have similarity >= 0.35."""
        from backend.services.event_deduplication import _jaccard_similarity, _word_set as _title_words

        a = _title_words("IDFC First Bank reports Rs 590 crore fraud at Chandigarh branch")
        b = _title_words("Bank staff colluded with outsiders in Rs 590 crore fraud at IDFC")
        sim = _jaccard_similarity(a, b)
        assert sim >= 0.35  # Above clustering threshold

    def test_unrelated_articles_dont_cluster(self):
        from backend.services.event_deduplication import _jaccard_similarity, _word_set as _title_words

        a = _title_words("IDFC First Bank reports Rs 590 crore fraud")
        b = _title_words("Nike appoints new Chief Sustainability Officer")
        sim = _jaccard_similarity(a, b)
        assert sim < 0.35


# ══════════════════════════════════════════════
# ROLE-BASED CURATION
# ══════════════════════════════════════════════


class TestRoleCuration:
    """Tests for backend/services/role_curation.py"""

    def test_board_member_profile_exists(self):
        from backend.services.role_curation import ROLE_PROFILES

        assert "board_member" in ROLE_PROFILES
        assert ROLE_PROFILES["board_member"]["boost"] == 1.3
        assert ROLE_PROFILES["board_member"]["alert_threshold"] == 85

    def test_ceo_profile(self):
        from backend.services.role_curation import ROLE_PROFILES

        assert "ceo" in ROLE_PROFILES
        assert ROLE_PROFILES["ceo"]["boost"] == 1.2

    def test_core_roles_have_required_fields(self):
        from backend.services.role_curation import ROLE_PROFILES

        required = {"boost", "alert_threshold", "priority_pillars", "priority_frameworks", "content_types"}
        core_roles = ["board_member", "ceo", "cfo", "cso", "compliance", "supply_chain"]
        for role in core_roles:
            if role in ROLE_PROFILES:
                for field in required:
                    assert field in ROLE_PROFILES[role], f"Role '{role}' missing field '{field}'"

    # --- Role relevance scoring ---

    def test_regulatory_content_high_for_compliance(self):
        from backend.services.role_curation import compute_role_relevance

        score = compute_role_relevance("compliance", "regulatory", ["BRSR"], "G")
        assert score >= 50

    def test_financial_content_high_for_cfo(self):
        from backend.services.role_curation import compute_role_relevance

        score = compute_role_relevance("cfo", "financial", ["TCFD", "IFRS_S1"], "E")
        assert score >= 50

    def test_operational_content_high_for_supply_chain(self):
        from backend.services.role_curation import compute_role_relevance

        score = compute_role_relevance("supply_chain", "operational", ["GRI"], "E")
        assert score >= 40

    def test_role_relevance_range(self):
        """Score should always be 0-100."""
        from backend.services.role_curation import compute_role_relevance

        score = compute_role_relevance("ceo", "regulatory", ["BRSR", "TCFD"], "G")
        assert 0 <= score <= 100

    def test_unknown_role_returns_reasonable_score(self):
        from backend.services.role_curation import compute_role_relevance

        score = compute_role_relevance("unknown_role", "regulatory", ["BRSR"], "G")
        assert isinstance(score, (int, float))

    # --- Recency scoring ---

    def test_recency_recent_article_high(self):
        from backend.services.role_curation import recency_score

        now = datetime.now(timezone.utc).isoformat()
        score = recency_score(now)
        assert score >= 90

    def test_recency_old_article_low(self):
        from backend.services.role_curation import recency_score

        old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        score = recency_score(old)
        assert score <= 0 or score < 10  # 72h * 2 = 144 deducted from 100

    def test_recency_none_returns_default(self):
        from backend.services.role_curation import recency_score

        score = recency_score(None)
        assert 0 <= score <= 100  # Returns a valid score even for None

    def test_recency_never_negative(self):
        from backend.services.role_curation import recency_score

        very_old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        score = recency_score(very_old)
        assert score >= 0


# ══════════════════════════════════════════════
# DATA MODEL INTEGRITY
# ══════════════════════════════════════════════


class TestDataModels:
    """Tests for dataclass construction and property computation."""

    def test_nlp_extraction_defaults(self):
        from backend.services.nlp_pipeline import NLPExtraction

        nlp = NLPExtraction()
        assert nlp.sentiment_score == 0
        assert nlp.sentiment_label == "NEUTRAL"
        assert nlp.primary_tone == "neutral"
        assert nlp.source_tier == 3
        assert nlp.named_entities == []

    def test_nlp_extraction_to_dict(self):
        from backend.services.nlp_pipeline import NLPExtraction

        nlp = NLPExtraction(sentiment_score=-1, sentiment_label="NEGATIVE", primary_tone="cautionary")
        d = nlp.to_dict()
        assert d["sentiment"]["score"] == -1
        assert d["sentiment"]["label"] == "NEGATIVE"
        assert d["tone"]["primary"] == "cautionary"

    def test_extracted_entity_construction(self):
        from backend.ontology.entity_extractor import ExtractedEntity

        e = ExtractedEntity(text="Nike", entity_type="company", confidence=0.95)
        assert e.text == "Nike"
        assert e.entity_type == "company"
        assert e.confidence == 0.95
        assert e.resolved_uri is None

    def test_extraction_result_defaults(self):
        from backend.ontology.entity_extractor import ExtractionResult

        r = ExtractionResult(entities=[])
        assert r.esg_pillar is None
        assert r.financial_signal is False
        assert r.sentiment_score is None
        assert r.frameworks_mentioned == []

    def test_esg_theme_tags_construction(self):
        from backend.services.esg_theme_tagger import ESGThemeTags

        tags = ESGThemeTags(
            primary_theme="Emissions",
            primary_pillar="Environmental",
            confidence=0.9,
        )
        assert tags.primary_theme == "Emissions"
        assert tags.primary_pillar == "Environmental"
        assert tags.method == "llm"
        assert tags.secondary_themes == []

    def test_esg_taxonomy_has_all_pillars(self):
        from backend.services.esg_theme_tagger import ESG_TAXONOMY

        assert "Environmental" in ESG_TAXONOMY
        assert "Social" in ESG_TAXONOMY
        assert "Governance" in ESG_TAXONOMY

    def test_esg_taxonomy_theme_count(self):
        from backend.services.esg_theme_tagger import ESG_TAXONOMY

        total = sum(len(themes) for themes in ESG_TAXONOMY.values())
        assert total == 21


# ══════════════════════════════════════════════
# SOURCE MATCHING (word boundary)
# ══════════════════════════════════════════════


class TestSourceMatching:
    """Tests for _source_matches word-boundary matching."""

    def _match(self, source, terms):
        from backend.services.nlp_pipeline import _source_matches

        return _source_matches(source, terms)

    def test_exact_match(self):
        assert self._match("bloomberg", {"bloomberg"}) is True

    def test_multi_word_match(self):
        assert self._match("financial times", {"financial times"}) is True

    def test_no_substring_match(self):
        """'sebi' should NOT match inside 'soccerbible'."""
        assert self._match("soccerbible", {"sebi"}) is False

    def test_start_boundary_match(self):
        assert self._match("sebi.gov", {"sebi"}) is True

    def test_no_match(self):
        assert self._match("random blog", {"bloomberg", "reuters"}) is False

    def test_empty_terms(self):
        assert self._match("bloomberg", set()) is False

    def test_word_in_phrase(self):
        assert self._match("cnbc tv18", {"cnbc tv18"}) is True
