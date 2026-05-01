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
  UsageStats,
  UserSummary,
} from "@/types";
import { getToken } from "@/stores/authStore";

const BASE = "/api";

async function request<T>(
  path: string,
  options: RequestInit & { _timeout?: number } = {},
): Promise<T> {
  const token = getToken();
  const customTimeout = options._timeout;
  // Strip custom field before passing to fetch
  const { _timeout, ...fetchOptions } = options as RequestInit & { _timeout?: number };
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(fetchOptions.headers as Record<string, string> ?? {}),
  };
  // Only set Content-Type when there's a body (not for GET requests)
  if (fetchOptions.body) {
    headers["Content-Type"] = "application/json";
  }

  const controller = new AbortController();
  const timeoutMs = customTimeout || 60000;
  // Pass an explicit reason so the browser doesn't surface a noisy
  // "signal is aborted without reason" warning in the console.
  const timeout = setTimeout(
    () => controller.abort(new DOMException(`Request timed out after ${timeoutMs}ms`, "TimeoutError")),
    timeoutMs,
  );
  try {
    const res = await fetch(`${BASE}${path}`, { ...fetchOptions, headers, signal: controller.signal });

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
    // First-time onboarding kicks off a background pipeline write; the
    // synchronous handler still returns in <1s, but we give it a generous
    // timeout so a slow cold start can't surface as a Toast error.
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
      _timeout: 60000,
    } as RequestInit & { _timeout?: number }),

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
    return request<{
      total: number;
      high_impact_count: number;
      // Phase 13 B8: real count of HOME-tier high-impact articles in the
      // last 7 days. `predictions_count` is preserved as a back-compat
      // alias and now mirrors `active_signals_count`.
      active_signals_count?: number;
      predictions_count: number;
      new_last_24h: number;
    }>(`/news/stats${q}`);
  },

  bookmark: (articleId: string) =>
    request<{ status: string }>(`/news/${articleId}/bookmark`, { method: "POST" }),

  refresh: () =>
    request<{ status: string; articles_fetched: number; articles_stored: number; sources: string[] }>(
      "/news/refresh",
      { method: "POST" }
    ),

  triggerAnalysis: (articleId: string, force = false) =>
    request<{ status: "triggered" | "already_running" | "cached" | "done"; message: string }>(
      `/news/${articleId}/trigger-analysis${force ? "?force=true" : ""}`,
      { method: "POST", _timeout: 120000 } as RequestInit & { _timeout?: number }
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

  // Phase 9: one-click share an analyzed article to a recipient's email.
  // Name is auto-extracted for greeting ("ambalika.m@x.com" → "Ambalika").
  share: (articleId: string, payload: { recipient_email: string; sender_note?: string; read_more_base?: string }) =>
    request<{
      status: "sent" | "preview" | "failed";
      recipient: string;
      recipient_name: string | null;
      subject: string;
      article_id: string;
      company_slug: string;
      company_name: string;
      html_length: number;
      provider_id: string;
      error: string;
    }>(`/news/${articleId}/share`, {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" },
    }),

  sharePreview: (articleId: string, payload: { recipient_email: string; sender_note?: string; read_more_base?: string }) =>
    request<{
      status: "sent" | "preview" | "failed";
      recipient: string;
      recipient_name: string | null;
      subject: string;
      html: string;
      article_id: string;
      company_slug: string;
      company_name: string;
      error: string;
    }>(`/news/${articleId}/share/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" },
    }),
};

// ---- Campaigns (Phase 10: drip scheduler) ----

export type CampaignCadence = "once" | "weekly" | "monthly";
export type CampaignStatus = "active" | "paused" | "archived";
export type ArticleSelection = "latest_home" | "specific";
export type SendLogStatus = "sent" | "preview" | "failed" | "skipped_stale" | "skipped_dedup";

export interface CampaignRecipient {
  id?: string;
  campaign_id?: string;
  email: string;
  name_override?: string | null;
  last_sent_at?: string | null;
  created_at?: string;
}

export interface Campaign {
  id: string;
  name: string;
  created_by: string;
  template_type: string;
  target_company: string;
  article_selection: ArticleSelection;
  article_id: string | null;
  cadence: CampaignCadence;
  day_of_week: number | null;
  day_of_month: number | null;
  send_time_utc: string | null;
  cta_url: string | null;
  cta_label: string | null;
  sender_note: string | null;
  status: CampaignStatus;
  last_sent_at: string | null;
  next_send_at: string | null;
  created_at: string;
  updated_at: string;
  recipient_count?: number;
}

export interface SendLogEntry {
  id: string;
  campaign_id: string;
  recipient_email: string;
  article_id: string | null;
  subject: string | null;
  html_length: number | null;
  status: SendLogStatus;
  provider_id: string | null;
  error: string | null;
  sent_at: string;
}

export interface CampaignCreateInput {
  name: string;
  target_company: string;
  article_selection: ArticleSelection;
  article_id?: string | null;
  cadence: CampaignCadence;
  day_of_week?: number | null;
  day_of_month?: number | null;
  send_time_utc?: string | null;
  cta_url?: string | null;
  cta_label?: string | null;
  sender_note?: string | null;
  recipients: CampaignRecipient[];
  status?: CampaignStatus;
}

export interface CampaignPatchInput {
  name?: string;
  article_selection?: ArticleSelection;
  article_id?: string | null;
  cadence?: CampaignCadence;
  day_of_week?: number | null;
  day_of_month?: number | null;
  send_time_utc?: string | null;
  cta_url?: string | null;
  cta_label?: string | null;
  sender_note?: string | null;
}

export interface CampaignPreview {
  campaign_id: string;
  article_id: string;
  subject: string;
  recipient: string;
  recipient_name: string | null;
  html: string;
  html_length: number;
}

export const campaigns = {
  list: (status?: CampaignStatus) => {
    const q = status ? `?status=${status}` : "";
    return request<{ campaigns: Campaign[]; total: number }>(`/campaigns${q}`);
  },
  get: (id: string) => request<Campaign>(`/campaigns/${id}`),
  create: (body: CampaignCreateInput) =>
    request<Campaign>("/campaigns", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patch: (id: string, body: CampaignPatchInput) =>
    request<Campaign>(`/campaigns/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  delete: (id: string) =>
    request<void>(`/campaigns/${id}`, { method: "DELETE" }),
  sendNow: (id: string, dryRun = false) =>
    request<{ status: string; campaign_id: string; dry_run: boolean }>(
      `/campaigns/${id}/send-now?dry_run=${dryRun}`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  pause: (id: string) =>
    request<Campaign>(`/campaigns/${id}/pause`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  resume: (id: string) =>
    request<Campaign>(`/campaigns/${id}/resume`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  archive: (id: string) =>
    request<Campaign>(`/campaigns/${id}/archive`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  sendLog: (id: string, limit = 50) =>
    request<{ campaign_id: string; total: number; entries: SendLogEntry[] }>(
      `/campaigns/${id}/send-log?limit=${limit}`,
    ),
  replaceRecipients: (id: string, recipients: CampaignRecipient[]) =>
    request<{ campaign_id: string; total: number; recipients: CampaignRecipient[] }>(
      `/campaigns/${id}/recipients`,
      { method: "POST", body: JSON.stringify({ recipients }) },
    ),
  preview: (id: string) => request<CampaignPreview>(`/campaigns/${id}/preview`),
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

// (Legacy CampaignItem + campaigns export removed — Phase 10 replaces them.)

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

/** Phase 10: enriched tenant shape returned by /api/admin/tenants.
 * Used by CompanySwitcher (for super-admins) to list every tenant the product
 * has ever seen — the 7 hardcoded targets + every onboarded prospect. */
export interface AdminTenant {
  id: string;
  slug: string;
  name: string;
  domain?: string | null;
  industry?: string | null;
  source?: "target" | "onboarded";
  article_count?: number;
  last_analysis_at?: string | null;
}

export const admin = {
  /** Super-admin-only. Non-admin tokens get 403. Includes target companies
   * AND every onboarded domain that has logged in. */
  tenants: () => request<AdminTenant[]>("/admin/tenants"),

  users: () =>
    request<UserSummary[]>("/admin/users"),

  usage: () =>
    request<UsageStats>("/admin/usage"),

  updateUserRole: (userId: string, role: string) =>
    request<UserSummary>(`/admin/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  /**
   * Phase 16.1 — Admin onboarding. POST /api/admin/onboard accepts a new
   * company name + optional ticker hint, returns 202 + slug. Frontend
   * polls onboardStatus() every 5s while state ∈ {pending, fetching,
   * analysing} and shows a progress card.
   */
  onboard: (req: { name: string; ticker_hint?: string; domain?: string; limit?: number }) =>
    request<{ slug: string; status: string; message: string }>(
      "/admin/onboard",
      {
        method: "POST",
        body: JSON.stringify(req),
      }
    ),

  /** Phase 16.1 — Poll target after admin.onboard(). Returns the live row
   * from the onboarding_status SQLite table. */
  onboardStatus: (slug: string) =>
    request<{
      slug: string;
      state: "pending" | "fetching" | "analysing" | "ready" | "failed";
      fetched: number;
      analysed: number;
      home_count: number;
      started_at: string;
      finished_at: string | null;
      error: string | null;
    }>(`/admin/onboard/${slug}/status`),

  /**
   * Phase 13 B7 — Server-confirmed email backend liveness. Polled on
   * boot + after login so the Share button can gate on real configuration
   * state rather than on permission alone. Returns:
   *   { enabled: bool, sender: string, reason?: string }
   */
  emailConfigStatus: () =>
    request<{ enabled: boolean; sender: string; reason?: string }>(
      "/admin/email-config-status"
    ),

  /**
   * Phase 18 — Bulk reanalyze: bumps the schema_version on every article
   * for `slug` so the next user click triggers fresh on-demand enrichment
   * via stages 10-12. Idempotent. Use after engine version bumps.
   */
  reanalyzeCompany: (slug: string) =>
    request<{
      status: string;
      company_slug: string;
      invalidated: number;
      skipped: number;
      errors: number;
    }>(`/admin/companies/${slug}/reanalyze`, { method: "POST" }),

  /** Phase 18 — single-article version of reanalyzeCompany. Use for
   * "this article looks wrong" UX. */
  reanalyzeArticle: (articleId: string) =>
    request<{
      status: string;
      company_slug: string;
      article_id: string;
      invalidated: number;
    }>(`/admin/articles/${articleId}/reanalyze`, { method: "POST" }),
};
