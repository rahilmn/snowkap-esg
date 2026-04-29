/** Phase 10 — RoleViewSwitcher
 *
 * Super-admin-only header dropdown: "View as: CFO | CEO | ESG Analyst | Member".
 *
 * Writes to authStore.viewAsRole. Consumed by `useActiveRole()` which is read
 * by ArticleDetailSheet (Phase D) to pick the default perspective panel.
 *
 * IMPORTANT: this is a UX default toggle, NOT impersonation. Backend still
 * enforces the actual JWT permissions regardless of what's set here — an
 * admin viewing "as Member" still has super_admin permissions and can still
 * hit admin endpoints. This is purely about which default panel opens when
 * the admin inspects an article.
 */

import { useState } from "react";
import { useAuthStore, type ViewAsRole } from "@/stores/authStore";
import { COLORS } from "@/lib/designTokens";

const ROLE_OPTIONS: { value: ViewAsRole; label: string; hint: string }[] = [
  { value: null, label: "My role", hint: "Use real designation from login" },
  { value: "cfo", label: "CFO", hint: "Decision-first, 10-second verdict" },
  { value: "ceo", label: "CEO", hint: "Strategic narrative" },
  { value: "esg-analyst", label: "ESG Analyst", hint: "Full detail view" },
  { value: "member", label: "Member", hint: "Basic feed only" },
];

function labelFor(role: ViewAsRole): string {
  const match = ROLE_OPTIONS.find((r) => r.value === role);
  return match?.label ?? "My role";
}

export function RoleViewSwitcher() {
  const [open, setOpen] = useState(false);
  const viewAsRole = useAuthStore((s) => s.viewAsRole);
  const setViewAsRole = useAuthStore((s) => s.setViewAsRole);

  const handleSelect = (role: ViewAsRole) => {
    setViewAsRole(role);
    setOpen(false);
  };

  return (
    <div className="relative inline-flex items-center">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium transition hover:border-slate-400"
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Super-admin only — switch default perspective panel"
      >
        <span style={{ fontSize: "10px", color: COLORS.textMuted, fontWeight: 500 }}>
          View as:
        </span>
        <span style={{ color: COLORS.textPrimary, fontWeight: 600 }}>
          {labelFor(viewAsRole)}
        </span>
        <span style={{ fontSize: "9px", color: COLORS.textMuted }}>
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
            className="absolute top-full mt-1 right-0 z-50 bg-white rounded-lg border shadow-lg py-1"
            style={{ minWidth: "220px", boxShadow: "0px 4px 12px rgba(0,0,0,0.12)" }}
          >
            {ROLE_OPTIONS.map((opt) => {
              const active = viewAsRole === opt.value;
              return (
                <button
                  key={opt.value ?? "real"}
                  onClick={() => handleSelect(opt.value)}
                  className="w-full text-left px-3 py-2 hover:bg-gray-50"
                  style={{
                    fontSize: "12px",
                    color: active ? COLORS.brand : COLORS.textPrimary,
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  <div>{opt.label}</div>
                  <div style={{ fontSize: "10px", color: COLORS.textMuted, marginTop: "1px" }}>
                    {opt.hint}
                  </div>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
