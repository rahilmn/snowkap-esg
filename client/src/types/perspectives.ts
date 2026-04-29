/** TypeScript types for Phase 4 rich perspectives (ESG Analyst + CEO).
 *
 * Mirrors the backend dataclasses:
 *   - engine/analysis/esg_analyst_generator.py::ESGAnalystPerspective
 *   - engine/analysis/ceo_narrative_generator.py::CEONarrativePerspective
 *
 * Read via `GET /api/news/{id}/perspectives/{lens}` (already-deployed FastAPI route).
 */

// ---- ESG Analyst ----

export interface EsgAnalystKpi {
  kpi_name: string;
  company_value: string;
  unit?: string;
  peer_quartile?: string;
  peer_examples?: string;
  data_source?: string;
  significance?: string;
}

export interface EsgAnalystConfidenceBound {
  figure: string;
  source_type: "from_article" | "engine_estimate" | string;
  confidence?: "high" | "medium" | "low" | string;
  beta_range?: string;
  lag?: string;
  functional_form?: string;
  rationale?: string;
}

export interface EsgAnalystSdgTarget {
  code: string;
  title: string;
  applicability?: "direct" | "indirect" | "adjacent" | string;
  rationale?: string;
}

export interface EsgAnalystAuditTrailEntry {
  claim: string;
  derivation: string;
  sources: string[];
}

export interface EsgAnalystFrameworkCitation {
  code: string;
  rationale: string;
  region?: string;
  deadline?: string;
}

export interface EsgAnalystPerspective {
  headline: string;
  generated_by?: string;
  kpi_table: EsgAnalystKpi[];
  confidence_bounds: EsgAnalystConfidenceBound[];
  double_materiality: {
    financial_impact?: string;
    impact_on_world?: string;
  };
  tcfd_scenarios: {
    "1_5c"?: string;
    "2c"?: string;
    "4c"?: string;
  };
  sdg_targets: EsgAnalystSdgTarget[];
  audit_trail: EsgAnalystAuditTrailEntry[];
  framework_citations: EsgAnalystFrameworkCitation[];
  warnings: string[];
  full_insight?: Record<string, unknown> | null;
}

// ---- CEO Narrative ----

export interface CeoStakeholderEntry {
  stakeholder: string;
  stance: string;
  precedent: string;
}

export interface CeoAnalogousPrecedent {
  case_name?: string;
  company?: string;
  year?: string;
  cost?: string;
  duration?: string;
  outcome?: string;
  applicability?: string;
}

export interface CeoNarrativePerspective {
  headline: string;
  generated_by?: string;
  board_paragraph: string;
  stakeholder_map: CeoStakeholderEntry[];
  analogous_precedent: CeoAnalogousPrecedent;
  three_year_trajectory: {
    do_nothing?: string;
    act_now?: string;
  };
  qna_drafts: {
    earnings_call?: string;
    press_statement?: string;
    board_qa?: string;
    regulator_qa?: string;
  };
  warnings: string[];
}
