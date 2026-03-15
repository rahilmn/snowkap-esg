// ---- Auth ----
export interface ResolveDomainResponse {
  domain: string;
  company_name: string | null;
  industry: string | null;
  is_existing: boolean;
  tenant_id: string | null;
}

export interface MagicLinkResponse {
  message: string;
  email: string;
}

export interface VerifyResponse {
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
  published_at: string | null;
  esg_pillar: string | null;
  sentiment: string | null;
  entities: string[];
  impact_scores: ArticleScore[];
  predictions: ArticlePrediction[];
  frameworks: string[];
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
