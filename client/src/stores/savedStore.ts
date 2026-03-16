/** Saved articles store — Zustand + localStorage persist (Stage 6.10) */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Article } from "@/types";

interface SavedState {
  savedArticles: Article[];
  savedIds: Set<string>;

  saveArticle: (article: Article) => void;
  unsaveArticle: (articleId: string) => void;
  isSaved: (articleId: string) => boolean;
}

export const useSavedStore = create<SavedState>()(
  persist(
    (set, get) => ({
      savedArticles: [],
      savedIds: new Set<string>(),

      saveArticle: (article) =>
        set((state) => {
          if (state.savedIds.has(article.id)) return state;
          const newIds = new Set(state.savedIds);
          newIds.add(article.id);
          return {
            savedArticles: [article, ...state.savedArticles],
            savedIds: newIds,
          };
        }),

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
    }),
    {
      name: "snowkap-saved",
      // Custom storage to handle Set serialization
      storage: {
        getItem: (name) => {
          const raw = localStorage.getItem(name);
          if (!raw) return null;
          const parsed = JSON.parse(raw);
          if (parsed?.state?.savedIds) {
            parsed.state.savedIds = new Set(parsed.state.savedIds);
          }
          return parsed;
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
