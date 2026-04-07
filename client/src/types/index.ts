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
  } | null;

  // v2.0 Intelligence Modules
  nlp_extraction: NlpExtraction | null;
  esg_themes: EsgThemes | null;
  framework_matches: FrameworkMatchV2[] | null;
  risk_matrix: RiskMatrix | null;
  geographic_signal: GeographicSignal | null;
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
