/**
 * Typed HTTP client for the Snowkap ESG Intelligence Engine API.
 *
 * Reads `VITE_API_URL` and `VITE_API_KEY` from Vite env vars.
 * Defaults to `/api` (proxied to FastAPI in dev) with no auth header in
 * local mode (backend disables auth when SNOWKAP_API_KEY is unset).
 */

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) || "/api";
const API_KEY = (import.meta.env.VITE_API_KEY as string | undefined) || "";

export type Perspective = "esg-analyst" | "cfo" | "ceo";

export interface Company {
  slug: string;
  name: string;
  domain: string;
  industry: string;
  sasb_category: string;
  market_cap: string;
  listing_exchange: string;
  headquarter_city: string;
  headquarter_country: string;
  headquarter_region: string;
  stats?: {
    total_insights: number;
    home_tier: number;
    secondary_tier: number;
    latest_insight_id: string | null;
  };
}

export interface IndexRow {
  id: string;
  company_slug: string;
  title: string;
  source: string;
  url: string;
  published_at: string;
  tier: string;
  materiality: string;
  action: string;
  relevance_score: number;
  impact_score: number;
  esg_pillar: string;
  primary_theme: string;
  content_type: string;
  framework_count: number;
  do_nothing: number;
  recommendations_count: number;
  json_path: string;
  written_at: string;
  ontology_queries: number;
}

export interface CrispView {
  perspective: Perspective;
  headline: string;
  impact_grid: Record<"financial" | "regulatory" | "strategic", "HIGH" | "MEDIUM" | "LOW">;
  what_matters: string[];
  action: string[];
  materiality: string;
  do_nothing: boolean;
  active_impact_dimensions: string[];
  full_insight: Record<string, unknown> | null;
}

export interface FullInsightPayload {
  article: Record<string, unknown>;
  pipeline: Record<string, unknown>;
  insight: Record<string, unknown> | null;
  recommendations: Record<string, unknown> | null;
  perspectives: Record<Perspective, CrispView>;
  meta: Record<string, unknown>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) || {}),
  };
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }
  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${text || path}`);
  }
  return (await resp.json()) as T;
}

export const api = {
  listCompanies: () =>
    request<{ count: number; companies: Company[] }>("/companies"),

  getCompany: (slug: string) =>
    request<Company>(`/companies/${slug}`),

  listInsights: (slug: string, tier?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (tier) params.set("tier", tier);
    return request<{ count: number; company_slug: string; items: IndexRow[] }>(
      `/companies/${slug}/insights?${params.toString()}`,
    );
  },

  getInsight: (id: string) =>
    request<{ index: IndexRow; payload: FullInsightPayload }>(`/insights/${id}`),

  getInsightPerspective: (id: string, perspective: Perspective) =>
    request<{ article: Record<string, unknown>; perspective: CrispView; index: IndexRow }>(
      `/insights/${id}?perspective=${perspective}`,
    ),

  globalFeed: (tier?: string, limit = 20) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (tier) params.set("tier", tier);
    return request<{ count: number; items: IndexRow[] }>(`/feed?${params.toString()}`);
  },

  triggerIngest: (slug: string, limit = 5) =>
    request<{ status: string; company_slug: string }>(`/ingest/${slug}?limit=${limit}`, {
      method: "POST",
    }),
};
