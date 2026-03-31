/**
 * Phase 3G: User preference Zustand store.
 */

import { create } from "zustand";
import { preferences as prefApi } from "../lib/api";

interface UserPreference {
  preferred_frameworks: string[];
  preferred_pillars: string[];
  preferred_topics: string[];
  alert_threshold: number;
  content_depth: string;
  companies_of_interest: string[];
  dismissed_topics: string[];
}

interface PreferenceState {
  preferences: UserPreference | null;
  isLoading: boolean;
  fetchPreferences: () => Promise<void>;
  updatePreferences: (data: Partial<UserPreference>) => Promise<void>;
}

export const usePreferenceStore = create<PreferenceState>((set) => ({
  preferences: null,
  isLoading: false,

  fetchPreferences: async () => {
    set({ isLoading: true });
    try {
      const data = await prefApi.get();
      set({ preferences: data, isLoading: false });
    } catch {
      set({ isLoading: false });
    }
  },

  updatePreferences: async (data) => {
    set({ isLoading: true });
    try {
      const updated = await prefApi.update(data);
      set({ preferences: updated, isLoading: false });
    } catch {
      set({ isLoading: false });
    }
  },
}));
