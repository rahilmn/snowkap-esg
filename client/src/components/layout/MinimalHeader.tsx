/**
 * MinimalHeader — top bar shown on every authenticated page.
 *
 * Layout: [logo] [company name / switcher] [avatar dropdown].
 *
 * Two surfaces depending on whether the caller is a Snowkap super-admin:
 *
 *   - **Super-admin** (allowlisted via SNOWKAP_INTERNAL_EMAILS): renders the
 *     full `<CompanySwitcher />` listing every target company + every
 *     onboarded prospect, with an "All Companies" entry that sets
 *     `companyId=null`. This is the cross-tenant view.
 *
 *   - **Regular user**: renders the user's own company name as plain text.
 *     There is intentionally NO dropdown and NO "All Companies" option —
 *     the cross-tenant view is restricted to Snowkap staff. The backend
 *     also enforces this on `/news/feed` and `/news/stats`, so any client
 *     that tries to omit `company_id` will get a 403.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useAuthStore, useIsSalesAdmin, useIsSuperAdmin } from "@/stores/authStore";
import { companies as companiesApi } from "@/lib/api";
import { COLORS } from "@/lib/designTokens";
import { CompanySwitcher } from "@/components/admin/CompanySwitcher";
import { PerspectiveSwitcher } from "@/components/PerspectiveSwitcher";
import { RoleViewSwitcher } from "@/components/admin/RoleViewSwitcher";

export function MinimalHeader() {
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  const name = useAuthStore((s) => s.name);
  const companyId = useAuthStore((s) => s.companyId);
  const logout = useAuthStore((s) => s.logout);
  const isSuperAdmin = useIsSuperAdmin();
  // Phase 24.1: ONLY the Sales admin sees the cross-tenant
  // CompanySwitcher (with the "All Companies" option). Other Snowkap
  // super-admins (ci@, newsletter@, etc.) see their own bound tenant
  // as plain text — they keep super_admin permissions for onboarding /
  // sharing but don't get the aggregated dashboard.
  const isSalesAdmin = useIsSalesAdmin();

  // Regular users get a fixed company name (their own). Skip the network
  // round-trip for the sales admin — they get their list via CompanySwitcher.
  const { data: companyList } = useQuery({
    queryKey: ["companies"],
    queryFn: () => companiesApi.list(),
    staleTime: 60_000 * 60,
    enabled: !isSalesAdmin,
  });

  const initials = name
    ? name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)
    : "SN";

  // Resolve the user's own company name. Falls back to the slug-cased
  // version of `companyId` so a brand-new prospect (whose slug isn't in
  // the curated companies list) still sees a recognisable label.
  const ownCompanyLabel =
    companyList?.find((c) => c.slug === companyId)?.name
    ?? (companyId
      ? companyId.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      : "");

  return (
    <div
      className="sticky top-0 z-40 bg-white border-b border-gray-100"
      style={{ maxWidth: "440px", margin: "0 auto" }}
    >
      {/* Row 1: Logo + center label + avatar dropdown */}
      <header
        className="h-12 grid grid-cols-3 items-center"
        style={{ paddingLeft: "12px", paddingRight: "16px" }}
      >
        {/* Left: Logo */}
        <div className="flex items-center">
          <img src="/assets/snowkap-icon.png" alt="Snowkap" style={{ width: "36px", height: "36px" }} />
        </div>

        {/* Center: Company label.
            - Sales admin (sales@snowkap.co.in) sees the cross-tenant
              CompanySwitcher (All Companies + every tenant).
            - Everyone else (regular customers AND other Snowkap super-admins
              like ci@, newsletter@) sees their OWN company name as plain
              text. No dropdown, no "All Companies" — the cross-tenant view
              is sales-only per Phase 24.1. */}
        {isSalesAdmin ? (
          <CompanySwitcher />
        ) : (
          <div className="flex items-center justify-center">
            <span
              style={{
                fontSize: "13px",
                fontWeight: 600,
                color: COLORS.textPrimary,
                maxWidth: "180px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={ownCompanyLabel || undefined}
            >
              {ownCompanyLabel || "Snowkap"}
            </span>
          </div>
        )}

        {/* Right: Avatar dropdown */}
        <div className="flex items-center justify-end">
          <div className="relative">
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              className="flex items-center justify-center text-xs font-bold"
              style={{
                width: "34px",
                height: "34px",
                borderRadius: "50%",
                backgroundColor: COLORS.brandLight,
                color: COLORS.brand,
                border: "none",
                cursor: "pointer",
              }}
              title={name || "Account"}
              aria-label="User menu"
            >
              {initials}
            </button>
            {menuOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
                <div
                  className="absolute right-0 top-10 z-50 bg-white rounded-lg border shadow-lg py-1"
                  style={{ minWidth: "180px", boxShadow: "0px 4px 12px rgba(0,0,0,0.12)" }}
                >
                  <button
                    onClick={() => { setMenuOpen(false); navigate("/preferences"); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50"
                    style={{ fontSize: "13px", color: COLORS.textPrimary }}
                  >
                    Preferences
                  </button>
                  {isSuperAdmin && (
                    <>
                      <button
                        onClick={() => { setMenuOpen(false); navigate("/settings/campaigns"); }}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50"
                        style={{ fontSize: "13px", color: COLORS.textPrimary }}
                      >
                        Campaigns
                      </button>
                      <button
                        onClick={() => { setMenuOpen(false); navigate("/settings/onboard"); }}
                        className="w-full text-left px-4 py-2 hover:bg-gray-50"
                        style={{ fontSize: "13px", color: COLORS.textPrimary }}
                      >
                        Onboard company
                      </button>
                    </>
                  )}
                  <div className="border-t my-1" />
                  <button
                    onClick={() => { setMenuOpen(false); logout(); navigate("/login"); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50"
                    style={{ fontSize: "13px", color: "#ff4044" }}
                  >
                    Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Row 2: Perspective switcher — globally accessible cognitive lens.
          Super-admins also see a "View as role" dropdown next to it which
          drives the default perspective panel on article detail (Phase D). */}
      <div className="flex items-center justify-center gap-3 py-1.5 border-t border-gray-50">
        <PerspectiveSwitcher />
        {isSuperAdmin && <RoleViewSwitcher />}
      </div>
    </div>
  );
}
