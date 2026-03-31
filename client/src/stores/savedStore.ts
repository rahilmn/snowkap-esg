/** Saved articles store — Zustand + localStorage persist (Stage 6.10)
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
  /** Call on login — clears saved if tenant changed */
  setTenant: (tenantId: string) => void;
  /** Call on logout — clears everything */
  clearAll: () => void;
}

export const useSavedStore = create<SavedState>()(
  persist(
    (set, get) => ({
      savedArticles: [],
      savedIds: new Set<string>(),
      tenantId: null,

      saveArticle: (article) => {
        set((state) => {
          if (state.savedIds.has(article.id)) return state;
          const newIds = new Set(state.savedIds);
          newIds.add(article.id);
          return {
            savedArticles: [article, ...state.savedArticles],
            savedIds: newIds,
          };
        });
        // Also persist to server (non-blocking)
        import("@/lib/api").then(({ news }) => {
          news.bookmark(article.id).catch((err) => {
            console.error("Bookmark sync failed:", err);
          });
        });
      },

      unsaveArticle: (articleId) =>
        set((state) => {
          const newIds = new Set(state.savedIds);
          newIds.delete(articleId);
          return {
            savedArticles: state.savedArticles.filter((a) => a.id !== articleId),
            savedIds: newIds,
          };
        }),

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
