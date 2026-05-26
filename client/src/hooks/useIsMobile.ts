/** Phase 28 / Feature 5 — useIsMobile.
 *
 * Returns `true` when the viewport is narrower than 640px (Tailwind's
 * `sm` breakpoint). The ArticleDetailSheet uses this in combination with
 * `useRolePanels` to additionally suppress non-essential panels on
 * mobile — fixes the endless-scroll problem the user reported.
 *
 * SSR-safe: returns `false` until the first effect runs.
 */
import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 640px)";

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(MOBILE_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(MOBILE_QUERY);
    const update = (ev: MediaQueryListEvent | MediaQueryList) => setIsMobile(ev.matches);
    // Initial sync
    update(mql);
    // Modern + legacy listener APIs (Safari < 14)
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", update);
      return () => mql.removeEventListener("change", update);
    }
    mql.addListener(update as (e: MediaQueryListEvent) => void);
    return () => mql.removeListener(update as (e: MediaQueryListEvent) => void);
  }, []);

  return isMobile;
}
