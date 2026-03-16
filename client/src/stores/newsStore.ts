/** News store — article list with pagination support (Stage 6.10) */

import { create } from "zustand";
import type { Article } from "@/types";

interface NewsState {
  articles: Article[];
  selectedArticle: Article | null;
  offset: number;
  hasMore: boolean;
  isLoadingMore: boolean;
  filter: {
    companyId: string | null;
    pillar: string | null;
  };

  setArticles: (articles: Article[]) => void;
  appendArticles: (articles: Article[]) => void;
  selectArticle: (article: Article | null) => void;
  setFilter: (filter: Partial<NewsState["filter"]>) => void;
  setOffset: (offset: number) => void;
  setHasMore: (hasMore: boolean) => void;
  setLoadingMore: (loading: boolean) => void;
}

export const useNewsStore = create<NewsState>()((set) => ({
  articles: [],
  selectedArticle: null,
  offset: 0,
  hasMore: true,
  isLoadingMore: false,
  filter: { companyId: null, pillar: null },

  setArticles: (articles) => set({ articles }),
  appendArticles: (newArticles) =>
    set((state) => ({ articles: [...state.articles, ...newArticles] })),
  selectArticle: (article) => set({ selectedArticle: article }),
  setFilter: (filter) =>
    set((state) => ({ filter: { ...state.filter, ...filter } })),
  setOffset: (offset) => set({ offset }),
  setHasMore: (hasMore) => set({ hasMore }),
  setLoadingMore: (loading) => set({ isLoadingMore: loading }),
}));
