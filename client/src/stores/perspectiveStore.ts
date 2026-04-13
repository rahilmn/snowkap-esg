import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { Perspective } from "@/lib/snowkap-api";

interface PerspectiveState {
  active: Perspective;
  setActive: (p: Perspective) => void;
}

export const usePerspective = create<PerspectiveState>()(
  persist(
    (set) => ({
      active: "esg-analyst",
      setActive: (p) => set({ active: p }),
    }),
    { name: "snowkap-perspective" },
  ),
);
