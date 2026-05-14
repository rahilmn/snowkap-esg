# Ontology ↔ code map

**Phase 24 (W2) audit-on-change registry.**

This file is a bidirectional registry: every consequential ontology
class / predicate is mapped to the SPARQL query function that consumes
it AND the analysis module that calls that query function. When you
edit a `.ttl` file under `data/ontology/`, find the changed entity in
the table below and check the propagation column to know what else may
need re-validation.

> **Why this exists.** Phase 15 made the ontology surface much larger
> by removing every hardcoded Python dict — every theme→risk map,
> headline rule, threshold, materiality weight, and ranking sort key
> now lives in TTL. A typo in a `.ttl` file can silently flip a
> HOME-tier article to REJECTED across all 7 companies. This map
> turns "what does this edit affect?" from detective work into a lookup.
>
> Edits to `data/ontology/*.ttl` should be logged via
> `engine.audit.append_edit()` with a Toulmin justification. The
> `/refine-ontology` skill (W4) and `POST /api/admin/discovery/decide`
> (W2) both write to `data/audit/ontology_edits.jsonl`.

---

## Quick navigation

- [Layer 1: Entity classes](#layer-1--entity)
- [Layer 2: ESG topics](#layer-2--esg-topics-21-themes)
- [Layer 3: Impact dimensions + perspectives](#layer-3--impact-dimensions--perspectives)
- [Layer 4: Frameworks](#layer-4--frameworks-21)
- [Layer 5: Risk + recommendation rules](#layer-5--risk--recommendation-rules-phase-15)
- [Layer 6: Causal primitives](#layer-6--causal-primitives-phase-17)
- [Layer 7: Precedents + stakeholders + KPIs](#layer-7--precedents--stakeholders--kpis-phase-14)
- [Layer 8: Toulmin warrants](#layer-8--toulmin-warrants-phase-24)
- [Self-evolving ontology](#self-evolving-ontology-phase-19)

---

## Layer 1 — Entity

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:Company` | `companies.ttl` (instances), `schema.ttl` (class) | `query_competitors` | `engine/analysis/insight_generator.py` (peer comparison block in user prompt) |
| `:Competitor` (subClassOf Company) | `companies.ttl` | `query_competitors` | `engine/analysis/insight_generator.py` |
| `:Industry` | `companies.ttl`, `schema.ttl` | `query_materiality_weight`, `query_risk_weight`, `query_industry_roi_benchmarks`, `query_esg_kpis_for_industry` | `engine/analysis/relevance_scorer.py`, `engine/analysis/risk_assessor.py`, `engine/analysis/recommendation_engine.py`, `engine/analysis/esg_analyst_generator.py` |
| `:Facility` | `companies.ttl` (sparse) | (none today) | reserved |
| `:GeographicRegion`, `:ClimateZone` | `companies.ttl`, `schema.ttl` | `query_climate_zones` | reserved (Phase 7 stub — geographic_intelligence) |
| `:CapitalizationTier` | `companies.ttl` | `query_cap_tier` | `engine/analysis/framework_matcher.py` (mandatory-rule routing) |

**Editing notes:**
- Adding a new company requires: new `:Company` instance + `:industry` + `:headquartersIn` + `:competesWith` triples in `companies.ttl`. Run `engine.ontology.seeder.seed_company` rather than hand-editing.
- New industries require materiality weights for every relevant `:ESGTopic` (otherwise relevance scoring returns 0 and articles get REJECTED).

---

## Layer 2 — ESG topics (21 themes)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:ESGTopic` (and `:EnvironmentalTopic`/`:SocialTopic`/`:GovernanceTopic`) | `knowledge_base.ttl` | `query_frameworks_for_topic`, `query_perspective_impacts`, `query_sdgs_for_topic`, `query_stakeholders_for_topic` | `engine/analysis/framework_matcher.py`, `engine/analysis/perspective_engine.py`, `engine/nlp/theme_tagger.py` |
| `:hasImpactOn` | `knowledge_base.ttl` | `query_perspective_impacts` | `engine/analysis/perspective_engine.py` |
| `:materialFor` (with weight) | `knowledge_base.ttl` | `query_materiality_weight` | `engine/analysis/relevance_scorer.py` |
| `:contributesToSDG` | `knowledge_base.ttl` | `query_sdgs_for_topic` | `engine/analysis/esg_analyst_generator.py` |

**Editing notes:**
- Adding a new theme: add the `:ESGTopic` instance + at least 5 `:materialFor` weights (one per industry) + theme→event mapping in `knowledge_depth.ttl` + theme→risk + theme→TEMPLES maps in `knowledge_expansion.ttl`. Without all five, the pipeline still runs but the new theme will never reach HOME tier.

---

## Layer 3 — Impact dimensions + perspectives

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:ImpactDimension` | `knowledge_base.ttl` | `query_perspective_impacts` | `engine/analysis/perspective_engine.py` |
| `:relevantTo` (Dim → Perspective) | `knowledge_base.ttl` | `query_perspective_impacts` | `engine/analysis/perspective_engine.py` |
| `:gridColumn` | `knowledge_expansion.ttl` | `query_grid_column_map` | `engine/analysis/perspective_engine.py` (`_build_impact_grid`) |
| `:insightKey` | `knowledge_expansion.ttl` | `query_dim_to_insight_keys` | `engine/analysis/perspective_engine.py` |
| `:PerspectiveLens` (CFO/CEO/ESG) | `schema.ttl` | `query_perspective_config` | `engine/analysis/perspective_engine.py` (word caps), `engine/analysis/ceo_narrative_generator.py`, `engine/analysis/esg_analyst_generator.py` |
| `:HeadlineRule` | `knowledge_expansion.ttl` | `query_headline_rules` | `engine/analysis/perspective_engine.py` (cascading template fill), `api/routes/legacy_adapter.py:auth_login` |
| `:RankingSortKey` | `knowledge_expansion.ttl` | `query_perspective_ranking_keys`, `query_perspective_rec_types` | `engine/analysis/recommendation_engine.py` |

**Editing notes:**
- HeadlineRules cascade by `:headlinePriority`. Lowest number tries first; first non-empty `:sourceField` value wins. Always include one `:isFallback true` rule per perspective so the cascade can never fall through to an empty headline.
- Editing `:gridColumn` re-shapes the CFO/CEO/ESG impact grid columns — verify in browser preview after change (the React `CrispInsight` component reads the `impact_grid` field directly).

---

## Layer 4 — Frameworks (21)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:ESGFramework` | `knowledge_base.ttl` | `query_frameworks_for_topic`, `query_frameworks_detail` | `engine/analysis/framework_matcher.py`, `engine/analysis/insight_generator.py` |
| `:triggersFramework` | `knowledge_base.ttl` | `query_frameworks_for_topic` | `engine/analysis/framework_matcher.py` |
| `:FrameworkSection` | `knowledge_base.ttl`, `framework_rationales.ttl` | `query_framework_sections`, `query_framework_rationales` | `engine/analysis/framework_matcher.py`, `engine/analysis/insight_generator.py` (rationale injection in verifier path) |
| `:RegionalFrameworkBoost` | `knowledge_expansion.ttl` | `query_regional_boosts` | `engine/analysis/framework_matcher.py` |
| `:MandatoryRule` (region × cap tier) | `knowledge_expansion.ttl` | `query_mandatory_rules` | `engine/analysis/framework_matcher.py` |
| `:ComplianceDeadline` | `knowledge_base.ttl` | `query_compliance_deadlines` | `engine/analysis/recommendation_engine.py`, `engine/analysis/persona_scorer.py` |

**Editing notes:**
- BRSR / CSRD / ESRS / SEC sections must use the official code (`BRSR:P5:Q12`, `ESRS:E1`, `GRI:303`). The matcher does substring matching, so non-canonical codes match nothing.
- New mandatory-rule combinations need a corresponding `:RegionalFrameworkBoost` entry or the framework will be flagged "mandatory but with 0 boost" — confusing to read.

---

## Layer 5 — Risk + recommendation rules (Phase 15)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:RiskCategory` | `knowledge_expansion.ttl` | `query_esg_risk_categories`, `query_risk_indicators` | `engine/analysis/risk_assessor.py` |
| `:TEMPLESCategory` | `knowledge_expansion.ttl` | `query_temples_categories` | `engine/analysis/risk_assessor.py` |
| `:triggersRiskCategory` (Topic → RiskCategory) | `knowledge_expansion.ttl` | `query_theme_risk_map` | `engine/analysis/risk_assessor.py` |
| `:triggersTEMPLES` (Topic → TEMPLESCategory) | `knowledge_expansion.ttl` | `query_theme_temples_map` | `engine/analysis/risk_assessor.py` |
| `:RiskLevelThreshold` | `knowledge_expansion.ttl` | `query_risk_level_thresholds` | `engine/analysis/risk_assessor.py` (`_classify_level`) |
| `:hasRiskWeight` (Industry × RiskCategory → float) | `knowledge_base.ttl` | `query_risk_weight` | `engine/analysis/risk_assessor.py`, `engine/analysis/relevance_scorer.py` |
| `:PriorityRule` (urgency × impact → priority) | `knowledge_expansion.ttl` | `query_priority_rules` | `engine/analysis/recommendation_engine.py` |
| `:RiskOfInactionConfig` | `knowledge_expansion.ttl` | `query_risk_of_inaction_config` | `engine/analysis/recommendation_engine.py` |

**Editing notes:**
- `_classify_level` is `@lru_cache`-d. After editing thresholds, restart the API so the cache picks up new values. The fuzz harness re-imports per run, so no flush needed there.
- Risk weight changes ripple into BOTH risk classification AND relevance scoring. Re-run `tests/test_phase12_blockers_5_6_7.py` after any edit.

---

## Layer 6 — Causal primitives (Phase 17)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:Primitive` (22 instances: OX/RV/CX/EU/GE/...) | `primitives_schema.ttl` | `query_primitives_for_event` | `engine/analysis/primitive_engine.py`, `engine/analysis/insight_generator.py` |
| `:CausalEdge` (P→P + P3/P4 chains) | `primitives_edges_p2p.ttl`, `primitives_order3.ttl` | `query_p2p_edges`, `query_cascade_context`, `query_feedback_loops` | `engine/analysis/primitive_engine.py` (β cascade computation) |
| `:OutcomeEdge` (P → outcome node) | `primitives_edges_p2p.ttl` | `query_cascade_context` | `engine/analysis/primitive_engine.py` |
| `:Indicator` | `primitives_indicators.ttl` | `query_indicators_for_primitive` (defined; see file) | `engine/analysis/recommendation_engine.py` (KPI surfacing) |
| `:Threshold` | `primitives_thresholds.ttl` | `query_thresholds_for_primitive` | `engine/analysis/recommendation_engine.py` |
| `:EventType` → `:hasPrimaryPrimitive` / `:hasSecondaryPrimitive` | `knowledge_depth.ttl` | `query_primitives_for_event` | `engine/analysis/primitive_engine.py` (cascade source primitive selection) |

**Editing notes:**
- β values on `:CausalEdge` instances are absolute — they're multiplied by the company's calibration ratio at runtime, not adjusted by it. Use the documented β ranges in PART 1/2.
- New event types need both `:hasPrimaryPrimitive` AND a `:eventTransmission` string OR they fall through to "general macro signal" framing.

---

## Layer 7 — Precedents + stakeholders + KPIs (Phase 14)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:PrecedentCase` | `precedents.ttl` | `query_precedents_for_event` | `engine/analysis/insight_generator.py`, `engine/analysis/ceo_narrative_generator.py` |
| `:StakeholderPosition` (with positive + negative variants) | `stakeholder_positions.ttl` | `query_stakeholder_positions` | `engine/analysis/ceo_narrative_generator.py` |
| `:KPI` | `kpis.ttl` | `query_esg_kpis_for_industry` | `engine/analysis/esg_analyst_generator.py` |
| `:ScenarioFraming` | `scenarios.ttl` | `query_scenario_framings` | `engine/analysis/ceo_narrative_generator.py` |
| `:SDGTarget` | `sdg_targets.ttl` | `query_sdg_targets` | `engine/analysis/esg_analyst_generator.py` |

**Editing notes:**
- `:StakeholderPosition` MUST carry both `:stakeholderDefaultStance`/`:stakeholderPrecedent` (negative variant) AND `:stakeholderPositiveStance`/`:stakeholderPositivePrecedent` (positive variant). Phase 15 polarity-aware SPARQL skips stakeholders missing the relevant variant — better to omit than emit wrong-polarity.

---

## Layer 8 — Toulmin warrants (Phase 24)

| Ontology entity | TTL file | SPARQL query function | Consumer module |
|---|---|---|---|
| `:NormativePrinciple` | `normative_principles.ttl` | `query_normative_principles_for_event` | `engine/analysis/toulmin_builder.py` (warrant text + citation) |
| `:appliesToEvent` | `normative_principles.ttl` | `query_normative_principles_for_event` | (same) |
| `:appliesToPolarity` | `normative_principles.ttl` | `query_normative_principles_for_event` | (same) |
| `:principleDomain` | `normative_principles.ttl` | (filter only) | (same) |

**Editing notes:**
- Adding a new principle requires choosing a polarity scope (`positive` / `negative` / `both`) and either an event-type whitelist OR no `:appliesToEvent` (which makes it cross-cutting). Cross-cutting principles surface as fallback warrants when no event-specific principle matches.
- `NP-MAT-003` (the do-nothing rebuttal-discipline rule) MUST stay in the file — without it, do-nothing verdicts have no warrant and cannot be defended at audit.

---

## Self-evolving ontology (Phase 19)

| Ontology entity | TTL file | Producer | Consumer |
|---|---|---|---|
| `:DiscoveredTriple` (subject) | `discovered.ttl` (runtime-written) | `engine/ontology/discovery/promoter.py` (auto-promotion + `manual_decide`) | `engine/ontology/intelligence.py` (queryable like authored triples) |
| `:discoveredFrom`, `:discoveredAt`, `:discoveryConfidence`, `:discoveryCategory`, `:discoveryStatus` | `discovered.ttl` | (provenance metadata) | admin discovery review at `/settings/discovery` (Phase 24 W2) |

**Editing notes:**
- Never hand-edit `discovered.ttl`. Use the W2 admin endpoint
  (`POST /api/admin/discovery/decide`) so the decision is logged with a
  Toulmin justification.
- Promotion-log auditing: `engine.audit.read_promotion_log()` reads
  `data/audit/promotion_log.jsonl` (newest entries first).

---

## Audit-on-change protocol

Whenever you edit any `data/ontology/*.ttl` file:

1. Identify the changed entity in the tables above. If it isn't listed,
   add a row first.
2. List the SPARQL query functions that consume it (column 3).
3. List the analysis modules that call those query functions (column 4).
4. For each module, decide: do existing tests cover this code path? If
   not, add a regression test in `tests/test_phaseNN_*.py` before merging.
5. Re-run the fuzz harness:
   `python scripts/fuzz_pipeline.py --slo-fail-pct 5`.
6. Log the edit via `engine.audit.append_edit()` with a Toulmin
   justification (claim/grounds/warrant minimum). The W4 `/refine-ontology`
   skill drives this; manual edits should call the helper from a one-off
   script.

This protocol decouples ontology engineers (Yoda / `/refine-ontology`)
from software engineers — Yoda flags Python files for engineer review
without auto-editing them; engineers approve before code lands.
