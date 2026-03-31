/** Minimal header — Phase 8: Snowkap logo + name + profile avatar with dropdown.
 * Bot icon, gear icon REMOVED. Settings moved to avatar dropdown.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { COLORS } from "../../lib/designTokens";

export function MinimalHeader() {
  const navigate = useNavigate();
  const name = useAuthStore((s) => s.name);
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const logout = useAuthStore((s) => s.logout);
  const [menuOpen, setMenuOpen] = useState(false);

  const initials = name
    ? name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)
    : "SN";

  return (
    <header
      className="h-12 grid grid-cols-3 items-center bg-white border-b border-gray-100 sticky top-0 z-40"
      style={{ maxWidth: "440px", margin: "0 auto", paddingLeft: "12px", paddingRight: "16px" }}
    >
      {/* Left: Logo */}
      <div className="flex items-center">
        <img src="/assets/snowkap-icon.png" alt="Snowkap" style={{ width: "36px", height: "36px" }} />
      </div>

      {/* Center: SNOWKAP text */}
      <div className="flex items-center justify-center">
        <span
          className="font-semibold"
          style={{ fontSize: "14px", color: COLORS.textPrimary, letterSpacing: "0.05em" }}
        >
          SNOWKAP
        </span>
      </div>

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

        {/* Dropdown menu */}
        {menuOpen && (
          <>
            {/* Backdrop */}
            <div
              className="fixed inset-0 z-40"
              onClick={() => setMenuOpen(false)}
            />
            {/* Menu */}
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

              {hasPermission("platform_admin") && (
                <button
                  onClick={() => { setMenuOpen(false); navigate("/admin"); }}
                  className="w-full text-left px-4 py-2 hover:bg-gray-50"
                  style={{ fontSize: "14px", color: COLORS.textPrimary }}
                >
                  Admin
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
  );
}
