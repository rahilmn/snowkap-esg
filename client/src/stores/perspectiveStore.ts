import { useEffect } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { Perspective } from "@/lib/snowkap-api";
import { roleToPerspective, useActiveRole } from "@/stores/authStore";

/** Phase 10 / Phase D: the perspective store remembers the user's last
 * explicit choice (`userOverride=true` once they click PerspectiveSwitcher).
 * Before that happens, the active perspective auto-syncs with the active
 * role from authStore — so a CFO-designated user opens an article to the
 * CFO panel by default, and a sales admin toggling `viewAsRole` sees that
 * reflected live.
 *
 * Once the user explicitly picks a lens via the PerspectiveSwitcher button,
 * we stop auto-syncing (their choice is sticky) — until they log out, which
 * resets `userOverride` so the next session picks the role default again. */
interface PerspectiveState {
  active: Perspective;
  /** Has the user ever clicked the PerspectiveSwitcher? Sticky-click flag. */
  userOverride: boolean;
  /** Explicit user click — turns off auto-role-sync. */
  setActive: (p: Perspective) => void;
  /** Internal sync from role — does NOT flip userOverride. */
  syncFromRole: (p: Perspective) => void;
  /** Reset override (called on logout). */
  resetOverride: () => void;
}

export const usePerspective = create<PerspectiveState>()(
  persist(
    (set) => ({
      active: "esg-analyst",
      userOverride: false,
      setActive: (p) => set({ active: p, userOverride: true }),
      syncFromRole: (p) => set({ active: p }),
      resetOverride: () => set({ userOverride: false }),
    }),
    { name: "snowkap-perspective" },
  ),
);

/** Phase 10 / Phase D: React hook that keeps `perspectiveStore.active` in
 * sync with the user's active role — unless they have an explicit override.
 * Mount it once at the top of the authenticated tree (AppLayout). */
export function useSyncPerspectiveWithRole(): void {
  const activeRole = useActiveRole();
  const userOverride = usePerspective((s) => s.userOverride);
  const active = usePerspective((s) => s.active);
  const syncFromRole = usePerspective((s) => s.syncFromRole);

  useEffect(() => {
    if (userOverride) return;
    const target = roleToPerspective(activeRole);
    if (active !== target) {
      syncFromRole(target);
    }
  }, [activeRole, userOverride, active, syncFromRole]);
}
