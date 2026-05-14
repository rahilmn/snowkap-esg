/** Phase 6 — Personalisation opt-in hook.
 *
 * Returns true once the user has saved their persona MCQ (mcq_completed=true).
 * Caller passes this to `news.list({ personalise })` to get persona-modulated
 * rankings. Falls back to false (un-personalised) when the user hasn't
 * filled out the MCQ yet — preserves the discoverability invariant: every
 * row is visible regardless of opt-in state.
 *
 * The query is cached aggressively (60s stale time + 5min cacheTime) so
 * every feed render doesn't re-query the persona endpoint.
 */
import { useQuery } from "@tanstack/react-query";
import { me } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

export function usePersonalisationOptIn(): boolean {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const { data } = useQuery({
    queryKey: ["persona", "self"],
    queryFn: () => me.getPersona(),
    enabled: isAuthenticated,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    // Defensive: a 401/403 here mustn't break the feed — flow as
    // un-personalised. React Query swallows errors at the data level
    // already, so we just check `data?.mcq_completed`.
    retry: false,
  });

  return Boolean(data?.mcq_completed);
}
