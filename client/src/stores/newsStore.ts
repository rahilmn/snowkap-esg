import { create } from "zustand";
import type { Article } from "@/types";

interface NewsState {
  articles: Article[];
  selectedArticle: Article | null;
  filter: {
    companyId: string | null;
    pillar: string | null;
  };

  setArticles: (articles: Article[]) => void;
  selectArticle: (article: Article | null) => void;
  setFilter: (filter: Partial<NewsState["filter"]>) => void;
}

export const useNewsStore = create<NewsState>()((set) => ({
  articles: [],
  selectedArticle: null,
  filter: { companyId: null, pillar: null },

  setArticles: (articles) => set({ articles }),
  selectArticle: (article) => set({ selectedArticle: article }),
  setFilter: (filter) =>
    set((state) => ({ filter: { ...state.filter, ...filter } })),
}));
