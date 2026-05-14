/** Phase 24 (W4) — analyst session state store
 *
 * Mirror of the server-side `analyst_session_state` table. Hydrated on
 * boot, kept in sync via debounced POSTs to /api/session/state. The
 * point isn't a chatbot — it's a stateful workflow companion. A user
 * returning to the dashboard sees "Resume monthly review · Adani Power
 * · CFO" with their follow-up queue pre-populated, instead of a generic
 * landing page.
 *
 * NOTE: this store is purely additive on top of existing perspectiveStore
 * + authStore. It does NOT change the default perspective behaviour, nav,
 * or feed rendering — it only persists the analyst's micro-context across
 * sessions. Components opt-in by reading the activity field; legacy
 * components that don't read it work exactly as before.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ??
  "/api";

export type SessionPhase =
  | "monthly_review"
  | "ad_hoc_lookup"
  | "onboarding_new_company";

export interface SessionActivity {
  current_action?: string; // e.g. "reading_insight", "reviewing_recs"
  insight_id?: string;
  started_at?: string;
}

export interface FollowUpEntry {
  insight_id: string;
  reason: string;
  company_slug: string;
  marked_at: string;
}

interface AnalystSessionState {
  /** The server-confirmed snapshot. Updated optimistically + reconciled. */
  phase: SessionPhase | null;
  active_company_slug: string | null;
  active_perspective: "cfo" | "ceo" | "esg-analyst" | null;
  activity: SessionActivity;
  follow_up_queue: FollowUpEntry[];
  hydrated: boolean;
  loading: boolean;

  /** Hydrate from server. Called on app boot after auth. Safe to call
   * multiple times; second+ calls no-op when already hydrated unless
   * `force=true`. */
  hydrate: (force?: boolean) => Promise<void>;
  /** Optimistic local update + debounced POST to server. */
  updateState: (
    partial: Partial<{
      phase: SessionPhase;
      active_company_slug: string;
      active_perspective: "cfo" | "ceo" | "esg-analyst";
      activity: SessionActivity;
    }>
  ) => Promise<void>;
  /** Mark an insight for follow-up. */
  addFollowUp: (
    insight_id: string,
    reason?: string,
    company_slug?: string
  ) => Promise<void>;
  /** Drop an insight from the follow-up queue. */
  removeFollowUp: (insight_id: string) => Promise<void>;
  /** Reset on logout. */
  reset: () => void;
}

function authHeaders(): Record<string, string> {
  // Mirror the lib/api.ts pattern: pull JWT from localStorage if present,
  // otherwise the API key. Both flow through the same Authorization /
  // X-API-Key headers the rest of the app uses.
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  try {
    const token = localStorage.getItem("snowkap_jwt") || "";
    if (token) headers.Authorization = `Bearer ${token}`;
    const apiKey = (import.meta.env.VITE_API_KEY as string | undefined) || "";
    if (apiKey) headers["X-API-Key"] = apiKey;
  } catch {
    // localStorage unavailable (SSR / private mode) — proceed without
  }
  return headers;
}

async function _get(): Promise<{
  phase: SessionPhase | null;
  active_company_slug: string | null;
  active_perspective: "cfo" | "ceo" | "esg-analyst" | null;
  activity: SessionActivity;
  follow_up_queue: FollowUpEntry[];
  updated_at: string;
}> {
  const r = await fetch(`${API_BASE}/session/state`, {
    headers: authHeaders(),
  });
  if (!r.ok) throw new Error(`session.get ${r.status}`);
  return r.json();
}

async function _post(body: unknown): Promise<unknown> {
  const r = await fetch(`${API_BASE}/session/state`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`session.post ${r.status}`);
  return r.json();
}

async function _postFollowUp(body: unknown): Promise<unknown> {
  const r = await fetch(`${API_BASE}/session/follow-up`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`session.followup.add ${r.status}`);
  return r.json();
}

async function _delFollowUp(insight_id: string): Promise<unknown> {
  const r = await fetch(
    `${API_BASE}/session/follow-up/${encodeURIComponent(insight_id)}`,
    { method: "DELETE", headers: authHeaders() }
  );
  if (!r.ok) throw new Error(`session.followup.del ${r.status}`);
  return r.json();
}

// Debounce updateState calls — the user can rapidly toggle perspective +
// open/close insights; we don't need to round-trip on every keystroke.
let _saveTimer: number | null = null;
const SAVE_DEBOUNCE_MS = 800;

export const useAnalystSession = create<AnalystSessionState>()(
  devtools(
    (set, get) => ({
      phase: null,
      active_company_slug: null,
      active_perspective: null,
      activity: {},
      follow_up_queue: [],
      hydrated: false,
      loading: false,

      hydrate: async (force = false) => {
        if (get().hydrated && !force) return;
        set({ loading: true });
        try {
          const data = await _get();
          set({
            phase: data.phase,
            active_company_slug: data.active_company_slug,
            active_perspective: data.active_perspective,
            activity: data.activity ?? {},
            follow_up_queue: data.follow_up_queue ?? [],
            hydrated: true,
            loading: false,
          });
        } catch (exc) {
          // Silent on failure — analyst session is additive UX, not core.
          // Logging via console.warn (allowed by ESLint config).
          console.warn("[analystSession] hydrate failed:", exc);
          set({ hydrated: true, loading: false });
        }
      },

      updateState: async (partial) => {
        // Optimistic local update
        set((s) => ({ ...s, ...partial }));
        // Debounced server save
        if (_saveTimer !== null) window.clearTimeout(_saveTimer);
        _saveTimer = window.setTimeout(async () => {
          try {
            await _post(partial);
          } catch (exc) {
            console.warn("[analystSession] updateState save failed:", exc);
          }
          _saveTimer = null;
        }, SAVE_DEBOUNCE_MS);
      },

      addFollowUp: async (insight_id, reason, company_slug) => {
        // Optimistic local insert (de-dup, head-of-queue)
        set((s) => {
          const existing = s.follow_up_queue.filter(
            (e) => e.insight_id !== insight_id
          );
          return {
            follow_up_queue: [
              {
                insight_id,
                reason: reason ?? "",
                company_slug: company_slug ?? "",
                marked_at: new Date().toISOString(),
              },
              ...existing,
            ].slice(0, 50),
          };
        });
        try {
          await _postFollowUp({ insight_id, reason, company_slug });
        } catch (exc) {
          console.warn("[analystSession] addFollowUp save failed:", exc);
        }
      },

      removeFollowUp: async (insight_id) => {
        set((s) => ({
          follow_up_queue: s.follow_up_queue.filter(
            (e) => e.insight_id !== insight_id
          ),
        }));
        try {
          await _delFollowUp(insight_id);
        } catch (exc) {
          console.warn("[analystSession] removeFollowUp save failed:", exc);
        }
      },

      reset: () => {
        if (_saveTimer !== null) {
          window.clearTimeout(_saveTimer);
          _saveTimer = null;
        }
        set({
          phase: null,
          active_company_slug: null,
          active_perspective: null,
          activity: {},
          follow_up_queue: [],
          hydrated: false,
          loading: false,
        });
      },
    }),
    { name: "analystSession" }
  )
);
