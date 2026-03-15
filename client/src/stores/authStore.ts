import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  token: string | null;
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
  logout: () => void;
  hasPermission: (perm: string) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      userId: null,
      tenantId: null,
      companyId: null,
      designation: null,
      permissions: [],
      domain: null,
      name: null,
      isAuthenticated: false,

      login: (data) => {
        localStorage.setItem("token", data.token);
        set({
          token: data.token,
          userId: data.user_id,
          tenantId: data.tenant_id,
          companyId: data.company_id,
          designation: data.designation,
          permissions: data.permissions,
          domain: data.domain,
          name: data.name,
          isAuthenticated: true,
        });
      },

      logout: () => {
        localStorage.removeItem("token");
        set({
          token: null,
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
      partialize: (state) => ({
        token: state.token,
        userId: state.userId,
        tenantId: state.tenantId,
        companyId: state.companyId,
        designation: state.designation,
        permissions: state.permissions,
        domain: state.domain,
        name: state.name,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);
