/** Minimal header — Phase 12: adds a PerspectiveSwitcher sub-bar below the
 * top row so the CFO / CEO / ESG Analyst lens is globally accessible. The
 * Admin and Campaigns menu items are removed (Hybrid scope).
 * Phase 16: adds company selector dropdown for switching between companies.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore, useIsSuperAdmin } from "@/stores/authStore";
import { companies as companiesApi } from "@/lib/api";
import { COLORS } from "../../lib/designTokens";
import { PerspectiveSwitcher } from "@/components/PerspectiveSwitcher";
import { CompanySwitcher } from "@/components/admin/CompanySwitcher";
import { RoleViewSwitcher } from "@/components/admin/RoleViewSwitcher";

export function MinimalHeader() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const name = useAuthStore((s) => s.name);
  const logout = useAuthStore((s) => s.logout);
  const companyId = useAuthStore((s) => s.companyId);
  const setCompanyId = useAuthStore((s) => s.setCompanyId);
  const isSuperAdmin = useIsSuperAdmin();
  const [menuOpen, setMenuOpen] = useState(false);
  const [companyOpen, setCompanyOpen] = useState(false);

  // Non-admins get the 7-target list; admins use CompanySwitcher which pulls
  // from /api/admin/tenants (targets + every onboarded prospect).
  const { data: companyList } = useQuery({
    queryKey: ["companies"],
    queryFn: () => companiesApi.list(),
    staleTime: 60_000 * 60,
    enabled: !isSuperAdmin,
  });

  const initials = name
    ? name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)
    : "SN";

  return (
    <div
      className="sticky top-0 z-40 bg-white border-b border-gray-100"
      style={{ maxWidth: "440px", margin: "0 auto" }}
    >
      {/* Row 1: Logo + SNOWKAP text + avatar dropdown */}
      <header
        className="h-12 grid grid-cols-3 items-center"
        style={{ paddingLeft: "12px", paddingRight: "16px" }}
      >
        {/* Left: Logo */}
        <div className="flex items-center">
          <img src="/assets/snowkap-icon.png" alt="Snowkap" style={{ width: "36px", height: "36px" }} />
        </div>

        {/* Center: Company selector. Super-admins see the full list from
            /api/admin/tenants (targets + every onboarded prospect). Regular
            users see the 7 hardcoded targets. */}
        {isSuperAdmin ? (
          <CompanySwitcher />
        ) : (
          <div className="relative flex items-center justify-center">
            <button
              onClick={() => setCompanyOpen(!companyOpen)}
              className="flex items-center gap-1"
              style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textPrimary, background: "none", border: "none", cursor: "pointer" }}
            >
              <span style={{ maxWidth: "140px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {companyList?.find((c) => c.slug === companyId)?.name || "All Companies"}
              </span>
              <span style={{ fontSize: "10px", color: COLORS.textMuted }}>{companyOpen ? "\u25B2" : "\u25BC"}</span>
            </button>

            {companyOpen && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setCompanyOpen(false)} />
                <div
                  className="absolute top-8 z-50 bg-white rounded-lg border shadow-lg py-1"
                  style={{ minWidth: "200px", boxShadow: "0px 4px 12px rgba(0,0,0,0.12)" }}
                >
                  <button
                    onClick={() => { setCompanyId(null); setCompanyOpen(false); queryClient.invalidateQueries(); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50"
                    style={{ fontSize: "13px", color: !companyId ? COLORS.brand : COLORS.textPrimary, fontWeight: !companyId ? 600 : 400 }}
                  >
                    All Companies
                  </button>
                  {companyList?.map((c) => (
                    <button
                      key={c.slug}
                      onClick={() => { setCompanyId(c.slug); setCompanyOpen(false); queryClient.invalidateQueries(); }}
                      className="w-full text-left px-4 py-2 hover:bg-gray-50"
                      style={{ fontSize: "13px", color: companyId === c.slug ? COLORS.brand : COLORS.textPrimary, fontWeight: companyId === c.slug ? 600 : 400 }}
                    >
                      {c.name}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* Right: Profile avatar with dropdown */}
        <div className="relative flex justify-end">
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="flex items-center justify-center text-xs font-bold"
            style={{
              width: "34px",
              height: "34px",
              borderRadius: "50%",
              backgroundColor: COLORS.brandLight,
              color: COLORS.brand,
            }}
            title={name || "Account"}
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
                <div className="px-4 py-2 border-b" style={{ color: COLORS.textSecondary, fontSize: "12px" }}>
                  {name || "User"}
                </div>

                <button
                  onClick={() => { setMenuOpen(false); navigate("/preferences"); }}
                  className="w-full text-left px-4 py-2 hover:bg-gray-50"
                  style={{ fontSize: "14px", color: COLORS.textPrimary }}
                >
                  Preferences
                </button>

                {isSuperAdmin && (
                  <button
                    onClick={() => { setMenuOpen(false); navigate("/settings/campaigns"); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50"
                    style={{ fontSize: "14px", color: COLORS.textPrimary }}
                  >
                    Drip campaigns
                  </button>
                )}

                <div className="border-t" />

                <button
                  onClick={() => { setMenuOpen(false); logout(); }}
                  className="w-full text-left px-4 py-2 hover:bg-gray-50"
                  style={{ fontSize: "14px", color: COLORS.riskHigh }}
                >
                  Sign Out
                </button>
              </div>
            </>
          )}
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
