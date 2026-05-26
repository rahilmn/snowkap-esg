/** Saved articles store — Zustand + localStorage persist (Stage 6.10)
 *
 * Wiki v1.1 (2026-05-22) — Server-first sync:
 *   - `saveArticle` / `unsaveArticle` apply optimistic local updates and
 *     await the server call. On failure they REVERT the local state and
 *     emit a `bookmark-error` event so the SwipeDeck can surface a toast.
 *     This closes the Phase-34.7 data-integrity gap where a failed
 *     network call left local-only bookmarks that never reached the
 *     server.
 *   - `syncFromServer()` re-fetches the canonical bookmark list from
 *     `/api/me/bookmarks` and reconciles `savedIds`. Called by WikiPage
 *     on every mount so cross-device bookmarks sync correctly.
 *
 * Tenant-scoped: stores tenantId alongside saved articles. On login,
 * if the tenant changes, saved articles are cleared to prevent
 * cross-tenant data leakage.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Article } from "@/types";

interface SavedState {
  savedArticles: Article[];
  savedIds: Set<string>;
  tenantId: string | null;

  saveArticle: (article: Article) => void;
  unsaveArticle: (articleId: string) => void;
  isSaved: (articleId: string) => boolean;
  /** Wiki v1.1 — reconcile `savedIds` with the canonical server list. */
  syncFromServer: () => Promise<void>;
  /** Call on login — clears saved if tenant changed */
  setTenant: (tenantId: string) => void;
  /** Call on logout — clears everything */
  clearAll: () => void;
}

/** Wiki v1.1 — bookmark-error event. SwipeDeck + ArticleSheet listen on
 *  `window` for this event to show a "Couldn't save — try again" toast.
 */
function _emitBookmarkError(articleId: string, action: "save" | "unsave", error: unknown) {
  if (typeof window === "undefined") return;
  const detail = {
    articleId,
    action,
    message: error instanceof Error ? error.message : String(error),
  };
  window.dispatchEvent(new CustomEvent("snowkap:bookmark-error", { detail }));
}

export const useSavedStore = create<SavedState>()(
  persist(
    (set, get) => ({
      savedArticles: [],
      savedIds: new Set<string>(),
      tenantId: null,

      saveArticle: (article) => {
        // Optimistic local update for snappy UI feedback.
        const alreadySaved = get().savedIds.has(article.id);
        if (alreadySaved) return;
        set((state) => {
          const newIds = new Set(state.savedIds);
          newIds.add(article.id);
          return {
            savedArticles: [article, ...state.savedArticles],
            savedIds: newIds,
          };
        });
        // Server-first: await the canonical write. On failure revert
        // local state + emit `snowkap:bookmark-error` so the UI can
        // surface a "Couldn't save — try again" toast.
        void import("@/lib/api").then(async ({ news, bookmarks }) => {
          try {
            await bookmarks.add(article.id);
            // Phase 34.7 back-compat: legacy `news.bookmark` endpoint is
            // still wired on some server code paths. Fire-and-forget so
            // it doesn't gate the user-visible success.
            news.bookmark(article.id).catch(() => { /* legacy soft-fail */ });
          } catch (err) {
            // Revert the optimistic update.
            set((state) => {
              const reverted = new Set(state.savedIds);
              reverted.delete(article.id);
              return {
                savedArticles: state.savedArticles.filter((a) => a.id !== article.id),
                savedIds: reverted,
              };
            });
            _emitBookmarkError(article.id, "save", err);
          }
        });
      },

      unsaveArticle: (articleId) => {
        // Snapshot the article for potential revert on failure.
        const before = get().savedArticles.find((a) => a.id === articleId);
        const wasSaved = get().savedIds.has(articleId);
        if (!wasSaved) return;
        set((state) => {
          const newIds = new Set(state.savedIds);
          newIds.delete(articleId);
          return {
            savedArticles: state.savedArticles.filter((a) => a.id !== articleId),
            savedIds: newIds,
          };
        });
        void import("@/lib/api").then(async ({ bookmarks }) => {
          try {
            await bookmarks.remove(articleId);
          } catch (err) {
            // Revert — put the article back.
            if (before) {
              set((state) => {
                const reverted = new Set(state.savedIds);
                reverted.add(articleId);
                return {
                  savedArticles: [before, ...state.savedArticles],
                  savedIds: reverted,
                };
              });
            }
            _emitBookmarkError(articleId, "unsave", err);
          }
        });
      },

      /** Wiki v1.1 — fetch the canonical server bookmark list and
       *  reconcile `savedIds`. Articles we don't yet have in
       *  `savedArticles` get a stub so the SwipeDeck badge can still
       *  render the "✓ bookmarked" state — the full article record is
       *  hydrated on next visit to /now or /wiki. */
      syncFromServer: async () => {
        try {
          const { bookmarks } = await import("@/lib/api");
          const out = await bookmarks.list();
          const serverIds = new Set((out.bookmarks || []).map((b) => b.article_id));
          set((state) => {
            // Keep the existing rich Article records for ids that still
            // exist on the server; drop the rest.
            const kept = state.savedArticles.filter((a) => serverIds.has(a.id));
            return {
              savedIds: serverIds,
              savedArticles: kept,
            };
          });
        } catch {
          // Network failure is non-fatal — the local cache stays as-is.
        }
      },

      isSaved: (articleId) => get().savedIds.has(articleId),

      setTenant: (newTenantId) => {
        const current = get().tenantId;
        if (current && current !== newTenantId) {
          // Tenant switched — clear saved articles to prevent cross-tenant leak
          set({
            savedArticles: [],
            savedIds: new Set<string>(),
            tenantId: newTenantId,
          });
        } else {
          set({ tenantId: newTenantId });
        }
      },

      clearAll: () =>
        set({
          savedArticles: [],
          savedIds: new Set<string>(),
          tenantId: null,
        }),
    }),
    {
      name: "snowkap-saved",
      // Custom storage to handle Set serialization
      storage: {
        getItem: (name) => {
          const raw = localStorage.getItem(name);
          if (!raw) return null;
          try {
            const parsed = JSON.parse(raw);
            if (parsed?.state?.savedIds) {
              parsed.state.savedIds = new Set(parsed.state.savedIds);
            }
            return parsed;
          } catch {
            localStorage.removeItem(name);
            return null;
          }
        },
        setItem: (name, value) => {
          const serializable = {
            ...value,
            state: {
              ...value.state,
              savedIds: Array.from(value.state.savedIds || []),
            },
          };
          localStorage.setItem(name, JSON.stringify(serializable));
        },
        removeItem: (name) => localStorage.removeItem(name),
      },
    },
  ),
);
