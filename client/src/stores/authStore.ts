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

interface AuthState {
  userId: string | null;
  tenantId: string | null;
  companyId: string | null;
  designation: string | null;
  permissions: string[];
  domain: string | null;
  name: string | null;
  isAuthenticated: boolean;

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
        });
        // Scope saved articles to this tenant — clears if tenant changed
        import("@/stores/savedStore").then(({ useSavedStore }) => {
          useSavedStore.getState().setTenant(data.tenant_id);
        });
      },

      setCompanyId: (id) => set({ companyId: id }),

      logout: () => {
        _writeToken(null);
        // Clear saved articles on logout to prevent cross-tenant leakage
        import("@/stores/savedStore").then(({ useSavedStore }) => {
          useSavedStore.getState().clearAll();
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
        });
      },

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
      }) as unknown as AuthState,
    },
  ),
);
