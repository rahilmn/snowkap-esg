"""Tests for LLM response parsing across all ESG pipeline services.

Validates that every JSON parsing layer handles:
- Valid JSON → correct object construction
- Malformed JSON → graceful fallback (None or defaults)
- Empty string → graceful handling
- Markdown-fenced JSON (```json ... ```) → strips and parses
- Missing required fields → uses defaults, no crash
- Extra unexpected fields → ignores them, no crash
- Wrong types (string instead of int, int instead of list) → handles or defaults
- Null values for required fields → handles gracefully
- Nested malformed JSON → handles
- Unicode/special chars → preserved correctly

Tested modules:
- backend/services/relevance_scorer.py — parse_relevance_from_llm
- backend/services/risk_taxonomy.py — _parse_llm_response, CategoryScore, RiskAssessment, _clamp, classify_risk
- backend/services/nlp_pipeline.py — NLPExtraction, assess_source_credibility, brace extraction
- backend/services/esg_theme_tagger.py — ESGThemeTags, _validate_theme, _validate_sub_metrics, infer_themes_from_keywords
- backend/services/rereact_engine.py — validator JSON structure checks
- backend/services/risk_spotlight.py — spotlight JSON validation
- backend/ontology/entity_extractor.py — _safe_float, _safe_enum, normalize_framework

Run: python -m pytest backend/tests/test_llm_parsing.py -v
"""

import json

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 1. RELEVANCE SCORER — parse_relevance_from_llm
# ══════════════════════════════════════════════════════════════════════════════


class TestParseRelevanceFromLLM:
    """Tests for backend/services/relevance_scorer.py — parse_relevance_from_llm."""

    def _parse(self, data: dict):
        from backend.services.relevance_scorer import parse_relevance_from_llm
        return parse_relevance_from_llm(data)

    # --- Valid inputs ---

    def test_valid_full_scores(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 2,
                "financial_impact": 1,
                "compliance_risk": 2,
                "supply_chain_impact": 0,
                "people_impact": 1,
            }
        })
        assert result is not None
        assert result.esg_correlation == 2
        assert result.financial_impact == 1
        assert result.compliance_risk == 2
        assert result.supply_chain_impact == 0
        assert result.people_impact == 1
        assert result.total == 6.0

    def test_valid_all_zeros(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 0,
                "financial_impact": 0,
                "compliance_risk": 0,
                "supply_chain_impact": 0,
                "people_impact": 0,
            }
        })
        assert result is not None
        assert result.total == 0.0
        assert result.tier == "REJECTED"

    def test_valid_all_max(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 2,
                "financial_impact": 2,
                "compliance_risk": 2,
                "supply_chain_impact": 2,
                "people_impact": 2,
            }
        })
        assert result is not None
        assert result.total == 10.0
        assert result.tier == "HOME"

    def test_home_tier_requires_esg_correlation(self):
        """Total >= 7 but esg_correlation=0 should NOT be HOME."""
        result = self._parse({
            "relevance": {
                "esg_correlation": 0,
                "financial_impact": 2,
                "compliance_risk": 2,
                "supply_chain_impact": 2,
                "people_impact": 2,
            }
        })
        assert result is not None
        assert result.total == 8.0
        assert result.tier == "SECONDARY"  # not HOME because esg=0

    # --- Missing / malformed ---

    def test_no_relevance_key(self):
        result = self._parse({"other_data": 123})
        assert result is None

    def test_relevance_is_none(self):
        result = self._parse({"relevance": None})
        assert result is None

    def test_relevance_is_string(self):
        result = self._parse({"relevance": "high"})
        assert result is None

    def test_relevance_is_list(self):
        result = self._parse({"relevance": [1, 2, 3]})
        assert result is None

    def test_empty_dict(self):
        result = self._parse({})
        assert result is None

    def test_empty_relevance_dict(self):
        """Empty relevance dict — all dimensions should default to 0."""
        result = self._parse({"relevance": {}})
        assert result is not None
        assert result.total == 0.0

    # --- Clamping / type coercion ---

    def test_values_above_max_clamped(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 5,
                "financial_impact": 100,
                "compliance_risk": 2,
                "supply_chain_impact": 2,
                "people_impact": 2,
            }
        })
        assert result is not None
        assert result.esg_correlation == 2
        assert result.financial_impact == 2

    def test_negative_values_clamped_to_zero(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": -1,
                "financial_impact": -5,
                "compliance_risk": 0,
                "supply_chain_impact": 0,
                "people_impact": 0,
            }
        })
        assert result is not None
        assert result.esg_correlation == 0
        assert result.financial_impact == 0

    def test_float_values_truncated(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 1.7,
                "financial_impact": 0.5,
                "compliance_risk": 0,
                "supply_chain_impact": 0,
                "people_impact": 0,
            }
        })
        assert result is not None
        assert result.esg_correlation == 1
        assert result.financial_impact == 0

    def test_string_values_default_to_zero(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": "high",
                "financial_impact": "2",  # string of number — not int/float
                "compliance_risk": None,
                "supply_chain_impact": True,  # bool, not int
                "people_impact": [],
            }
        })
        assert result is not None
        assert result.esg_correlation == 0  # "high" is not int/float
        assert result.compliance_risk == 0

    def test_extra_fields_ignored(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 2,
                "financial_impact": 1,
                "compliance_risk": 1,
                "supply_chain_impact": 1,
                "people_impact": 1,
                "extra_field": 99,
                "another_unknown": "value",
            }
        })
        assert result is not None
        assert result.total == 6.0

    def test_missing_some_dimensions(self):
        result = self._parse({
            "relevance": {
                "esg_correlation": 2,
                "financial_impact": 1,
            }
        })
        assert result is not None
        assert result.esg_correlation == 2
        assert result.financial_impact == 1
        assert result.compliance_risk == 0
        assert result.supply_chain_impact == 0
        assert result.people_impact == 0

    def test_unicode_in_surrounding_data(self):
        """Unicode in the outer dict should not affect relevance parsing."""
        result = self._parse({
            "title": "ESG Report: ₹500 Crore Investment 🌍",
            "relevance": {
                "esg_correlation": 2,
                "financial_impact": 2,
                "compliance_risk": 1,
                "supply_chain_impact": 0,
                "people_impact": 1,
            }
        })
        assert result is not None
        assert result.total == 6.0


# ══════════════════════════════════════════════════════════════════════════════
# 2. RISK TAXONOMY — _parse_llm_response, CategoryScore, _clamp, classify_risk
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskTaxonomyClamp:
    """Tests for the _clamp helper in risk_taxonomy.py."""

    def _clamp(self, value, low=1, high=5):
        from backend.services.risk_taxonomy import _clamp
        return _clamp(value, low, high)

    def test_normal_value(self):
        assert self._clamp(3) == 3

    def test_min_boundary(self):
        assert self._clamp(1) == 1

    def test_max_boundary(self):
        assert self._clamp(5) == 5

    def test_below_min(self):
        assert self._clamp(0) == 1

    def test_above_max(self):
        assert self._clamp(10) == 5

    def test_negative(self):
        assert self._clamp(-5) == 1

    def test_string_number(self):
        assert self._clamp("3") == 3

    def test_string_invalid(self):
        assert self._clamp("abc") == 1

    def test_none(self):
        assert self._clamp(None) == 1

    def test_float(self):
        assert self._clamp(3.7) == 3

    def test_bool_true(self):
        # bool is subclass of int in Python: True == 1
        assert self._clamp(True) == 1

    def test_list(self):
        assert self._clamp([1, 2]) == 1


class TestClassifyRisk:
    """Tests for classify_risk threshold function."""

    def _classify(self, score):
        from backend.services.risk_taxonomy import classify_risk
        return classify_risk(score)

    def test_critical_threshold(self):
        assert self._classify(25) == "CRITICAL"
        assert self._classify(20) == "CRITICAL"

    def test_high_threshold(self):
        assert self._classify(19) == "HIGH"
        assert self._classify(12) == "HIGH"

    def test_moderate_threshold(self):
        assert self._classify(11) == "MODERATE"
        assert self._classify(6) == "MODERATE"

    def test_low_threshold(self):
        assert self._classify(5) == "LOW"
        assert self._classify(1) == "LOW"

    def test_zero(self):
        assert self._classify(0) == "LOW"


class TestCategoryScore:
    """Tests for CategoryScore dataclass construction and properties."""

    def _make(self, **kwargs):
        from backend.services.risk_taxonomy import CategoryScore
        defaults = {
            "category_id": "physical",
            "category_name": "Physical Risk",
            "probability": 3,
            "exposure": 4,
        }
        defaults.update(kwargs)
        return CategoryScore(**defaults)

    def test_risk_score_multiplication(self):
        cs = self._make(probability=3, exposure=4)
        assert cs.risk_score == 12

    def test_risk_score_max(self):
        cs = self._make(probability=5, exposure=5)
        assert cs.risk_score == 25
        assert cs.classification == "CRITICAL"

    def test_risk_score_min(self):
        cs = self._make(probability=1, exposure=1)
        assert cs.risk_score == 1
        assert cs.classification == "LOW"

    def test_probability_label(self):
        cs = self._make(probability=4)
        assert cs.probability_label == "Likely"

    def test_exposure_label(self):
        cs = self._make(exposure=5)
        assert cs.exposure_label == "Critical"

    def test_to_dict_keys(self):
        cs = self._make()
        d = cs.to_dict()
        expected_keys = {
            "category_id", "category_name", "probability", "probability_label",
            "exposure", "exposure_label", "risk_score", "classification", "rationale",
        }
        assert set(d.keys()) == expected_keys

    def test_rationale_default(self):
        cs = self._make()
        assert cs.rationale == ""

    def test_custom_rationale(self):
        cs = self._make(rationale="Flooding risk is high")
        assert cs.rationale == "Flooding risk is high"


class TestRiskAssessmentDataclass:
    """Tests for RiskAssessment aggregate properties."""

    def _make_assessment(self, scores):
        from backend.services.risk_taxonomy import CategoryScore, RiskAssessment
        categories = [
            CategoryScore(
                category_id=s["id"],
                category_name=s.get("name", s["id"]),
                probability=s["p"],
                exposure=s["e"],
            )
            for s in scores
        ]
        return RiskAssessment(categories=categories)

    def test_total_score(self):
        ra = self._make_assessment([
            {"id": "physical", "p": 3, "e": 4},
            {"id": "regulatory", "p": 2, "e": 2},
        ])
        assert ra.total_score == 12 + 4  # 16

    def test_aggregate_score_normalised(self):
        ra = self._make_assessment([
            {"id": "physical", "p": 5, "e": 5},  # 25
        ])
        assert ra.aggregate_score == round(25 / 250, 4)

    def test_empty_categories(self):
        from backend.services.risk_taxonomy import RiskAssessment
        ra = RiskAssessment(categories=[])
        assert ra.total_score == 0
        assert ra.aggregate_score == 0.0
        assert ra.top_risks == []

    def test_top_risks_returns_top_3(self):
        ra = self._make_assessment([
            {"id": "a", "p": 1, "e": 1},
            {"id": "b", "p": 5, "e": 5},
            {"id": "c", "p": 3, "e": 3},
            {"id": "d", "p": 4, "e": 4},
            {"id": "e", "p": 2, "e": 2},
        ])
        top = ra.top_risks
        assert len(top) == 3
        assert top[0].category_id == "b"  # 25
        assert top[1].category_id == "d"  # 16
        assert top[2].category_id == "c"  # 9


class TestParseLLMResponseRiskTaxonomy:
    """Tests for _parse_llm_response in risk_taxonomy.py."""

    def _parse(self, raw: str):
        from backend.services.risk_taxonomy import _parse_llm_response
        return _parse_llm_response(raw)

    def test_valid_json_all_categories(self):
        from backend.services.risk_taxonomy import RISK_CATEGORIES
        categories = [
            {"category_id": cat["id"], "probability": 3, "exposure": 2, "rationale": "Test."}
            for cat in RISK_CATEGORIES
        ]
        raw = json.dumps({"categories": categories})
        result = self._parse(raw)
        assert len(result.categories) == 10
        assert all(c.probability == 3 for c in result.categories)
        assert all(c.exposure == 2 for c in result.categories)

    def test_valid_json_partial_categories(self):
        """Missing categories should be filled with defaults (p=1, e=1)."""
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": 4, "exposure": 5, "rationale": "High flood risk."},
            {"category_id": "regulatory", "probability": 3, "exposure": 3, "rationale": "New regs."},
        ]})
        result = self._parse(raw)
        assert len(result.categories) == 10
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert phys.probability == 4
        assert phys.exposure == 5
        # Filled defaults
        tech = next(c for c in result.categories if c.category_id == "technological")
        assert tech.probability == 1
        assert tech.exposure == 1

    def test_markdown_fenced_json(self):
        inner = json.dumps({"categories": [
            {"category_id": "physical", "probability": 2, "exposure": 3, "rationale": "OK."}
        ]})
        raw = f"```json\n{inner}\n```"
        result = self._parse(raw)
        phys = next((c for c in result.categories if c.category_id == "physical"), None)
        assert phys is not None
        assert phys.probability == 2

    def test_malformed_json_returns_default(self):
        result = self._parse('{"categories": [{"category_id": "physical", "probability": 3')
        # Should return default assessment with all 10 at p=1, e=1
        assert len(result.categories) == 10
        assert all(c.probability == 1 for c in result.categories)

    def test_empty_string_returns_default(self):
        result = self._parse("")
        assert len(result.categories) == 10
        assert all(c.probability == 1 for c in result.categories)

    def test_wrong_types_clamped(self):
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": "high", "exposure": "severe", "rationale": 12345},
        ]})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert phys.probability == 1  # "high" -> _clamp default
        assert phys.exposure == 1     # "severe" -> _clamp default
        assert phys.rationale == "12345"  # str() coercion

    def test_null_values(self):
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": None, "exposure": None, "rationale": None},
        ]})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert phys.probability == 1
        assert phys.exposure == 1
        assert phys.rationale == "None"

    def test_extra_fields_ignored(self):
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": 3, "exposure": 4, "rationale": "X", "extra": "ignored"},
        ], "metadata": "should be ignored"})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert phys.probability == 3

    def test_invalid_category_id_skipped(self):
        raw = json.dumps({"categories": [
            {"category_id": "nonexistent_cat", "probability": 5, "exposure": 5, "rationale": "???"},
            {"category_id": "physical", "probability": 3, "exposure": 2, "rationale": "OK"},
        ]})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert phys.probability == 3
        # nonexistent_cat should not appear
        assert not any(c.category_id == "nonexistent_cat" for c in result.categories)

    def test_duplicate_category_id_keeps_first(self):
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": 5, "exposure": 5, "rationale": "First"},
            {"category_id": "physical", "probability": 1, "exposure": 1, "rationale": "Dupe"},
        ]})
        result = self._parse(raw)
        phys_list = [c for c in result.categories if c.category_id == "physical"]
        assert len(phys_list) == 1
        assert phys_list[0].probability == 5

    def test_categories_as_top_level_array(self):
        """_parse_llm_response also handles a bare array (no 'categories' key)."""
        raw = json.dumps([
            {"category_id": "physical", "probability": 3, "exposure": 4, "rationale": "Test"},
        ])
        result = self._parse(raw)
        phys = next((c for c in result.categories if c.category_id == "physical"), None)
        assert phys is not None
        assert phys.probability == 3

    def test_unicode_in_rationale(self):
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": 2, "exposure": 3,
             "rationale": "Facility near ₹500 Crore dam — 洪水 risk elevated 🌊"},
        ]})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert "₹500" in phys.rationale
        assert "洪水" in phys.rationale
        assert "🌊" in phys.rationale

    def test_rationale_truncated_to_300(self):
        long_rationale = "A" * 500
        raw = json.dumps({"categories": [
            {"category_id": "physical", "probability": 2, "exposure": 3, "rationale": long_rationale},
        ]})
        result = self._parse(raw)
        phys = next(c for c in result.categories if c.category_id == "physical")
        assert len(phys.rationale) == 300


# ══════════════════════════════════════════════════════════════════════════════
# 3. NLP PIPELINE — NLPExtraction, source credibility, brace extraction
# ══════════════════════════════════════════════════════════════════════════════


class TestNLPExtractionDataclass:
    """Tests for NLPExtraction dataclass defaults and to_dict."""

    def test_defaults(self):
        from backend.services.nlp_pipeline import NLPExtraction
        nlp = NLPExtraction()
        assert nlp.sentiment_score == 0
        assert nlp.sentiment_label == "NEUTRAL"
        assert nlp.primary_tone == "neutral"
        assert nlp.secondary_tone is None
        assert nlp.core_claim == ""
        assert nlp.source_tier == 3
        assert nlp.named_entities == []

    def test_to_dict_structure(self):
        from backend.services.nlp_pipeline import NLPExtraction
        nlp = NLPExtraction(sentiment_score=-1, sentiment_label="NEGATIVE")
        d = nlp.to_dict()
        assert d["sentiment"]["score"] == -1
        assert d["sentiment"]["label"] == "NEGATIVE"
        assert "tone" in d
        assert "narrative_arc" in d
        assert "source_credibility" in d
        assert "esg_signals" in d


class TestSourceCredibility:
    """Tests for assess_source_credibility rule-based function."""

    def _assess(self, source):
        from backend.services.nlp_pipeline import assess_source_credibility
        return assess_source_credibility(source)

    def test_tier1_sebi(self):
        tier, _ = self._assess("SEBI")
        assert tier == 1

    def test_tier1_rbi(self):
        tier, _ = self._assess("RBI circular")
        assert tier == 1

    def test_tier1_gov_domain(self):
        tier, _ = self._assess("pollution-board.gov.in")
        assert tier == 1

    def test_tier2_bloomberg(self):
        tier, _ = self._assess("Bloomberg")
        assert tier == 2

    def test_tier2_economic_times(self):
        tier, _ = self._assess("Economic Times")
        assert tier == 2

    def test_tier3_trade_journal(self):
        tier, _ = self._assess("Mining Industry Journal")
        assert tier == 3

    def test_tier3_unknown(self):
        tier, _ = self._assess("random-blog.xyz")
        assert tier == 3

    def test_tier4_no_source(self):
        tier, _ = self._assess(None)
        assert tier == 4

    def test_tier4_empty_string(self):
        tier, _ = self._assess("")
        assert tier == 4

    def test_sebi_not_in_soccerbible(self):
        """Word-boundary matching: 'sebi' must not match 'SoccerBible'."""
        tier, _ = self._assess("SoccerBible")
        assert tier != 1


class TestNLPBraceExtraction:
    """Tests for the balanced-brace JSON extraction logic from nlp_pipeline.py.

    The brace extraction is inline in run_nlp_pipeline, so we replicate
    the exact logic here for unit testing.
    """

    def _extract_json(self, raw: str) -> dict | None:
        """Replicate the brace-finding logic from nlp_pipeline.py lines 270-304."""
        raw = raw.strip()
        # Strip markdown
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Balanced brace extraction
        start = raw.find("{")
        if start >= 0:
            depth = 0
            end = -1
            in_string = False
            escape_next = False
            for i in range(start, len(raw)):
                c = raw[i]
                if escape_next:
                    escape_next = False
                    continue
                if c == "\\":
                    escape_next = True
                    continue
                if c == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end > start:
                try:
                    return json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    pass
        return None

    def test_clean_json(self):
        data = self._extract_json('{"sentiment_score": 1, "primary_tone": "neutral"}')
        assert data is not None
        assert data["sentiment_score"] == 1

    def test_markdown_fenced(self):
        raw = '```json\n{"sentiment_score": -2}\n```'
        data = self._extract_json(raw)
        assert data is not None
        assert data["sentiment_score"] == -2

    def test_json_with_preamble(self):
        raw = 'Here is the analysis:\n{"sentiment_score": 0, "primary_tone": "analytical"}\n\nDone.'
        data = self._extract_json(raw)
        assert data is not None
        assert data["primary_tone"] == "analytical"

    def test_nested_braces(self):
        raw = '{"outer": {"inner": {"deep": 1}}, "value": 2}'
        data = self._extract_json(raw)
        assert data is not None
        assert data["outer"]["inner"]["deep"] == 1
        assert data["value"] == 2

    def test_braces_in_strings(self):
        raw = '{"text": "use {braces} in strings", "count": 5}'
        data = self._extract_json(raw)
        assert data is not None
        assert data["text"] == "use {braces} in strings"
        assert data["count"] == 5

    def test_empty_string(self):
        data = self._extract_json("")
        assert data is None

    def test_no_json(self):
        data = self._extract_json("This is just plain text with no JSON at all.")
        assert data is None

    def test_unclosed_brace(self):
        data = self._extract_json('{"key": "value"')
        assert data is None

    def test_unicode_values(self):
        raw = '{"claim": "₹500 Crore investment in 新能源"}'
        data = self._extract_json(raw)
        assert data is not None
        assert "₹500" in data["claim"]
        assert "新能源" in data["claim"]

    def test_escaped_quotes(self):
        raw = r'{"text": "He said \"hello\" to them"}'
        data = self._extract_json(raw)
        assert data is not None
        assert "hello" in data["text"]


class TestNLPFieldParsing:
    """Tests for how nlp_pipeline parses individual fields from LLM JSON data dict.

    These mirror the field extraction from lines 309-333 of nlp_pipeline.py.
    """

    def _apply_parsing(self, data: dict) -> dict:
        """Apply the same parsing rules as run_nlp_pipeline (without the LLM call)."""
        from backend.services.nlp_pipeline import SENTIMENT_LABELS, VALID_TONES

        result = {}

        # Sentiment
        score = data.get("sentiment_score", 0)
        score = max(-2, min(2, int(score)))
        result["sentiment_score"] = score
        result["sentiment_label"] = SENTIMENT_LABELS.get(score, "NEUTRAL")

        # Tone
        primary = (data.get("primary_tone") or "neutral").lower()
        result["primary_tone"] = primary if primary in VALID_TONES else "neutral"
        secondary = (data.get("secondary_tone") or "").lower()
        result["secondary_tone"] = secondary if secondary in VALID_TONES else None

        # Narrative
        result["core_claim"] = data.get("core_claim", "fallback_title")
        result["supporting_evidence"] = data.get("supporting_evidence", [])[:3]
        result["implied_causation"] = data.get("implied_causation", "")
        result["stakeholder_framing"] = data.get("stakeholder_framing", {})
        tf = (data.get("temporal_framing") or "present").lower()
        result["temporal_framing"] = tf if tf in ("backward", "present", "forward") else "present"

        # ESG signals
        result["named_entities"] = data.get("named_entities", [])[:20]
        result["quantitative_claims"] = data.get("quantitative_claims", [])[:10]

        return result

    def test_valid_full_data(self):
        r = self._apply_parsing({
            "sentiment_score": -1,
            "primary_tone": "cautionary",
            "secondary_tone": "urgent",
            "core_claim": "Company faces penalty",
            "supporting_evidence": ["₹50 Crore fine", "SEBI notice"],
            "temporal_framing": "present",
        })
        assert r["sentiment_score"] == -1
        assert r["sentiment_label"] == "NEGATIVE"
        assert r["primary_tone"] == "cautionary"
        assert r["secondary_tone"] == "urgent"
        assert r["core_claim"] == "Company faces penalty"

    def test_sentiment_clamped(self):
        r = self._apply_parsing({"sentiment_score": 10})
        assert r["sentiment_score"] == 2
        r = self._apply_parsing({"sentiment_score": -10})
        assert r["sentiment_score"] == -2

    def test_invalid_tone_defaults(self):
        r = self._apply_parsing({"primary_tone": "ANGRY", "secondary_tone": "furious"})
        assert r["primary_tone"] == "neutral"
        assert r["secondary_tone"] is None

    def test_none_tone(self):
        r = self._apply_parsing({"primary_tone": None, "secondary_tone": None})
        assert r["primary_tone"] == "neutral"
        assert r["secondary_tone"] is None

    def test_invalid_temporal_framing(self):
        r = self._apply_parsing({"temporal_framing": "yesterday"})
        assert r["temporal_framing"] == "present"

    def test_missing_all_fields(self):
        r = self._apply_parsing({})
        assert r["sentiment_score"] == 0
        assert r["primary_tone"] == "neutral"
        assert r["core_claim"] == "fallback_title"
        assert r["supporting_evidence"] == []

    def test_supporting_evidence_truncated(self):
        r = self._apply_parsing({"supporting_evidence": ["a", "b", "c", "d", "e"]})
        assert len(r["supporting_evidence"]) == 3

    def test_named_entities_truncated(self):
        entities = [{"text": f"entity_{i}", "type": "company"} for i in range(30)]
        r = self._apply_parsing({"named_entities": entities})
        assert len(r["named_entities"]) == 20


# ══════════════════════════════════════════════════════════════════════════════
# 4. ESG THEME TAGGER — _validate_theme, _validate_sub_metrics, ESGThemeTags
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateTheme:
    """Tests for _validate_theme in esg_theme_tagger.py."""

    def _validate(self, theme):
        from backend.services.esg_theme_tagger import _validate_theme
        return _validate_theme(theme)

    def test_exact_match(self):
        assert self._validate("Energy") == "Energy"

    def test_case_insensitive(self):
        result = self._validate("energy")
        assert result == "Energy"

    def test_invalid_theme(self):
        assert self._validate("Nonexistent") is None

    def test_empty_string(self):
        assert self._validate("") is None

    def test_partial_match_fails(self):
        assert self._validate("Ener") is None


class TestValidateSubMetrics:
    """Tests for _validate_sub_metrics in esg_theme_tagger.py."""

    def _validate(self, theme, sub_metrics):
        from backend.services.esg_theme_tagger import _validate_sub_metrics
        return _validate_sub_metrics(theme, sub_metrics)

    def test_valid_sub_metrics(self):
        result = self._validate("Energy", ["renewable_energy_use", "energy_intensity"])
        assert result == ["renewable_energy_use", "energy_intensity"]

    def test_invalid_sub_metric_filtered(self):
        result = self._validate("Energy", ["renewable_energy_use", "fake_metric", "energy_intensity"])
        assert "fake_metric" not in result
        assert len(result) == 2

    def test_wrong_theme_sub_metric(self):
        """Sub-metric from Water should not pass for Energy."""
        result = self._validate("Energy", ["water_withdrawal"])
        assert result == []

    def test_non_string_filtered(self):
        result = self._validate("Energy", [123, None, "renewable_energy_use", True])
        assert result == ["renewable_energy_use"]

    def test_empty_list(self):
        result = self._validate("Energy", [])
        assert result == []

    def test_unknown_theme(self):
        result = self._validate("FakeTheme", ["anything"])
        assert result == []


class TestESGThemeTagsDataclass:
    """Tests for ESGThemeTags construction and to_dict."""

    def test_basic_construction(self):
        from backend.services.esg_theme_tagger import ESGThemeTags
        tags = ESGThemeTags(
            primary_theme="Emissions",
            primary_pillar="Environmental",
            primary_sub_metrics=["scope_1_direct"],
            secondary_themes=[],
            confidence=0.85,
            method="llm",
        )
        assert tags.primary_theme == "Emissions"
        assert tags.confidence == 0.85

    def test_to_dict(self):
        from backend.services.esg_theme_tagger import ESGThemeTags
        tags = ESGThemeTags(
            primary_theme="Energy",
            primary_pillar="Environmental",
        )
        d = tags.to_dict()
        assert d["primary_theme"] == "Energy"
        assert d["primary_pillar"] == "Environmental"
        assert "confidence" in d
        assert "method" in d

    def test_defaults(self):
        from backend.services.esg_theme_tagger import ESGThemeTags
        tags = ESGThemeTags(primary_theme="DEI", primary_pillar="Social")
        assert tags.primary_sub_metrics == []
        assert tags.secondary_themes == []
        assert tags.confidence == 0.0
        assert tags.method == "llm"


class TestInferThemesFromKeywords:
    """Tests for keyword-based fallback theme inference."""

    def _infer(self, title, content, esg_pillar=None):
        from backend.services.esg_theme_tagger import infer_themes_from_keywords
        return infer_themes_from_keywords(title, content, esg_pillar)

    def test_emissions_keywords(self):
        result = self._infer("Company reduces carbon emissions", "Carbon offset credits purchased. GHG reduction targets met.")
        assert result is not None
        assert result.primary_theme == "Emissions"
        assert result.method == "keyword_fallback"

    def test_no_keywords_no_pillar(self):
        result = self._infer("Untitled", "No relevant content here at all xyz123")
        assert result is None

    def test_no_keywords_with_pillar_hint(self):
        result = self._infer("Unknown topic", "Nothing relevant.", esg_pillar="E")
        assert result is not None
        assert result.primary_pillar == "Environmental"
        assert result.confidence == 0.1

    def test_multiple_themes_detected(self):
        result = self._infer(
            "Water pollution from waste at factory",
            "The waste water discharge caused water contamination. Hazardous waste found. Recycling rate dropped.",
        )
        assert result is not None
        assert len(result.secondary_themes) > 0

    def test_unicode_content(self):
        result = self._infer("₹500 Crore carbon tax", "The company must pay ₹500 crore in carbon emission penalties")
        assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# 5. REREACT ENGINE — validator JSON structure
# ══════════════════════════════════════════════════════════════════════════════


class TestRereactValidatorParsing:
    """Tests for REREACT validator JSON parsing (post json.loads validation)."""

    def _validate_structure(self, data) -> dict:
        """Replicate the validation from rereact_engine.py lines 331-333."""
        if not isinstance(data, dict) or "validated_recommendations" not in data:
            return {"validated_recommendations": [], "rejected": [], "validation_summary": "Invalid validator response"}
        return data

    def test_valid_structure(self):
        data = {
            "validated_recommendations": [
                {"type": "compliance", "title": "File BRSR", "confidence": "HIGH"}
            ],
            "rejected": ["Vague recommendation"],
            "validation_summary": "1 of 2 passed validation.",
        }
        result = self._validate_structure(data)
        assert len(result["validated_recommendations"]) == 1
        assert result["validation_summary"] == "1 of 2 passed validation."

    def test_missing_validated_recommendations(self):
        result = self._validate_structure({"other_key": "value"})
        assert result["validated_recommendations"] == []
        assert result["validation_summary"] == "Invalid validator response"

    def test_not_a_dict(self):
        result = self._validate_structure([1, 2, 3])
        assert result["validated_recommendations"] == []

    def test_none_input(self):
        result = self._validate_structure(None)
        assert result["validated_recommendations"] == []

    def test_string_input(self):
        result = self._validate_structure("just a string")
        assert result["validated_recommendations"] == []

    def test_empty_recommendations_valid(self):
        data = {"validated_recommendations": [], "rejected": [], "validation_summary": "No recommendations survived."}
        result = self._validate_structure(data)
        assert result["validated_recommendations"] == []
        assert result["validation_summary"] == "No recommendations survived."

    def test_extra_fields_preserved(self):
        data = {
            "validated_recommendations": [{"title": "Test"}],
            "rejected": [],
            "validation_summary": "OK",
            "debug_info": "extra",
        }
        result = self._validate_structure(data)
        assert result["debug_info"] == "extra"


# ══════════════════════════════════════════════════════════════════════════════
# 6. RISK SPOTLIGHT — JSON validation
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskSpotlightParsing:
    """Tests for risk spotlight JSON validation logic."""

    def _validate_risks(self, raw_json: str) -> dict | None:
        """Replicate the parsing from risk_spotlight.py lines 61-96."""
        from backend.services.risk_spotlight import CLASSIFICATION_ORDER
        raw = raw_json.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            risks = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(risks, list):
            return None

        valid_risks = []
        for r in risks[:3]:
            if not isinstance(r, dict):
                continue
            name = r.get("category_name", "")
            cls = r.get("classification", "LOW").upper()
            if cls not in CLASSIFICATION_ORDER:
                cls = "LOW"
            valid_risks.append({
                "category_name": name,
                "classification": cls,
                "rationale": r.get("rationale", ""),
            })

        valid_risks.sort(key=lambda x: CLASSIFICATION_ORDER.get(x["classification"], 2))
        return {"mode": "spotlight", "top_risks": valid_risks} if valid_risks else None

    def test_valid_array(self):
        raw = json.dumps([
            {"category_name": "Regulatory Risk", "classification": "HIGH", "rationale": "New regs"},
            {"category_name": "Physical Risk", "classification": "MODERATE", "rationale": "Flood risk"},
            {"category_name": "Transition Risk", "classification": "LOW", "rationale": "Minor"},
        ])
        result = self._validate_risks(raw)
        assert result is not None
        assert len(result["top_risks"]) == 3
        assert result["top_risks"][0]["classification"] == "HIGH"  # sorted first

    def test_markdown_fenced(self):
        inner = json.dumps([
            {"category_name": "Physical Risk", "classification": "HIGH", "rationale": "Test"},
        ])
        raw = f"```json\n{inner}\n```"
        result = self._validate_risks(raw)
        assert result is not None

    def test_malformed_json(self):
        result = self._validate_risks('[{"category_name": "Physical Risk"')
        assert result is None

    def test_empty_string(self):
        result = self._validate_risks("")
        assert result is None

    def test_dict_instead_of_array(self):
        result = self._validate_risks('{"risk": "physical"}')
        assert result is None

    def test_invalid_classification_defaults_low(self):
        raw = json.dumps([
            {"category_name": "Test", "classification": "EXTREME", "rationale": "Bad"},
        ])
        result = self._validate_risks(raw)
        assert result is not None
        assert result["top_risks"][0]["classification"] == "LOW"

    def test_non_dict_items_skipped(self):
        raw = json.dumps(["string_item", 42, {"category_name": "Physical Risk", "classification": "HIGH", "rationale": "OK"}])
        result = self._validate_risks(raw)
        assert result is not None
        assert len(result["top_risks"]) == 1

    def test_missing_fields_defaults(self):
        raw = json.dumps([{}])
        result = self._validate_risks(raw)
        assert result is not None
        assert result["top_risks"][0]["category_name"] == ""
        assert result["top_risks"][0]["classification"] == "LOW"
        assert result["top_risks"][0]["rationale"] == ""

    def test_truncates_to_3(self):
        raw = json.dumps([
            {"category_name": f"Risk {i}", "classification": "HIGH", "rationale": "X"}
            for i in range(10)
        ])
        result = self._validate_risks(raw)
        assert result is not None
        assert len(result["top_risks"]) == 3


# ══════════════════════════════════════════════════════════════════════════════
# 7. ENTITY EXTRACTOR — _safe_float, _safe_enum, normalize_framework
# ══════════════════════════════════════════════════════════════════════════════


class TestSafeFloat:
    """Tests for _safe_float in entity_extractor.py."""

    def _safe_float(self, value, min_val, max_val):
        from backend.ontology.entity_extractor import _safe_float
        return _safe_float(value, min_val, max_val)

    def test_normal_float(self):
        assert self._safe_float(0.5, 0.0, 1.0) == 0.5

    def test_clamp_above_max(self):
        assert self._safe_float(5.0, 0.0, 1.0) == 1.0

    def test_clamp_below_min(self):
        assert self._safe_float(-2.0, 0.0, 1.0) == 0.0

    def test_integer_input(self):
        assert self._safe_float(1, 0.0, 1.0) == 1.0

    def test_string_number(self):
        assert self._safe_float("0.7", 0.0, 1.0) == 0.7

    def test_string_invalid(self):
        assert self._safe_float("abc", 0.0, 1.0) is None

    def test_none(self):
        assert self._safe_float(None, 0.0, 1.0) is None

    def test_list(self):
        assert self._safe_float([1, 2], 0.0, 1.0) is None

    def test_bool(self):
        # bool is numeric: float(True) == 1.0
        assert self._safe_float(True, 0.0, 1.0) == 1.0


class TestSafeEnum:
    """Tests for _safe_enum in entity_extractor.py."""

    def _safe_enum(self, value, valid):
        from backend.ontology.entity_extractor import _safe_enum
        return _safe_enum(value, valid)

    def test_valid_value(self):
        assert self._safe_enum("high", {"critical", "high", "medium", "low"}) == "high"

    def test_case_normalised(self):
        assert self._safe_enum("HIGH", {"critical", "high", "medium", "low"}) == "high"

    def test_with_whitespace(self):
        assert self._safe_enum("  high  ", {"critical", "high", "medium", "low"}) == "high"

    def test_invalid_value(self):
        assert self._safe_enum("extreme", {"critical", "high", "medium", "low"}) is None

    def test_none(self):
        assert self._safe_enum(None, {"critical", "high"}) is None

    def test_integer(self):
        assert self._safe_enum(123, {"critical", "high"}) is None

    def test_empty_string(self):
        assert self._safe_enum("", {"critical", "high"}) is None


class TestNormalizeFramework:
    """Tests for normalize_framework in entity_extractor.py."""

    def _normalize(self, name):
        from backend.ontology.entity_extractor import normalize_framework
        return normalize_framework(name)

    def test_known_alias(self):
        assert self._normalize("Task Force on Climate-Related Financial Disclosures") == "TCFD"

    def test_known_alias_lowercase(self):
        assert self._normalize("global reporting initiative") == "GRI"

    def test_brsr_alias(self):
        assert self._normalize("Business Responsibility and Sustainability Report") == "BRSR"

    def test_cdp(self):
        assert self._normalize("carbon disclosure project") == "CDP"

    def test_unknown_uppercased(self):
        assert self._normalize("some new framework") == "SOME_NEW_FRAMEWORK"

    def test_direct_code(self):
        assert self._normalize("GRI") == "GRI"

    def test_whitespace_handling(self):
        assert self._normalize("  BRSR  ") == "BRSR"

    def test_empty_string(self):
        assert self._normalize("") == ""

    def test_ifrs_s2(self):
        assert self._normalize("ifrs s2") == "IFRS_S2"

    def test_sbti(self):
        assert self._normalize("science based targets initiative") == "SBTi"


# ══════════════════════════════════════════════════════════════════════════════
# 8. NON-ENGLISH DETECTION
# ══════════════════════════════════════════════════════════════════════════════


class TestIsNonEnglish:
    """Tests for _is_non_english heuristic in nlp_pipeline.py."""

    def _check(self, text):
        from backend.services.nlp_pipeline import _is_non_english
        return _is_non_english(text)

    def test_english_text(self):
        assert self._check("This is a perfectly normal English sentence.") is False

    def test_hindi_text(self):
        assert self._check("यह एक हिंदी वाक्य है जो काफी लंबा है और इसमें बहुत सारे अक्षर हैं") is True

    def test_chinese_text(self):
        assert self._check("这是一个中文句子，包含足够多的中文字符来触发检测") is True

    def test_empty_string(self):
        assert self._check("") is False

    def test_mixed_mostly_english(self):
        assert self._check("Company announced ₹500 crore investment in new plant") is False

    def test_numbers_only(self):
        assert self._check("123456789 9876543210") is False


# ══════════════════════════════════════════════════════════════════════════════
# 9. MARKDOWN FENCE STRIPPING (shared pattern across all modules)
# ══════════════════════════════════════════════════════════════════════════════


class TestMarkdownFenceStripping:
    """Tests for the common markdown fence stripping pattern used across all services."""

    def _strip(self, raw: str) -> str:
        """Common pattern used in all modules."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return raw

    def test_json_fence(self):
        assert self._strip('```json\n{"key": "value"}\n```') == '{"key": "value"}'

    def test_plain_fence(self):
        assert self._strip('```\n{"key": "value"}\n```') == '{"key": "value"}'

    def test_no_fence(self):
        assert self._strip('{"key": "value"}') == '{"key": "value"}'

    def test_fence_with_extra_whitespace(self):
        result = self._strip('  ```json\n{"a": 1}\n```  ')
        assert json.loads(result) == {"a": 1}

    def test_nested_backticks_in_content(self):
        """If content itself contains backticks (rare but possible)."""
        raw = '```json\n{"code": "use `var` here"}\n```'
        result = self._strip(raw)
        data = json.loads(result)
        assert data["code"] == "use `var` here"

    def test_only_opening_fence(self):
        """Only opening fence, no closing — should still attempt strip."""
        raw = '```json\n{"key": "value"}'
        result = self._strip(raw)
        # split("\n", 1)[-1] gives '{"key": "value"}', rsplit("```", 1)[0] keeps it
        assert json.loads(result) == {"key": "value"}


# ══════════════════════════════════════════════════════════════════════════════
# 10. DEEP INSIGHT GENERATOR — field validation patterns
# ══════════════════════════════════════════════════════════════════════════════


class TestDeepInsightValidation:
    """Tests for the validation pattern in deep_insight_generator.py (post json.loads)."""

    def _validate(self, raw_json: str) -> dict | None:
        """Replicate validation from deep_insight_generator.py lines 168-169."""
        raw = raw_json.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            insight = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(insight, dict) or "core_mechanism" not in insight:
            return None
        return insight

    def test_valid_insight(self):
        raw = json.dumps({
            "headline": "Test Impact",
            "impact_score": 7.5,
            "core_mechanism": "Structural shift in regulatory framework.",
            "translation": "This means higher costs for the company.",
        })
        result = self._validate(raw)
        assert result is not None
        assert result["impact_score"] == 7.5

    def test_missing_core_mechanism(self):
        raw = json.dumps({
            "headline": "Test Impact",
            "impact_score": 5.0,
        })
        result = self._validate(raw)
        assert result is None

    def test_malformed_json(self):
        result = self._validate('{"headline": "broken')
        assert result is None

    def test_empty_string(self):
        result = self._validate("")
        assert result is None

    def test_not_a_dict(self):
        result = self._validate(json.dumps([1, 2, 3]))
        assert result is None

    def test_markdown_fenced(self):
        inner = json.dumps({"core_mechanism": "Test shift", "headline": "X"})
        result = self._validate(f"```json\n{inner}\n```")
        assert result is not None
        assert result["core_mechanism"] == "Test shift"

    def test_extra_fields_preserved(self):
        raw = json.dumps({
            "core_mechanism": "Shift",
            "headline": "X",
            "unknown_future_field": "preserved",
        })
        result = self._validate(raw)
        assert result is not None
        assert result["unknown_future_field"] == "preserved"

    def test_unicode_in_mechanism(self):
        raw = json.dumps({
            "core_mechanism": "₹500 Crore impact on 新能源 sector",
            "headline": "Test 🌍",
        })
        result = self._validate(raw)
        assert result is not None
        assert "₹500" in result["core_mechanism"]
        assert "新能源" in result["core_mechanism"]
