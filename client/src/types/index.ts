// ---- Auth ----
export interface ResolveDomainResponse {
  domain: string;
  company_name: string | null;
  industry: string | null;
  is_existing: boolean;
  tenant_id: string | null;
}

export interface LoginResponse {
  token: string;
  user_id: string;
  tenant_id: string;
  company_id: string | null;
  designation: string;
  permissions: string[];
  domain: string;
  name: string | null;
}

// ---- Companies ----
export interface Company {
  id: string;
  name: string;
  slug: string;
  domain: string | null;
  industry: string | null;
  sasb_category: string | null;
  status: string;
}

// ---- News ----
export interface Article {
  id: string;
  // Phase 28 — backend's build_legacy_article stamps company_id (the slug) on
  // every article row so the frontend can route chat / detail / share without
  // a separate /api/insights/{id}/meta call.
  company_id?: string | null;
  company_slug?: string | null;
  title: string;
  summary: string | null;
  source: string | null;
  url: string | null;
  image_url: string | null;
  published_at: string | null;
  esg_pillar: string | null;
  sentiment: string | null;
  entities: string[];
  impact_scores: ArticleScore[];
  predictions: ArticlePrediction[];
  frameworks: string[];
  framework_hits: FrameworkHit[];

  // Phase 1C: Enhanced sentiment + criticality
  sentiment_score: number | null;
  sentiment_confidence: number | null;
  aspect_sentiments: Record<string, number> | null;
  content_type: string | null;
  urgency: string | null;
  time_horizon: string | null;
  reversibility: string | null;
  priority_score: number | null;
  priority_level: string | null;
  financial_signal: {
    type: string;
    amount: number;
    currency: string;
    confidence: number;
  } | null;
  executive_insight: string | null;

  // Advanced Intelligence
  relevance_score: number | null;
  relevance_breakdown: Record<string, number> | null;
  deep_insight: Record<string, unknown> | null;
  scoring_metadata: Record<string, unknown> | null;
  rereact_recommendations: {
    validated_recommendations: Array<{
      type: string;
      title: string;
      description: string;
      framework?: string;
      framework_section?: string;
      responsible_party?: string;
      deadline?: string;
      estimated_budget?: string;
      success_criterion?: string;
      urgency: string;
      confidence: string;
      validation_notes?: string;
      profitability_link?: string;
      roi_percentage?: number;
      payback_months?: number;
      priority?: string;
    }>;
    rejected: string[];
    validation_summary: string;
    suggested_questions?: string[];
    recommendation_rankings?: Record<string, number[]>;
    priority_matrix?: Record<string, Array<{ index: number; title: string; type: string; roi?: number; budget?: string }>>;
    perspective_type_filters?: Record<string, string[]>;
  } | null;

  // v2.0 Intelligence Modules
  nlp_extraction: NlpExtraction | null;
  esg_themes: EsgThemes | null;
  framework_matches: FrameworkMatchV2[] | null;
  risk_matrix: RiskMatrix | null;
  geographic_signal: GeographicSignal | null;

  // Phase 12: Ontology-driven perspective views (CFO / CEO / ESG Analyst)
  perspectives?: Record<"cfo" | "ceo" | "esg-analyst", CrispView> | null;

  // Phase 1 — base criticality (objective article importance, 0..1)
  criticality_score?: number | null;
  criticality_band?: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | null;

  // Phase 51 — explicit /now deck tier. Namespaced `now_` to avoid colliding
  // with the unrelated `tier` string in news.liveAnalyze.
  now_tier?: "critical" | "light" | null;

  // Phase 6 — persona modulation (only present when ?personalise=true was sent)
  personalised_score?: number | null;
  /** True iff the article's topics have zero overlap with the caller's
   * persona.esg_focus. Renders an "Outside your focus" badge so the CXO
   * sees why a CRITICAL article they don't normally care about surfaced. */
  outside_focus?: boolean;
  persona_boost?: number;

  // Phase 3 §5.1 — structured Evidence Pack stamped at insight write time.
  // Read directly without rebuilding from raw pipeline state. Empty when
  // the writer couldn't assemble it (e.g. REJECTED article, builder failure).
  evidence_pack?: EvidencePack | null;

  // Phase 3 §5.2 — per-role RoleDistinctPayload for the 3 canonical roles.
  // Surfaces the role-distinct headline + hero metric + takeaways +
  // role-typed recommendations the Stage 11 dispatcher built from the
  // shared EvidencePack. Empty {} when the dispatcher couldn't build any
  // role (write-time failure or pre-Phase-3 article). The frontend SHOULD
  // prefer this over the legacy `perspectives` field once the LLM-prompt
  // swap lands and content quality justifies the visual surface change.
  role_payloads?: Record<RoleKey, RoleDistinctPayload>;

  // Phase 32 — unified 4-bullet analysis block. Single source of truth for
  // the article-detail UI: replaces role_payloads + perspectives over the
  // 1-release shim window. Renders via UnifiedAnalysisCard at the top of
  // ArticleDetailSheet; (i) icon on each bullet opens MethodologyDrawer
  // scoped to that bullet's methodology entry. Absent on pre-Phase-32
  // articles — frontend falls back to the legacy role view in that case.
  analysis?: UnifiedAnalysis | null;
}

// ---------------------------------------------------------------------------
// Phase 32 — UnifiedAnalysis: the 4-bullet news-flow brief that collapses
// the per-role view. Mirrors the Python composer in
// `engine/analysis/unified_analysis.py`.
// ---------------------------------------------------------------------------

export interface UnifiedAnalysisWhatChanged {
  headline: string;
  event_type: string;
  polarity: "positive" | "negative" | "neutral" | "";
  source: string;
  published_at: string;
  url: string;
}

export interface UnifiedFinancialExposure {
  amount_cr?: number | null;
  kind?: string;
  source?: string;
  label?: string;
}

export interface UnifiedAnalysisWhyItMatters {
  materiality_band: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "";
  materiality_weight: number | null;
  dominant_signal: string;
  criticality_summary: string;
  stakes_for_company: string;
  financial_exposure: UnifiedFinancialExposure;
  /** Phase 3 — "sasb_unmapped" when the company's sasb_category isn't in
   * the materiality TTL. UI degrades to a subdued chip. */
  warning: string | null;
}

export interface UnifiedFrameworkObligation {
  code: string;
  section: string;
  is_mandatory: boolean;
  deadline_days?: number;
}

export interface UnifiedRecommendedAction {
  title: string;
  deadline: string;
  owner: string;
  budget?: string | number | null;
  framework_section: string;
  type: string;
}

export interface UnifiedAnalysisWhatItTriggers {
  frameworks: UnifiedFrameworkObligation[];
  recommended_actions: UnifiedRecommendedAction[];
}

export interface UnifiedSentimentTrajectory {
  horizon_3m: string;
  horizon_6m: string;
  horizon_12m: string;
  confidence: string;
}

export interface UnifiedBenchmark {
  source: string;
  metric: string;
  value: string | number;
  as_of: string;
}

export interface UnifiedAnalysisWhatToWatch {
  sentiment_trajectory: UnifiedSentimentTrajectory | Record<string, never>;
  top_risk_categories: string[];
  lead_indicators: string[];
  /** Phase 4 — populated from `company_benchmarks` table. Hidden in the
   * UI when empty (DECISION 4.1). */
  benchmarks: UnifiedBenchmark[];
  next_decision_window: { label: string; by_date: string } | Record<string, never>;
}

export interface UnifiedMethodologyBlock {
  source: string;
  simple_logic: string;
  formula_human: string;
  ontology_anchors: string[];
  your_inputs: Record<string, unknown>;
}

export interface UnifiedAnalysisMethodology {
  what_changed: UnifiedMethodologyBlock;
  why_it_matters: UnifiedMethodologyBlock;
  what_it_triggers: UnifiedMethodologyBlock;
  what_to_watch: UnifiedMethodologyBlock;
}

// Phase 39 — editorial lede (2-3 sentence story-style opener that sits
// above the WHAT CHANGED bullet on the article-detail view + at the top
// of the morning_brew newsletter). Composed once at write time by
// engine.analysis.lede_writer; both surfaces read the same text.
export interface UnifiedAnalysisLede {
  text: string;
  pattern: string; // one of: character | contrast | temporal | setup_twist | reset | generic
  model_used?: string;
  cached?: boolean;
  char_count?: number;
  word_count?: number;
}

export interface UnifiedAnalysis {
  // Phase 39 — optional lede. Absent on pre-Phase-39 articles still at
  // schema 3.2 or earlier; renders nothing when missing.
  lede?: UnifiedAnalysisLede;
  what_changed: UnifiedAnalysisWhatChanged;
  why_it_matters: UnifiedAnalysisWhyItMatters;
  what_it_triggers: UnifiedAnalysisWhatItTriggers;
  what_to_watch: UnifiedAnalysisWhatToWatch;
  methodology: UnifiedAnalysisMethodology;
}

// ---------------------------------------------------------------------------
// Phase 3 §5.1/§5.2 — EvidencePack + RoleDistinctPayload (shared canonical
// block + per-role payload). Mirrors the Python dataclasses in
// `engine/analysis/evidence_pack.py` and `engine/analysis/role_generators/`.
// ---------------------------------------------------------------------------

export type RoleKey = "cfo" | "ceo" | "esg-analyst";
export type Polarity = "positive" | "negative" | "mixed" | "neutral";

export interface CascadeHop {
  source: string;
  target: string;
  beta?: number | null;
  lag_months?: number | null;
  delta_cr?: number | null;
  confidence?: string | null;
}

export interface CascadeBlock {
  total_cr: number;
  margin_bps?: number | null;
  dominant_lag_months?: number | null;
  hops: CascadeHop[];
  source_flag: string;
}

// NOTE: distinct from the pre-existing `FrameworkHit` (line 162) which has
// a different shape used by FrameworkComplianceMap. Prefix with `Evidence`
// to avoid the name collision.
export interface EvidenceFrameworkHit {
  code: string;
  name: string;
  rationale: string;
  region: string;
  is_mandatory: boolean;
}

export interface EvidenceStakeholder {
  name: string;
  stance: string;
  precedent: string;
}

export interface PainpointMatch {
  topic: string;
  similarity: number;
  severity: number;
  evidence: string;
}

export interface EvidenceCausalChain {
  hops: number;
  relationship_type: string;
  explanation: string;
  impact_score: number;
}

export interface PeerEvent {
  company: string;
  event_type: string;
  year?: number | null;
  polarity: Polarity;
  summary: string;
  citation: string;
}

export interface ConfidenceBoundsBlock {
  figure_lo_cr?: number | null;
  figure_hi_cr?: number | null;
  method: string;
  notes: string;
}

export interface DecisionWindow {
  label: string;
  deadline: string;
  severity: string;
}

export interface EvidencePack {
  cascade: CascadeBlock;
  frameworks: EvidenceFrameworkHit[];
  stakeholders: EvidenceStakeholder[];
  painpoint_matches: PainpointMatch[];
  causal_chain: EvidenceCausalChain;
  comparables: PeerEvent[];
  polarity: Polarity;
  confidence_bounds: ConfidenceBoundsBlock;
  decision_windows: DecisionWindow[];
}

export interface HeroMetric {
  value: string;
  label: string;
  decision_window: string;
  horizon: string;
  deadline: string;
}

export interface RecommendationStub {
  title: string;
  type: string;
  budget_cr?: number | null;
  payback_months?: number | null;
  framework_section: string;
}

export interface RoleDistinctPayload {
  role: RoleKey;
  headline: string;
  hero_metric: HeroMetric;
  role_takeaways: string[];
  role_paragraph: string;
  recommendations: RecommendationStub[];
  visible_panels: string[];
  hidden_panels: string[];
}

/** Bloomberg-style perspective view produced by the ontology-driven pipeline. */
export interface CrispView {
  perspective: "cfo" | "ceo" | "esg-analyst";
  headline: string;
  impact_grid: {
    financial: "HIGH" | "MEDIUM" | "LOW";
    regulatory: "HIGH" | "MEDIUM" | "LOW";
    strategic: "HIGH" | "MEDIUM" | "LOW";
  };
  what_matters: string[];
  action: string[];
  materiality: string;
  do_nothing: boolean;
  active_impact_dimensions: string[];
  full_insight?: Record<string, unknown> | null;
}

// v2.0 Module Types
export interface NlpExtraction {
  sentiment: { score: number; label: string };
  tone: { primary: string; secondary: string | null };
  narrative_arc: {
    core_claim: string;
    supporting_evidence: string[];
    implied_causation: string;
    stakeholder_framing: { protagonist?: string; antagonist?: string; affected?: string };
    temporal_framing: string;
  };
  source_credibility: { tier: number; rationale: string };
  esg_signals: {
    named_entities: Array<{ text: string; type: string }>;
    quantitative_claims: string[];
    regulatory_references: string[];
    supply_chain_references: string[];
  };
}

export interface EsgThemes {
  primary_theme: string;
  primary_pillar: string;
  primary_sub_metrics: string[];
  secondary_themes: Array<{
    theme: string;
    pillar: string;
    sub_metrics: string[];
  }>;
  confidence: number;
  method: string;
}

export interface FrameworkMatchV2 {
  framework_id: string;
  framework_name: string;
  triggered_sections: string[];
  triggered_questions?: string[];  // question-level citations e.g. ["Q14 (Water discharge)", "Q15 (Air emissions)"]
  compliance_implications: string[];
  alignment_notes: string[];
  relevance_score: number;
  cross_industry_metrics?: string[];
  profitability_link?: string;
  is_mandatory?: boolean;
}

export interface RiskCategory {
  category_id: string;
  category_name: string;
  probability: number;
  probability_label: string;
  exposure: number;
  exposure_label: string;
  risk_score: number;
  classification: string;
  rationale: string;
  industry_weight?: number;
  adjusted_score?: number;
  profitability_note?: string;
}

export interface RiskMatrix {
  categories: RiskCategory[];
  top_risks: RiskCategory[];
  total_score: number;
  aggregate_score: number;
}

export interface GeographicSignal {
  locations_detected?: string[];
  regulatory_jurisdictions?: Record<string, string[]>;
  supply_chain_overlap?: string;
  geo_risk_flags?: string[];
}

export interface UserPreference {
  preferred_frameworks: string[];
  preferred_pillars: string[];
  preferred_topics: string[];
  alert_threshold: number;
  content_depth: string;
  companies_of_interest: string[];
  dismissed_topics: string[];
}

export interface FrameworkHit {
  framework: string;
  indicator: string | null;
  indicator_name: string | null;
  relevance: number | null;
  explanation: string | null;
}

export interface ArticleScore {
  company_id: string;
  company_name: string;
  impact_score: number;
  causal_hops: number;
  relationship_type: string;
  explanation: string | null;
  financial_exposure: number | null;
  frameworks: string[];
  framework_hits: FrameworkHit[];
  // Causal chain data for visualization
  chain_path: Array<{ nodes?: string[]; edges?: string[] }> | null;
  confidence: number | null;
  framework_alignment: string[];
}

export interface ArticlePrediction {
  id: string;
  title: string;
  summary: string | null;
  prediction_text: string | null;
  confidence_score: number;
  financial_impact: number | null;
  time_horizon: string | null;
  risk_level: string | null;
  status: string;
}

export interface CausalChain {
  id: string;
  article_id: string;
  company_id: string;
  chain_path: string[];
  hops: number;
  impact_score: number;
  relationship_type: string;
  explanation: string;
  framework_alignment: string[];
}

// ---- Predictions ----
export interface PredictionReport {
  id: string;
  company_id: string;
  article_id: string | null;
  title: string;
  summary: string | null;
  prediction_text: string | null;
  confidence_score: number;
  financial_impact: number | null;
  time_horizon: string | null;
  scenario_variables: Record<string, unknown> | null;
  agent_consensus: AgentConsensus | null;
  status: string;
}

export interface AgentConsensus {
  analysis: string;
  recommendation: string;
  risk_level: string;
  opportunities: string[];
}

export interface PredictionDetail extends PredictionReport {
  causal_chain_id: string | null;
  simulation_runs: SimulationRun[];
}

export interface SimulationRun {
  id: string;
  agent_count: number;
  rounds: number;
  convergence_score: number;
  duration_seconds: number;
  status: string;
}

export interface PredictionStats {
  total_predictions: number;
  avg_confidence: number;
  high_risk_count: number;
  completed_count: number;
  pending_count: number;
}

// ---- Ontology ----
export interface OntologyStats {
  companies: number;
  facilities: number;
  suppliers: number;
  commodities: number;
  material_issues: number;
  frameworks: number;
  regulations: number;
  causal_chains: number;
}

export interface OntologyRule {
  id: string;
  name: string;
  rule_type: string;
  definition: Record<string, unknown>;
  is_active: boolean;
}

// ---- Agent Chat ----
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  agent?: AgentInfo;
  timestamp?: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  keywords?: string[];
  tools?: string[];
}

export interface ChatResponse {
  response: string;
  agent: { id: string; name: string };
  classification: Record<string, unknown>;
  tools_used: string[];
  pending_actions?: Array<{ id: string; type: string; description: string; resource: string; status: string }>;
}

// ---- Admin ----
export interface TenantSummary {
  id: string;
  name: string;
  domain: string;
  industry: string | null;
  is_active: boolean;
  user_count: number;
  created_at: string | null;
}

export interface UserSummary {
  id: string;
  email: string;
  name: string | null;
  designation: string | null;
  domain: string;
  role: string | null;
  permissions: string[];
  is_active: boolean;
  last_login: string | null;
}

export interface UsageStats {
  total_tenants: number;
  active_tenants: number;
  total_users: number;
  active_users_30d: number;
  total_articles: number;
  total_predictions: number;
  tenants_by_industry: Record<string, number>;
}
