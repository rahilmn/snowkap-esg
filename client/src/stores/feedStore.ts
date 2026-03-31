/** Feed state store — in-memory, manages swipe card stack (Stage 6.10) */

import { create } from "zustand";

interface FeedState {
  currentIndex: number;
  dismissedIds: Set<string>;
  hasSeenIntro: boolean;
  lastRefreshTime: number;

  dismiss: (articleId: string) => void;
  advance: () => void;
  reset: () => void;
  markIntroSeen: () => void;
  setRefreshTime: () => void;
}

export const useFeedStore = create<FeedState>()((set) => ({
  currentIndex: 0,
  dismissedIds: new Set<string>(),
  hasSeenIntro: true,
  lastRefreshTime: Date.now(),

  dismiss: (articleId) =>
    set((state) => {
      let newIds = new Set(state.dismissedIds);
      newIds.add(articleId);
      // Cap at 500 entries to prevent unbounded memory growth
      if (newIds.size > 500) {
        const arr = Array.from(newIds);
        newIds = new Set(arr.slice(arr.length - 300));
      }
      return { dismissedIds: newIds, currentIndex: state.currentIndex + 1 };
    }),

  advance: () => set((state) => ({ currentIndex: state.currentIndex + 1 })),

  reset: () =>
    set({ currentIndex: 0, dismissedIds: new Set<string>() }),

  markIntroSeen: () => set({ hasSeenIntro: true }),

  setRefreshTime: () => set({ lastRefreshTime: Date.now() }),
}));
