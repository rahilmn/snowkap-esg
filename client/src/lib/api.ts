import type {
  Article,
  ChatResponse,
  AgentInfo,
  Company,
  LoginResponse,
  OntologyStats,
  PredictionDetail,
  PredictionReport,
  PredictionStats,
  ResolveDomainResponse,
  TenantSummary,
  UsageStats,
  UserSummary,
} from "@/types";
import { getToken } from "@/stores/authStore";

const BASE = "/api";

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers as Record<string, string> ?? {}),
  };
  // Only set Content-Type when there's a body (not for GET requests)
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const res = await fetch(`${BASE}${path}`, { ...options, headers, signal: controller.signal });

    if (res.status === 401) {
      const { logout } = (await import("@/stores/authStore")).useAuthStore.getState();
      logout();
      window.location.href = "/login";
      throw new Error("Session expired");
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const detail = body.detail;
      const message = typeof detail === "string" ? detail
        : typeof detail === "object" && detail !== null ? JSON.stringify(detail)
        : `Request failed: ${res.status}`;
      throw new Error(message);
    }

    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

// ---- Auth ----
export const auth = {
  resolveDomain: (domain: string) =>
    request<ResolveDomainResponse>("/auth/resolve-domain", {
      method: "POST",
      body: JSON.stringify({ domain }),
    }),

  login: (data: {
    email: string;
    domain: string;
    designation: string;
    company_name: string;
    name: string;
  }) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  returningUser: (email: string) =>
    request<LoginResponse>("/auth/returning-user", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
};

// ---- Companies ----
export const companies = {
  list: async (limit = 50) => {
    const res = await request<{ companies: Company[]; total: number }>(`/companies/?limit=${limit}`);
    return res.companies;
  },

  get: (id: string) =>
    request<Company>(`/companies/${id}`),
};

// ---- News ----
export const news = {
  list: async (params?: {
    limit?: number;
    offset?: number;
    company_id?: string;
    sort_by?: string;
    pillar?: string;
    content_type?: string;
  }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.company_id) q.set("company_id", params.company_id);
    if (params?.sort_by) q.set("sort_by", params.sort_by);
    if (params?.pillar) q.set("pillar", params.pillar);
    if (params?.content_type) q.set("content_type", params.content_type);
    const res = await request<{ articles: Article[]; total: number }>(`/news/feed?${q}`);
    return res.articles;
  },

  stats: (companyId?: string) => {
    const q = companyId ? `?company_id=${encodeURIComponent(companyId)}` : "";
    return request<{ total: number; high_impact_count: number; predictions_count: number; new_last_24h: number }>(`/news/stats${q}`);
  },

  bookmark: (articleId: string) =>
    request<{ status: string }>(`/news/${articleId}/bookmark`, { method: "POST" }),

  refresh: () =>
    request<{ status: string; articles_fetched: number; articles_stored: number; sources: string[] }>(
      "/news/refresh",
      { method: "POST" }
    ),

  triggerAnalysis: (articleId: string) =>
    request<{ status: "triggered" | "already_running" | "cached"; message: string }>(
      `/news/${articleId}/trigger-analysis`,
      { method: "POST" }
    ),

  getAnalysisStatus: (articleId: string) =>
    request<{
      status: "done" | "pending" | "idle";
      analysis: {
        deep_insight: Record<string, unknown> | null;
        rereact_recommendations: Record<string, unknown> | null;
        risk_matrix: Record<string, unknown> | null;
        framework_matches: unknown[] | null;
        priority_score: number | null;
        priority_level: string | null;
      } | null;
    }>(`/news/${articleId}/analysis`),
};

// ---- Preferences (Phase 2D) ----
interface UserPreferenceData {
  preferred_frameworks: string[];
  preferred_pillars: string[];
  preferred_topics: string[];
  alert_threshold: number;
  content_depth: string;
  companies_of_interest: string[];
  dismissed_topics: string[];
}

export const preferences = {
  get: () => request<UserPreferenceData>("/preferences/"),
  update: (data: Partial<UserPreferenceData>) =>
    request<UserPreferenceData>("/preferences/", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  patch: (data: Partial<UserPreferenceData>) =>
    request<UserPreferenceData>("/preferences/", {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
};

// ---- Predictions ----
export const predictions = {
  list: (params?: { company_id?: string; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.company_id) q.set("company_id", params.company_id);
    if (params?.limit) q.set("limit", String(params.limit));
    return request<PredictionReport[]>(`/predictions/?${q}`);
  },

  get: (id: string) =>
    request<PredictionDetail>(`/predictions/${id}`),

  stats: () =>
    request<PredictionStats>("/predictions/stats"),

  trigger: (data: {
    article_id: string;
    company_id: string;
    causal_chain_id?: string;
  }) =>
    request<{ status: string; message: string }>("/predictions/trigger", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};

// ---- Ontology ----
export const ontology = {
  stats: () =>
    request<OntologyStats>("/ontology/stats"),

  sparql: (query: string) =>
    request<Record<string, unknown>>("/ontology/sparql", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  causalExplorer: (entity: string) =>
    request<Record<string, unknown>>("/ontology/explore", {
      method: "POST",
      body: JSON.stringify({ entity_text: entity }),
    }),
};

// ---- Campaigns ----
export interface CampaignItem {
  id: string;
  type: string;
  title: string;
  content: string;
  topic: string | null;
  status: string;
  frameworks_referenced: string[];
  articles_used: number;
  created_at: string | null;
}

export const campaigns = {
  list: (type?: string) =>
    request<{ campaigns: CampaignItem[]; total: number }>(`/campaigns/${type ? `?type=${type}` : ""}`),

  get: (id: string) =>
    request<CampaignItem>(`/campaigns/${id}`),

  generate: (type: string, topic?: string, frameworks?: string[]) =>
    request<CampaignItem>("/campaigns/generate", {
      method: "POST",
      body: JSON.stringify({ type, topic, frameworks }),
    }),

  delete: (id: string) =>
    request<void>(`/campaigns/${id}`, { method: "DELETE" }),
};

// ---- Agent Chat ----
export const agent = {
  chat: (question: string, agent_id?: string, conversation_id?: string, article_id?: string) =>
    request<ChatResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ question, agent_id, conversation_id, article_id }),
    }),

  askAboutNews: (article_id: string, question?: string) =>
    request<{
      response: string;
      agent: { id: string; name: string };
      causal_chains: Array<{
        id: string;
        source_entity: string;
        target_entity: string;
        relationship_type: string;
        hops: number;
        impact_score: number;
        explanation: string;
      }>;
      prediction_available: boolean;
      article_summary: Record<string, unknown>;
    }>("/agent/ask-about-news", {
      method: "POST",
      body: JSON.stringify({ article_id, question }),
    }),

  askAboutInsights: (
    article_id: string,
    company_id: string,
    message: string,
    conversation_history: Array<{ role: string; content: string }> = [],
    context_sections: string[] = ["recommendations", "framework_alignment", "financial_impact", "risk_matrix"],
  ) =>
    request<{ response: string; article_id: string }>(`/news/${article_id}/chat`, {
      method: "POST",
      body: JSON.stringify({ company_id, message, conversation_history, context_sections }),
    }),

  confirmAction: (action_id: string, conversation_id: string) =>
    request<{ status: string; result: Record<string, unknown> }>("/agent/confirm-action", {
      method: "POST",
      body: JSON.stringify({ action_id, conversation_id }),
    }),

  rejectAction: (action_id: string, conversation_id: string) =>
    request<{ status: string; action_id: string }>("/agent/reject-action", {
      method: "POST",
      body: JSON.stringify({ action_id, conversation_id }),
    }),

  listAgents: () =>
    request<AgentInfo[]>("/agent/agents"),

  history: (last_n = 20) =>
    request<{ messages: Record<string, unknown>[]; context_summary: string | null }>(
      `/agent/history?last_n=${last_n}`,
    ),

  clearHistory: () =>
    request<{ status: string }>("/agent/history", { method: "DELETE" }),
};

// ---- Admin ----
export const admin = {
  tenants: () =>
    request<TenantSummary[]>("/admin/tenants"),

  users: () =>
    request<UserSummary[]>("/admin/users"),

  usage: () =>
    request<UsageStats>("/admin/usage"),

  updateUserRole: (userId: string, role: string) =>
    request<UserSummary>(`/admin/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
};
