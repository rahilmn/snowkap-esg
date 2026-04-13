# Phase 15: Full Ontology Migration — Change Log

**Date:** 2026-04-12
**Goal:** Migrate all remaining hardcoded domain knowledge from Python dicts/if-else chains into OWL2 ontology triples with SPARQL queries. Push ontology coverage from ~40% to ~90%.

---

## Summary

| Metric | Before | After |
|--------|--------|-------|
| Ontology-driven stages | 7/12 (58%) | 9/12 (75%) + partial on remaining 3 |
| Hardcoded domain dicts in engine/ | 8 dicts, 152 lines | 0 dicts, 0 lines |
| SPARQL query functions | ~15 | ~28 (+13 new) |
| Ontology triples added | — | ~200 new triples |
| New OWL2 classes | — | 7 new classes |
| New predicates | — | ~20 new predicates |

---

## Files Modified

### 1. `data/ontology/schema.ttl` (+80 lines)

**New classes:**
- `RiskLevelThreshold` — score-to-level mapping (CRITICAL/HIGH/MODERATE/LOW)
- `RegionalFrameworkBoost` — region-specific framework relevance boosts
- `MandatoryRule` — region × cap-tier mandatory framework marking
- `PriorityRule` — urgency × impact → priority matrix
- `RiskOfInactionConfig` — base scores, type bonuses, escalation keywords
- `HeadlineRule` — perspective-specific headline templates with cascading priority
- `RankingSortKey` — per-perspective recommendation sort order

**New predicates:**
- `triggersRiskCategory` (ESGTopic → RiskCategory)
- `triggersTEMPLES` (ESGTopic → TEMPLESCategory)
- `riskLevel`, `minScore` (RiskLevelThreshold properties)
- `forRegion`, `boostValue`, `boostsFramework` (RegionalFrameworkBoost)
- `mandatoryFramework`, `mandatoryRegion`, `mandatoryCapTier` (MandatoryRule)
- `ifUrgency`, `ifImpact`, `thenPriority` (PriorityRule)
- `forPriority`, `baseRiskScore`, `recTypeBonus`, `forRecType`, `escalationKeyword` (RiskOfInactionConfig)
- `gridColumn`, `insightKey` (ImpactDimension data properties)
- `forPerspective`, `headlinePriority`, `sourceField`, `headlineTemplate`, `isFallback` (HeadlineRule)
- `sortKey`, `sortDirection`, `sortPriority` (RankingSortKey)

### 2. `data/ontology/knowledge_expansion.ttl` (+200 lines)

**Triples added:**
- 21 `triggersRiskCategory` mappings (each ESG topic → relevant risk categories)
- 21 `triggersTEMPLES` mappings (each ESG topic → relevant TEMPLES categories)
- 4 `RiskLevelThreshold` instances: CRITICAL≥20, HIGH≥12, MODERATE≥6, LOW≥0
- 14 `RegionalFrameworkBoost` instances:
  - India: BRSR +0.6, GRI/CDP/TCFD +0.1
  - EU: CSRD/ESRS +0.6, EU_TAXONOMY +0.5, SFDR +0.4
  - US: SEC_CLIMATE +0.6, SASB +0.4
  - Global: TCFD/GRI/CDP/ISSB +0.1
- 5 `MandatoryRule` instances:
  - India: BRSR for Large Cap
  - EU: CSRD, ESRS, EU_TAXONOMY for ALL
  - US: SEC_CLIMATE for ALL
- 12 `PriorityRule` instances (full urgency × impact matrix)
- Risk-of-inaction config: base scores (CRITICAL=8, HIGH=6, MEDIUM=4, LOW=2), type bonuses (compliance=+2, esg_positioning=+1), 7 escalation keywords
- 10 `gridColumn` mappings on `snowkap:impact_*` instances
- 10 `insightKey` mappings on `snowkap:impact_*` instances
- 7 `HeadlineRule` instances (4 CFO rules, 3 CEO rules) with templates
- 6 `RankingSortKey` instances (CFO: roi DESC → payback ASC; CEO: impact ASC → urgency ASC; ESG: type compliance-first → urgency ASC)

### 3. `engine/ontology/intelligence.py` (+300 lines)

**New dataclasses:**
- `RiskLevelThreshold(level: str, min_score: float)`
- `RegionalBoost(framework_id: str, boost_value: float)`
- `MandatoryRuleInfo(framework_id: str, region: str, cap_tier: str)`
- `PriorityRuleInfo(urgency: str, impact: str, priority: str)`
- `RiskOfInactionConfig(base_scores: dict, type_bonuses: dict, escalation_keywords: list)`
- `HeadlineRuleInfo(priority: int, source_field: str, template: str, is_fallback: bool)`
- `RankingSortKey(sort_key: str, sort_direction: str, priority: int)`

**New SPARQL query functions (13):**
- `query_esg_risk_categories()` → list of 10 ESG risk category labels
- `query_temples_categories()` → list of 7 TEMPLES category labels
- `query_theme_risk_map(theme)` → ESG risk categories triggered by theme
- `query_theme_temples_map(theme)` → TEMPLES categories triggered by theme
- `query_risk_level_thresholds()` → score→level thresholds (cached via `@lru_cache`)
- `query_regional_boosts(region)` → framework boost values per region
- `query_mandatory_rules(region)` → mandatory framework rules per region
- `query_priority_rules()` → urgency × impact → priority matrix (cached)
- `query_risk_of_inaction_config()` → base scores, type bonuses, keywords (cached)
- `query_grid_column_map()` → impact dimension → grid column name
- `query_dim_to_insight_keys()` → impact dimension → insight analysis keys
- `query_headline_rules(perspective)` → cascading headline templates per lens
- `query_perspective_ranking_keys(perspective)` → recommendation sort order per lens

### 4. `engine/analysis/risk_assessor.py` (rewritten)

**Removed:**
- `ESG_CATEGORIES` list (10 hardcoded items)
- `TEMPLES_CATEGORIES` list (7 hardcoded items)
- `_THEME_RISK_MAP` dict (21 entries, 23 lines)
- `_THEME_TEMPLES_MAP` dict (21 entries, 23 lines)
- `_classify_level()` if-else thresholds (4 branches)

**Replaced with:**
- `_classify_level()` → calls `query_risk_level_thresholds()`, iterates sorted thresholds
- `_build_llm_prompt()` → calls `query_esg_risk_categories()` and `query_temples_categories()`
- `assess_risk()` → calls `query_esg_risk_categories()` and `query_temples_categories()`
- `assess_risk_lite()` → calls `query_theme_risk_map()` and `query_theme_temples_map()`

### 5. `engine/analysis/framework_matcher.py` (rewritten)

**Removed:**
- `REGION_BOOSTS` dict (hardcoded framework → boost mappings per region)
- `MARKET_CAP_BRSR_MANDATORY` set (hardcoded cap tiers)

**Replaced with:**
- Regional boost: `query_regional_boosts(region_key)` → iterate and apply
- Mandatory marking: `query_mandatory_rules(region_key)` → iterate and check cap_tier
- Added `triggered_sections: list[str] = field(default_factory=list)` to `FrameworkMatch` dataclass (was set as attribute but missing from dataclass declaration, causing `asdict()` to skip it)

### 6. `engine/analysis/recommendation_engine.py` (rewritten)

**Removed:**
- `_derive_priority()` if-else chain (hardcoded urgency × impact → priority)
- `_compute_risk_of_inaction()` hardcoded base scores and type bonuses
- `_build_perspective_rankings()` hardcoded sort logic per perspective

**Replaced with:**
- `_derive_priority()` → calls `query_priority_rules()`, matches urgency + impact, falls back to urgency-only
- `_compute_risk_of_inaction()` → calls `query_risk_of_inaction_config()`, uses ontology base scores, type bonuses, and escalation keywords
- `_build_perspective_rankings()` → calls `query_perspective_ranking_keys()`, uses generic `_sort_value()` helper that handles roi_percentage, payback_months, estimated_impact, urgency, type fields

### 7. `engine/analysis/perspective_engine.py` (rewritten)

**Removed:**
- `GRID_COLUMN_MAP` dict (10 entries mapping dimension → grid column)
- `_DIM_TO_KEY` dict (10 entries mapping dimension → insight keys)
- Hardcoded CFO/CEO headline string concatenation (produced malformed text)

**Replaced with:**
- `_build_impact_grid()` → calls `query_grid_column_map()`
- `_extract_what_matters()` → calls `query_dim_to_insight_keys()`
- `_perspective_headline()` → completely rewritten:
  - New `_resolve_field(insight, dot_path)` helper resolves nested fields like `decision_summary.financial_exposure`
  - Calls `query_headline_rules(perspective)` for cascading template rules
  - Each rule has priority, source field, template with `{value}/{base}` placeholders, and fallback flag
  - Guaranteed distinct headlines per perspective (last-resort rule always prepends perspective prefix)

### 8. `api/routes/legacy_adapter.py` (headline section rewritten)

**Removed:**
- Hardcoded CFO headline cascade with string concatenation
- Hardcoded CEO headline cascade with string concatenation

**Replaced with:**
- `query_headline_rules(perspective)` with dot-path field resolution from insight dict
- Same template-based rendering as `perspective_engine.py`

### 9. `CLAUDE.md` (updated)

- Pipeline description: 7/12 → 9/12 ontology stages
- Ontology architecture: added Layer 6 (Risk & Recommendation Rules)
- Key predicates: added 20+ Phase 15 predicates
- New section: SPARQL Query Functions (complete inventory of all 28 queries)
- Critical Rules: expanded rule 1, added rules 11-12
- Phase 15 section with migration summary table
- "What NOT to Build" updated with Phase 15 verification note

---

## Bugs Fixed

### CFO headline concatenation
**Before:** `"₹10,000-15,000 Cr at risk financial exposure — ₹10,000-15,000 Cr at risk at stake —"`
**After:** `"₹10,000-15,000 Cr at stake — ICICI Bank faces ₹10,000-15,000 Cr GST demand"`
**Root cause:** Hardcoded string concatenation duplicated the value and appended "at stake" to already-complete text.
**Fix:** Template-based system: `"{value} — {base}"` where `{value}` is resolved from `decision_summary.financial_exposure`.

### FrameworkMatch.triggered_sections not serialized
**Before:** `triggered_sections` set as an attribute in `framework_matcher.py` but not declared in the dataclass → `asdict()` never included it → frontend never received it.
**After:** Added `triggered_sections: list[str] = field(default_factory=list)` to dataclass declaration.

### Stale docstring reference
**Before:** `risk_assessor.py` docstring referenced removed `_THEME_RISK_MAP`
**After:** Updated to reference `query_theme_risk_map()` SPARQL function.

---

## URI Mismatches Fixed During Implementation

1. **ImpactDimension URIs:** Triples used `snowkap:dim_financial` but actual instances are `snowkap:impact_financial`. Fixed 20 references.
2. **PerspectiveLens URIs:** Triples used `snowkap:perspective_cfo` but actual instances are `snowkap:lens_cfo`. Fixed all references.
3. **Framework URIs:** Triples used `snowkap:framework_brsr` but actual instances are `snowkap:BRSR`. Fixed 20+ references across boosts and mandatory rules.
