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

const BASE = "/api";

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers as Record<string, string> ?? {}),
  };

  const res = await fetch(`${BASE}${path}`, { ...options, headers });

  if (res.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }

  return res.json();
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
  list: async (params?: { limit?: number; offset?: number; company_id?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.company_id) q.set("company_id", params.company_id);
    const res = await request<{ articles: Article[]; total: number }>(`/news/feed?${q}`);
    return res.articles;
  },

  stats: () =>
    request<{ total: number; high_impact: number; predictions: number; new_today: number }>("/news/stats"),

  bookmark: (articleId: string) =>
    request<{ status: string }>(`/news/${articleId}/bookmark`, { method: "POST" }),
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

// ---- Agent Chat ----
export const agent = {
  chat: (question: string, agent_id?: string, conversation_id?: string) =>
    request<ChatResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ question, agent_id, conversation_id }),
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
