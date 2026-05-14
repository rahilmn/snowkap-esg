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
  // Only set Content-Type when there's a body (not for GET requests).
  // Phase 25 W6 — FormData uploads (multipart) MUST NOT set Content-Type
  // explicitly; the browser computes the boundary parameter and includes
  // it in its own Content-Type header. Setting it manually clobbers the
  // boundary and the server gets a 400.
  if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
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
    // `cache: 'no-store'` — API responses are always dynamic (auth, tenant
    // scope, freshness). Without this, the browser disk cache can serve a
    // stale response after a deploy that changed an endpoint's behaviour
    // (e.g. a new route that previously returned the SPA fallback HTML).
    // Caller can override via fetchOptions.cache when needed.
    const res = await fetch(`${BASE}${path}`, {
      cache: "no-store",
      ...fetchOptions,
      headers,
      signal: controller.signal,
    });

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

/**
 * Phase 22.3 — When `RESEND_API_KEY` is configured server-side, the
 * /auth/login + /auth/returning-user endpoints return this challenge
 * shape instead of a JWT. The client must collect the 6-digit code
 * the user receives by email and POST it to /auth/verify alongside
 * the same signup data to mint the token.
 */
export interface VerifyChallenge {
  step: "verify";
  email: string;
  expires_in: number;
}

function isVerifyChallenge(x: unknown): x is VerifyChallenge {
  return typeof x === "object" && x !== null && (x as { step?: unknown }).step === "verify";
}

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
    request<LoginResponse | VerifyChallenge>("/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
      _timeout: 60000,
    } as RequestInit & { _timeout?: number }),

  returningUser: (email: string) =>
    request<LoginResponse | VerifyChallenge>("/auth/returning-user", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  /**
   * Phase 22.3 — Step 2 of magic-link login. Submits the 6-digit OTP
   * + the same signup data the user entered in step 1 (so the JWT
   * carries name/company/designation just like the legacy single-step
   * flow). Server burns the OTP on success.
   */
  verify: (data: {
    email: string;
    code: string;
    name?: string;
    company_name?: string;
    domain?: string;
    designation?: string;
  }) =>
    request<LoginResponse>("/auth/verify", {
      method: "POST",
      body: JSON.stringify(data),
      _timeout: 60000,
    } as RequestInit & { _timeout?: number }),

  isVerifyChallenge,
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
    /** Phase 1.6 — surface gates the criticality floor:
     *  'home' (default 0.65) shows only CRITICAL/HIGH band
     *  'feed' (0.40) shows everything above MEDIUM-low
     *  'all'  (no floor) is the admin/debug full list
     */
    surface?: "home" | "feed" | "all";
    /** Phase 6 §8.3 — when true, the API re-ranks rows per the caller's
     *  stored persona and tags `outside_focus` for the UI badge. Default
     *  false so existing callers stay byte-identical to legacy behaviour.
     *  Discoverability invariant: never drops rows; CRITICAL articles
     *  remain visible regardless of persona match.
     */
    personalise?: boolean;
  }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.company_id) q.set("company_id", params.company_id);
    if (params?.sort_by) q.set("sort_by", params.sort_by);
    if (params?.pillar) q.set("pillar", params.pillar);
    if (params?.content_type) q.set("content_type", params.content_type);
    if (params?.surface) q.set("surface", params.surface);
    if (params?.personalise) q.set("personalise", "true");
    const res = await request<{ articles: Article[]; total: number }>(`/news/feed?${q}`);
    return res.articles;
  },

  /**
   * Phase 22.1 — Self-service onboarding progress for the caller's
   * own tenant. Used by HomePage + SwipeFeedPage to differentiate
   * "still onboarding" from "onboarding finished but found nothing"
   * so the empty-state copy isn't a permanent "Fetching..." spinner.
   * Unlike `admin.onboardStatus`, this endpoint is NOT super-admin
   * gated — backend enforces the same tenant-scope rules as /news/feed.
   */
  onboardingStatus: (companyId?: string) => {
    const q = companyId ? `?company_id=${encodeURIComponent(companyId)}` : "";
    return request<{
      slug: string | null;
      state: "pending" | "fetching" | "analysing" | "ready" | "failed";
      fetched: number;
      analysed: number;
      home_count: number;
      started_at: string | null;
      finished_at: string | null;
      error: string | null;
    }>(`/news/onboarding-status${q}`);
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

  /**
   * Phase 22.3 — Self-service retry for the caller's own onboarding.
   * Wipes the existing onboarding_status row, re-claims pending, and
   * schedules a fresh `_background_onboard` run against the JWT-bound
   * tenant slug. No body args — backend reads everything off the JWT.
   *
   * Returns 409 when an onboarding is already in flight (UI should
   * surface "still running, please wait"); 400 when the JWT lacks a
   * tenant scope (legacy token before Phase 22 — re-authenticate).
   */
  retryOnboarding: () =>
    request<{ status: "queued"; slug: string }>(
      "/news/onboarding-retry",
      { method: "POST", body: JSON.stringify({}) },
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
  // Phase 4 §6.4 — `role` is the sales-tool role toggle. Sent for audit
  // even when the backend's email body is currently role-agnostic so
  // the upgrade to per-role rendering is a backend-only change.
  share: (articleId: string, payload: {
    recipient_email: string;
    sender_note?: string;
    read_more_base?: string;
    role?: "cfo" | "ceo" | "analyst" | "esg-analyst";
  }) =>
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

  sharePreview: (articleId: string, payload: {
    recipient_email: string;
    sender_note?: string;
    read_more_base?: string;
    role?: "cfo" | "ceo" | "analyst" | "esg-analyst";
  }) =>
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

// ---------------------------------------------------------------------------
// Phase C — Stateful chat (persistent conversations + memory + MCP admin)
// ---------------------------------------------------------------------------

export interface ConversationSummary {
  conversation_id: string;
  tenant_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  last_message_at: string;
  message_count: number;
  archived_at: string | null;
}

export interface PersistentChatMessage {
  message_id: string;
  conversation_id: string;
  tenant_id: string;
  user_id: string | null;
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  toulmin: Record<string, unknown> | null;
  phase_k_tags: Record<string, unknown> | null;
  skill_invocations: unknown[];
  model_used: string | null;
  usage: Record<string, unknown> | null;
  finish_reason: string | null;
  created_at: string;
}

export interface MemoryRecord {
  memory_id: string;
  tenant_id: string;
  user_id: string | null;
  scope: "personal" | "shared";
  fact_kind: "fact" | "preference" | "decision" | "open_thread";
  content: string;
  confidence: number;
  created_at: string;
  last_accessed: string | null;
  access_count: number;
}

export const conversations = {
  list: (params: { include_archived?: boolean; limit?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.include_archived) query.set("include_archived", "true");
    if (params.limit) query.set("limit", String(params.limit));
    const qs = query.toString();
    return request<{ conversations: ConversationSummary[]; count: number }>(
      `/conversations${qs ? `?${qs}` : ""}`,
    );
  },
  get: (cid: string) =>
    request<{ summary: ConversationSummary; messages: PersistentChatMessage[] }>(
      `/conversations/${cid}`,
    ),
  rename: (cid: string, title: string) =>
    request<{ ok: boolean }>(`/conversations/${cid}/rename`, {
      method: "PATCH", body: JSON.stringify({ title }),
    }),
  archive: (cid: string) =>
    request<{ ok: boolean }>(`/conversations/${cid}/archive`, { method: "POST" }),
  delete: (cid: string) =>
    request<{ ok: boolean }>(`/conversations/${cid}`, { method: "DELETE" }),
  fork: (cid: string, upToMessageId?: string) =>
    request<{ conversation_id: string }>(`/conversations/${cid}/fork`, {
      method: "POST",
      body: JSON.stringify({ up_to_message_id: upToMessageId ?? null }),
    }),
  search: (q: string) =>
    request<{ hits: Record<string, unknown>[]; q: string; count: number }>(
      `/conversations/search?q=${encodeURIComponent(q)}`,
    ),
};

export const memory = {
  list: (limit = 50) =>
    request<{ memories: MemoryRecord[]; count: number }>(
      `/memory?limit=${limit}`,
    ),
  insert: (req: {
    content: string;
    scope?: "personal" | "shared";
    fact_kind?: "fact" | "preference" | "decision" | "open_thread";
    confidence?: number;
    source_conversation_id: string;
  }) =>
    request<{ memory: MemoryRecord }>("/memory", {
      method: "POST",
      body: JSON.stringify({
        scope: req.scope ?? "personal",
        fact_kind: req.fact_kind ?? "fact",
        confidence: req.confidence ?? 0.7,
        ...req,
      }),
    }),
  delete: (mid: string) =>
    request<void>(`/memory/${mid}`, { method: "DELETE" }),
  extract: (conversation_id: string) =>
    request<{
      conversation_id: string;
      extracted_count: number;
      memories: MemoryRecord[];
    }>(`/memory/extract/${conversation_id}`, { method: "POST" }),
};

export const mcp = {
  manifest: () => request<Record<string, unknown>>("/mcp/manifest"),
  tools: () => request<{ tools: Record<string, unknown>[]; smoke: Record<string, unknown> }>(
    "/mcp/tools",
  ),
  resources: () => request<{ resources: Record<string, unknown>[] }>("/mcp/resources"),
  invoke: (body: { tool: string; payload: Record<string, unknown>; signoff?: string }) =>
    request<{
      tool: string;
      state: "ok" | "signoff_required" | "error";
      result?: Record<string, unknown> | null;
      error?: Record<string, unknown> | null;
      signoff_phrase?: string | null;
      annotations?: Record<string, unknown> | null;
    }>("/mcp/invoke", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

/** Phase C — SSE chat. Opens an EventSource-like fetch stream and dispatches
 * each event line to the supplied callback. Returns an abort function. */
export function streamChat(
  req: { conversation_id: string | null; message: string; signoff?: string },
  onEvent: (event: string, data: Record<string, unknown>) => void,
  onError?: (err: unknown) => void,
): () => void {
  const controller = new AbortController();
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  void (async () => {
    try {
      const res = await fetch(`${BASE}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify(req),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        onError?.(new Error(`chat stream failed: HTTP ${res.status}`));
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line
        let split = buffer.indexOf("\n\n");
        while (split >= 0) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const lines = frame.split("\n");
          let eventName = "message";
          let dataStr = "";
          for (const line of lines) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          }
          if (dataStr) {
            try {
              onEvent(eventName, JSON.parse(dataStr));
            } catch {
              onEvent(eventName, { raw: dataStr });
            }
          }
          split = buffer.indexOf("\n\n");
        }
      }
    } catch (exc) {
      onError?.(exc);
    }
  })();
  return () => controller.abort();
}

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

/** W1 — response shape for /api/admin/tenants. `meta.warnings` carries
 * non-fatal degradations (Supabase blip, etc.) so the dropdown can render
 * a small "(degraded)" badge instead of going blank. */
export interface AdminTenantsResponse {
  companies: AdminTenant[];
  meta: { warnings: string[] };
}

export const admin = {
  /** Super-admin-only. Non-admin tokens get 403. Includes target companies
   * AND every onboarded domain that has logged in. */
  tenants: () => request<AdminTenantsResponse>("/admin/tenants"),

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
  onboard: (req: { name?: string; ticker_hint?: string; domain?: string; limit?: number }) =>
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
    request<OnboardStatus>(`/admin/onboard/${slug}/status`),

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

  // -------------------------------------------------------------------------
  // Phase 24 (W2) — self-evolving ontology review surface
  // -------------------------------------------------------------------------

  /** List staged (pending) discovery candidates. Optional category filter:
   * entity | theme | event | edge | weight | stakeholder | framework. */
  discoveryStaged: (category?: string, limit = 100) =>
    request<{
      count: number;
      by_category: Record<string, number>;
      candidates: Array<{
        candidate_id: string;
        category: string;
        label: string;
        slug: string;
        confidence: number;
        article_ids: string[];
        sources: string[];
        companies: string[];
        first_seen: string;
        last_seen: string;
        data: Record<string, unknown>;
        status: string;
      }>;
    }>(
      `/admin/discovery/staged?limit=${limit}${
        category ? `&category=${encodeURIComponent(category)}` : ""
      }`
    ),

  /** Apply a promote / reject / defer decision. Reject + defer require a
   * Toulmin block (claim + grounds[] + warrant minimum). */
  discoveryDecide: (req: {
    candidate_id: string;
    decision: "promote" | "reject" | "defer";
    toulmin?: {
      claim: string;
      grounds: string[];
      warrant: string;
      qualifier?: string;
      rebuttal?: string;
    };
  }) =>
    request<{
      ok: boolean;
      message: string;
      category: string | null;
      slug: string | null;
      decision: string | null;
      triples_added: number;
      new_status: string | null;
    }>("/admin/discovery/decide", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  /** Recent decisions from data/audit/promotion_log.jsonl (newest first). */
  discoveryHistory: (limit = 50, decision?: "promote" | "reject" | "defer") =>
    request<{
      count: number;
      entries: Array<{
        ts: string;
        decision: "promote" | "reject" | "defer";
        candidate_id: string;
        category: string;
        confidence?: number;
        candidate_payload: Record<string, unknown>;
        toulmin?: {
          claim: string;
          grounds: string[];
          warrant: string;
          qualifier?: string;
          rebuttal?: string;
        };
        user_id?: string;
        extra?: Record<string, unknown>;
      }>;
    }>(
      `/admin/discovery/history?limit=${limit}${
        decision ? `&decision=${decision}` : ""
      }`
    ),

  // -------------------------------------------------------------------------
  // Phase 25 W6 — batch onboarding from HubSpot CSV
  // -------------------------------------------------------------------------

  /** Dry-run: parse uploaded CSV + return roster + disambiguation flags WITHOUT enqueueing.
   * Phase 25 W6 — passes FormData; request() detects FormData and skips
   * the application/json Content-Type so the browser sets multipart with
   * the right boundary. */
  batchOnboardPreview: (csvFile: File) => {
    const fd = new FormData();
    fd.append("csv_file", csvFile);
    return request<BatchOnboardPreviewResponse>(
      "/admin/onboard/batch/preview",
      { method: "POST", body: fd },
    );
  },

  /** Commit: parse uploaded CSV AND enqueue every eligible row. */
  batchOnboardCommit: (csvFile: File, skipExisting = true) => {
    const fd = new FormData();
    fd.append("csv_file", csvFile);
    return request<BatchOnboardCommitResponse>(
      `/admin/onboard/batch?skip_existing=${skipExisting}`,
      { method: "POST", body: fd },
    );
  },
};

// ---------------------------------------------------------------------------
// Base Version Adoption L6 — advisor queue review surface
// ---------------------------------------------------------------------------

/** Shape of a single open advisor event surfaced to the analyst UI. */
export interface AdvisorEvent {
  event_id: string;
  event_type: "high_uncertainty_decision" | "unverified_candidate" | string;
  ts: string;
  article_id?: string | null;
  company_slug?: string | null;
  candidate_id?: string;
  category?: string;
  source_decision_type?: string;
  tags?: {
    scope?: string;
    signal_type?: string;
    attribution?: string;
    uncertainty?: string;
  };
  toulmin?: {
    claim?: string;
    grounds?: string[];
    warrant?: string;
    qualifier?: string;
    rebuttal?: string;
  };
  rationale?: string;
}

// ---------------------------------------------------------------------------
// Repos Integration W1 — 3-tier wiki surface
// ---------------------------------------------------------------------------

export interface WikiSearchHit {
  path: string;
  score: number;
  tier: "system" | "tenant" | "user" | "unknown";
}

// ---------------------------------------------------------------------------
// Repos Integration W2+W3 — Intelligence enrichments
// ---------------------------------------------------------------------------

export interface CompetitorEntry {
  slug: string;
  name: string;
  shared_risks: string[];
}

export interface ForecasterHorizonShape {
  direction: "improving" | "stable" | "declining";
  confidence: "low" | "moderate" | "high";
  rationale: string;
}

export interface ForecasterTrajectoryPoint {
  month: string;
  central: number;
  lo: number;
  hi: number;
}

export interface ForecasterResult {
  company_slug: string;
  polarity_series: Array<{ month: string; polarity_mean: number; count: number }>;
  horizons: {
    "3m"?: ForecasterHorizonShape;
    "6m"?: ForecasterHorizonShape;
    "12m"?: ForecasterHorizonShape;
  };
  trajectory: ForecasterTrajectoryPoint[];
  llm_used?: boolean;
}

export const intelligence = {
  competitors: (slug: string) =>
    request<{ tenant_slug: string; competitors: CompetitorEntry[]; error?: string }>(
      `/intelligence/${encodeURIComponent(slug)}/competitors`,
    ),

  forecast: (slug: string) =>
    request<ForecasterResult>(
      `/intelligence/${encodeURIComponent(slug)}/forecast`,
    ),
};

export const wiki = {
  search: (params: {
    q: string;
    tier?: "system" | "tenant" | "user";
    tenant?: string;
    user?: string;
    top_k?: number;
  }) => {
    const qs = new URLSearchParams({ q: params.q });
    if (params.tier) qs.set("tier", params.tier);
    if (params.tenant) qs.set("tenant", params.tenant);
    if (params.user) qs.set("user", params.user);
    if (params.top_k) qs.set("top_k", String(params.top_k));
    return request<{ count: number; hits: WikiSearchHit[]; wiki_root_missing?: boolean }>(
      `/wiki/search?${qs.toString()}`,
    );
  },

  related: (path: string) =>
    request<{ path: string; backlinks: string[]; wiki_root_missing?: boolean }>(
      `/wiki/related?path=${encodeURIComponent(path)}`,
    ),

  page: (path: string) =>
    request<{ path: string; content: string }>(
      `/wiki/page?path=${encodeURIComponent(path)}`,
    ),
};

// ---------------------------------------------------------------------------
// Autoresearcher Phase B — calibration ledger + leaderboard + run
// ---------------------------------------------------------------------------

export interface AutoresearcherExperiment {
  experiment_id: string;
  ts: string;
  tier: "system" | "tenant" | "user";
  seed: number;
  knob_kind: string;
  knob_id: string;
  metric_delta: number;
  decision: "keep" | "discard";
  rationale: string;
  n_articles: number;
}

export const autoresearcher = {
  experiments: (params: { tier?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    qs.set("tier", params.tier ?? "system");
    if (params.limit) qs.set("limit", String(params.limit));
    return request<{
      tier: string;
      count: number;
      experiments: AutoresearcherExperiment[];
    }>(`/autoresearcher/experiments?${qs.toString()}`);
  },

  leaderboard: (params: { tier?: string; top_n?: number } = {}) => {
    const qs = new URLSearchParams();
    qs.set("tier", params.tier ?? "system");
    if (params.top_n) qs.set("top_n", String(params.top_n));
    return request<{
      tier: string;
      count: number;
      entries: AutoresearcherExperiment[];
    }>(`/autoresearcher/leaderboard?${qs.toString()}`);
  },

  run: (req: {
    tier: "system" | "tenant" | "user";
    tenant_slug?: string;
    user_id?: string;
    budget?: number;
    seed?: number;
    keep_threshold?: number;
    min_age_days?: number;
  }) =>
    request<{
      tier: string;
      budget: number;
      seed: number;
      n_keeps: number;
      n_discards: number;
      n_errors: number;
      top_delta: number;
      top_knob_id: string | null;
    }>("/autoresearcher/run", {
      method: "POST",
      body: JSON.stringify(req),
    }),
};

export const advisor = {
  /** List the currently-open advisor queue. Optional tenant filter. */
  queue: (tenant?: string) =>
    request<{ count: number; events: AdvisorEvent[] }>(
      `/advisor/queue${tenant ? `?tenant=${encodeURIComponent(tenant)}` : ""}`,
    ),

  /** Approve or reject one event. Bearer-token actor is captured server-side. */
  resolve: (req: {
    event_id: string;
    resolution: "approve" | "reject";
    rationale?: string;
  }) =>
    request<{
      resolved: {
        ts: string;
        event_id: string;
        resolution: string;
        actor: string;
        rationale: string;
      };
      promoter_action?: {
        ok: boolean;
        message: string;
        category?: string | null;
        slug?: string | null;
      };
    }>("/advisor/resolve", {
      method: "POST",
      body: JSON.stringify(req),
    }),
};

// ---------------------------------------------------------------------------
// W2 — Self-service profile onboarding
// ---------------------------------------------------------------------------

/** W2 — onboarding status row, shared between admin + self-service flows. */
export interface OnboardStatus {
  slug: string;
  state: "pending" | "fetching" | "analysing" | "ready" | "failed";
  fetched: number;
  analysed: number;
  home_count: number;
  started_at: string;
  finished_at: string | null;
  error: string | null;
}

/** W2 — self-service profile onboarding. Any signed-in user can call this
 * for their own email-domain. Snowkap super-admins can call it for any
 * domain. Reuses /api/admin/onboard/{slug}/status for polling so we don't
 * need a duplicate poll endpoint. */

// ---------------------------------------------------------------------------
// Phase 6 — persona MCQ schema (lives in api.ts so types stay co-located
// with the request signatures the wizard consumes).
// ---------------------------------------------------------------------------

export type PersonaRole = "cfo" | "ceo" | "analyst" | "other";
export type PersonaHorizon = "quarterly" | "annual" | "3yr" | "5yr_plus";
export type PersonaDecisionStyle =
  | "data_first"
  | "narrative_first"
  | "regulatory_first"
  | "competitive_first";
export type PersonaRiskAppetite = "defensive" | "balanced" | "opportunistic";

export interface Persona {
  user_id: string;
  role: PersonaRole;
  esg_focus: string[];
  frameworks: string[];
  geographies: string[];
  horizon: PersonaHorizon;
  decision_style: PersonaDecisionStyle;
  risk_appetite: PersonaRiskAppetite;
  click_affinity: Record<string, number>;
  skip_affinity: Record<string, number>;
  last_active: string | null;
  onboarded_at: string;
  last_edited_at: string | null;
  last_drift_update_at: string | null;
  version: number;
}

export interface PersonaQuestionOption {
  value: string;
  label: string;
}

export interface PersonaQuestion {
  id: string;
  question: string;
  type: "multi_select" | "single_select";
  max_selections?: number;
  options: PersonaQuestionOption[];
}

export interface PersonaUpsertBody {
  role?: PersonaRole;
  esg_focus?: string[];
  frameworks?: string[];
  geographies?: string[];
  horizon?: PersonaHorizon;
  decision_style?: PersonaDecisionStyle;
  risk_appetite?: PersonaRiskAppetite;
}

export const me = {
  onboard: (domain: string, limit: number = 10) =>
    request<{ status: string; slug: string; domain: string; poll_url: string }>(
      "/me/onboard",
      {
        method: "POST",
        body: JSON.stringify({ domain, limit }),
      }
    ),

  onboardStatus: (slug: string) =>
    request<OnboardStatus>(`/admin/onboard/${slug}/status`),

  // Phase 6 — persona MCQ
  personaQuestions: () =>
    request<{ questions: PersonaQuestion[] }>("/me/persona/questions"),

  getPersona: () =>
    request<{ persona: Persona; mcq_completed: boolean }>("/me/persona"),

  upsertPersona: (body: PersonaUpsertBody) =>
    request<{ persona: Persona; mcq_completed: boolean }>("/me/persona", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};


// ---------------------------------------------------------------------------
// Phase 25 W6 schema types
// ---------------------------------------------------------------------------

export interface BatchOnboardRosterEntry {
  record_id: string;
  deal_name: string;
  company_name: string;
  slug: string;
  deal_stage: "Won" | "Negotiation";
  region: string;
  headquarter_country: string;
  amount_inr: number | null;
  deal_owner: string;
  needs_disambiguation: boolean;
  disambiguation_candidates: Array<{
    ticker: string;
    display_name: string;
    industry_hint: string;
    confidence: number;
    is_private: boolean;
  }>;
}

export interface BatchOnboardPreviewResponse {
  total_eligible: number;
  won_count: number;
  negotiation_count: number;
  countries: string[];
  auto_resolvable: number;
  needs_review: number;
  roster: BatchOnboardRosterEntry[];
}

export interface BatchOnboardCommitResponse extends BatchOnboardPreviewResponse {
  enqueued_job_ids: number[];
  skipped_already_existing: string[];
}
