/** Phase 10 — CompanySwitcher
 *
 * Super-admin-only dropdown in the header. Lists every tenant the sales admin
 * can switch into:
 *   - the 7 hardcoded target companies (source='target')
 *   - every onboarded prospect that has logged in (source='onboarded')
 *
 * Selecting a tenant writes its slug to `companyId` in authStore, which the
 * rest of the app already uses to filter the news feed and all downstream
 * analysis. That means clicking any row in this switcher instantly reloads
 * the dashboard for that tenant — no new wiring needed.
 *
 * Regular client users never see this component (gated by `useIsSuperAdmin`
 * at the call site in MinimalHeader).
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import { admin, type AdminTenant } from "@/lib/api";
import { COLORS } from "@/lib/designTokens";

export function CompanySwitcher() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const companyId = useAuthStore((s) => s.companyId);
  const setCompanyId = useAuthStore((s) => s.setCompanyId);

  const { data: tenants, isLoading, isError } = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: () => admin.tenants(),
    staleTime: 60_000,
  });

  const current = tenants?.find((t) => t.slug === companyId);
  const targetTenants = tenants?.filter((t) => t.source === "target") ?? [];
  const onboardedTenants = tenants?.filter((t) => t.source === "onboarded") ?? [];

  const handleSelect = (slug: string | null) => {
    setCompanyId(slug);
    setOpen(false);
    // Invalidate every company-scoped query so the feed reloads.
    queryClient.invalidateQueries();
  };

  return (
    <div className="relative flex items-center justify-center">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1"
        style={{
          fontSize: "13px",
          fontWeight: 600,
          color: COLORS.textPrimary,
          background: "none",
          border: "none",
          cursor: "pointer",
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span
          style={{
            maxWidth: "160px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {current?.name || "All Companies"}
        </span>
        <span style={{ fontSize: "10px", color: COLORS.textMuted }}>
          {open ? "\u25B2" : "\u25BC"}
        </span>
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div
            role="listbox"
            className="absolute top-8 z-50 bg-white rounded-lg border shadow-lg py-1"
            style={{
              minWidth: "260px",
              maxHeight: "440px",
              overflowY: "auto",
              boxShadow: "0px 4px 12px rgba(0,0,0,0.12)",
            }}
          >
            <button
              onClick={() => handleSelect(null)}
              className="w-full text-left px-4 py-2 hover:bg-gray-50"
              style={{
                fontSize: "13px",
                color: !companyId ? COLORS.brand : COLORS.textPrimary,
                fontWeight: !companyId ? 600 : 400,
              }}
            >
              All Companies
            </button>

            {isLoading && (
              <div
                className="px-4 py-2"
                style={{ fontSize: "12px", color: COLORS.textMuted }}
              >
                Loading tenants…
              </div>
            )}

            {isError && (
              <div
                className="px-4 py-2"
                style={{ fontSize: "12px", color: COLORS.riskHigh }}
              >
                Couldn't load tenants
              </div>
            )}

            {targetTenants.length > 0 && (
              <div
                className="px-4 pt-2 pb-1 mt-1 border-t"
                style={{
                  fontSize: "10px",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: COLORS.textMuted,
                  fontWeight: 600,
                }}
              >
                Target companies
              </div>
            )}
            {targetTenants.map((t) => (
              <TenantRow key={t.slug} tenant={t} active={companyId === t.slug} onSelect={handleSelect} />
            ))}

            {onboardedTenants.length > 0 && (
              <div
                className="px-4 pt-2 pb-1 mt-1 border-t"
                style={{
                  fontSize: "10px",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: COLORS.textMuted,
                  fontWeight: 600,
                }}
              >
                Onboarded prospects ({onboardedTenants.length})
              </div>
            )}
            {onboardedTenants.map((t) => (
              <TenantRow key={t.slug} tenant={t} active={companyId === t.slug} onSelect={handleSelect} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function TenantRow({
  tenant,
  active,
  onSelect,
}: {
  tenant: AdminTenant;
  active: boolean;
  onSelect: (slug: string) => void;
}) {
  return (
    <button
      onClick={() => onSelect(tenant.slug)}
      className="w-full text-left px-4 py-2 hover:bg-gray-50 flex items-center justify-between gap-2"
      style={{ fontSize: "13px" }}
    >
      <span
        style={{
          color: active ? COLORS.brand : COLORS.textPrimary,
          fontWeight: active ? 600 : 400,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {tenant.name}
      </span>
      {/* Phase 18 — show an explicit "(empty)" badge for newly-onboarded
          tenants whose articles haven't finished indexing yet, so the
          super-admin doesn't think the dropdown is broken when the count
          is just 0. */}
      {typeof tenant.article_count === "number" && tenant.article_count > 0 ? (
        <span style={{ fontSize: "10px", color: COLORS.textMuted, flexShrink: 0 }}>
          {tenant.article_count}
        </span>
      ) : tenant.source === "onboarded" ? (
        <span style={{
          fontSize: "9px",
          color: COLORS.textMuted,
          fontStyle: "italic",
          flexShrink: 0,
        }}>
          empty
        </span>
      ) : null}
    </button>
  );
}
