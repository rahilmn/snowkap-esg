import { create } from "zustand";
import { persist } from "zustand/middleware";

// Token stored in sessionStorage (survives refresh, clears on tab close)
// Separate from Zustand persist to avoid exposing token in the store snapshot
const _TOKEN_KEY = "snowkap-tk";

function _readToken(): string | null {
  try { return sessionStorage.getItem(_TOKEN_KEY); } catch { return null; }
}
function _writeToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(_TOKEN_KEY, t);
    else sessionStorage.removeItem(_TOKEN_KEY);
  } catch { /* private browsing */ }
}

/** Get the current auth token. */
export function getToken(): string | null {
  return _readToken();
}

/** Phase 10: super-admin "View as..." options. Purely a UX default picker —
 * NOT a security boundary. Backend still enforces actual JWT permissions. */
export type ViewAsRole = "cfo" | "ceo" | "esg-analyst" | "member" | null;

interface AuthState {
  userId: string | null;
  tenantId: string | null;
  companyId: string | null;
  designation: string | null;
  permissions: string[];
  domain: string | null;
  name: string | null;
  isAuthenticated: boolean;
  /** Phase 10: super-admin-only role-view override. Null = use real designation. */
  viewAsRole: ViewAsRole;
  /**
   * Phase 13 B7: server-confirmed email backend liveness. Polled from
   * GET /api/admin/email-config-status on app boot + after any /login.
   * The Share button must gate on this AND `manage_drip_campaigns`
   * permission, otherwise demo-day clicks immediately fall through to
   * "preview" and confuse the user.
   */
  emailConfigured: boolean;
  emailConfigReason: string;
  emailSender: string;

  login: (data: {
    token: string;
    user_id: string;
    tenant_id: string;
    company_id: string | null;
    designation: string;
    permissions: string[];
    domain: string;
    name: string | null;
  }) => void;
  setCompanyId: (id: string | null) => void;
  setViewAsRole: (role: ViewAsRole) => void;
  setEmailConfig: (cfg: { enabled: boolean; sender: string; reason?: string }) => void;
  logout: () => void;
  hasPermission: (perm: string) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      userId: null,
      tenantId: null,
      companyId: null,
      designation: null,
      permissions: [],
      domain: null,
      name: null,
      isAuthenticated: false,
      viewAsRole: null,
      emailConfigured: false,  // Phase 13 B7
      emailConfigReason: "",
      emailSender: "",

      login: (data) => {
        _writeToken(data.token);
        set({
          userId: data.user_id,
          tenantId: data.tenant_id,
          companyId: data.company_id,
          designation: data.designation,
          permissions: data.permissions,
          domain: data.domain,
          name: data.name,
          isAuthenticated: true,
          viewAsRole: null,  // reset role-view override on login
        });
        // Scope saved articles to this tenant — clears if tenant changed
        import("@/stores/savedStore").then(({ useSavedStore }) => {
          useSavedStore.getState().setTenant(data.tenant_id);
        });
      },

      setCompanyId: (id) => set({ companyId: id }),

      setViewAsRole: (role) => {
        set({ viewAsRole: role });
        // Reset the perspective override so the role-driven default kicks in
        // the next time useSyncPerspectiveWithRole runs.
        import("@/stores/perspectiveStore").then(({ usePerspective }) => {
          usePerspective.getState().resetOverride();
        });
      },

      logout: () => {
        _writeToken(null);
        // Clear saved articles on logout to prevent cross-tenant leakage
        import("@/stores/savedStore").then(({ useSavedStore }) => {
          useSavedStore.getState().clearAll();
        });
        // Reset perspective override so next session honours the new role.
        import("@/stores/perspectiveStore").then(({ usePerspective }) => {
          usePerspective.getState().resetOverride();
        });
        set({
          userId: null,
          tenantId: null,
          companyId: null,
          designation: null,
          permissions: [],
          domain: null,
          name: null,
          isAuthenticated: false,
          viewAsRole: null,
        });
      },

      setEmailConfig: (cfg) => set({
        emailConfigured: !!cfg.enabled,
        emailSender: cfg.sender || "",
        emailConfigReason: cfg.reason || "",
      }),

      hasPermission: (perm: string) => get().permissions.includes(perm),
    }),
    {
      name: "snowkap-auth",
      storage: {
        getItem: (name) => {
          const str = sessionStorage.getItem(name);
          return str ? JSON.parse(str) : null;
        },
        setItem: (name, value) => {
          sessionStorage.setItem(name, JSON.stringify(value));
        },
        removeItem: (name) => {
          sessionStorage.removeItem(name);
        },
      },
      partialize: (state) => ({
        userId: state.userId,
        tenantId: state.tenantId,
        companyId: state.companyId,
        designation: state.designation,
        permissions: state.permissions,
        domain: state.domain,
        name: state.name,
        isAuthenticated: state.isAuthenticated,
        viewAsRole: state.viewAsRole,
      }) as unknown as AuthState,
    },
  ),
);


/** Phase 10: "active role" for the current view.
 *
 * - If the user is a super-admin AND has set a `viewAsRole` override → that.
 * - Otherwise → their real `designation` from the JWT (lowercased).
 *
 * Consumed by ArticleDetailSheet (Phase D) to pick the default perspective
 * panel. Note: this is a UX default, NOT a security boundary — the backend
 * still enforces actual JWT permissions regardless of what this returns. */
export function useActiveRole(): string | null {
  const designation = useAuthStore((s) => s.designation);
  const viewAsRole = useAuthStore((s) => s.viewAsRole);
  const permissions = useAuthStore((s) => s.permissions);
  const isSuperAdmin = permissions.includes("super_admin");

  if (isSuperAdmin && viewAsRole) return viewAsRole;
  return designation ? designation.toLowerCase() : null;
}

/** Phase 10: is this user a super-admin (allowlisted Snowkap staff)? */
export function useIsSuperAdmin(): boolean {
  const permissions = useAuthStore((s) => s.permissions);
  return permissions.includes("super_admin");
}

/** Phase 24.1: is this user the Snowkap Sales admin specifically?
 *
 * Stricter than `useIsSuperAdmin`. Only the Sales admin gets the
 * "All Companies" cross-tenant view in the company switcher. Other
 * Snowkap super-admins (ci@, newsletter@, etc.) keep their super_admin
 * permissions for onboarding / sharing — they just don't see the
 * aggregated dashboard.
 *
 * Configurable via `VITE_SALES_ADMIN_EMAIL` (defaults to
 * sales@snowkap.co.in) so the same code supports staging tenants and
 * ops rotations without a redeploy. The backend mirrors this via
 * SNOWKAP_SALES_ADMIN_EMAIL — keep them in sync.
 */
export function useIsSalesAdmin(): boolean {
  const userId = useAuthStore((s) => s.userId);
  if (!userId) return false;
  const target = (
    import.meta.env.VITE_SALES_ADMIN_EMAIL || "sales@snowkap.co.in"
  )
    .toString()
    .trim()
    .toLowerCase();
  return userId.trim().toLowerCase() === target;
}


/** Phase 10 / Phase D: map the active role to the matching perspective panel.
 *
 * Used by `useSyncPerspectiveWithRole` to pick the default CFO/CEO/ESG Analyst
 * tab on article detail. Kept outside the store so it can be unit-tested and
 * reused by other components that need the same mapping.
 *
 * The fallback is "esg-analyst" — the deepest view, safe for unknown roles.
 */
export function roleToPerspective(role: string | null): "cfo" | "ceo" | "esg-analyst" {
  if (!role) return "esg-analyst";
  const r = role.toLowerCase().trim();

  // CFO / Finance / Treasury → CFO panel (10-second verdict)
  if (r === "cfo" || /^(finance|treasur|cfo)/.test(r)) return "cfo";

  // CEO / MD / Board → CEO panel (strategic narrative)
  if (r === "ceo" || /^(ceo|cto|coo|managing director|md|chairman|board)/.test(r)) return "ceo";

  // Sustainability / ESG / Analyst → ESG Analyst panel (full detail)
  if (/^(sustainab|esg|compliance|analyst|research|consult|data)/.test(r)) return "esg-analyst";

  // Default: decision-first ESG Analyst view
  return "esg-analyst";
}
