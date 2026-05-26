/** W4e — role-aware panel visibility hook.
 *
 * Reads the `role_panel_order` block stamped onto every insight by the
 * backend (W4d) and returns helpers the ArticleDetailSheet uses to
 * render only the panels that matter for the active perspective.
 *
 * Backend shape (engine/analysis/insight_generator.py::DeepInsight):
 *   role_panel_order: {
 *     cfo:          { order: string[], hidden: string[] },
 *     ceo:          { order: string[], hidden: string[] },
 *     "esg-analyst": { order: string[], hidden: string[] },
 *   }
 *
 * Returns:
 *   - `isHidden(panelId)`: true when the panel should be suppressed for the
 *      active role. Falls open (returns false) when the insight predates W4d
 *      or the role isn't in the map — preserves legacy behaviour.
 *   - `isHiddenOnMobile(panelId)`: Phase 28 / Feature 5 — true when the
 *      panel is non-essential on mobile for the active role. Hides the
 *      "endless scroll" panels on small screens; desktop still shows
 *      everything. Caller AND-combines this with the mobile detector
 *      (`useIsMobile`) so desktop is unaffected.
 *   - `order`: the canonical render order for the role.
 */

import { useMemo } from "react";

type RolePanels = { order: string[]; hidden: string[] };
type RolePanelOrder = { cfo?: RolePanels; ceo?: RolePanels; "esg-analyst"?: RolePanels };

const ROLE_KEY: Record<string, keyof RolePanelOrder> = {
  cfo: "cfo",
  ceo: "ceo",
  "esg-analyst": "esg-analyst",
  esg_analyst: "esg-analyst",
  analyst: "esg-analyst",
};

/** Phase 28 / Feature 5 — mobile-essential panel sets per role.
 *
 * Panels NOT in the role's set are treated as `isHiddenOnMobile = true`
 * so the ArticleDetailSheet renders ≤4-5 panels on small screens.
 *
 * Future: lift into ontology TTL (`:mobileVisibility "essential"`) so
 * this list isn't hardcoded. For now the deterministic baseline ships
 * with sensible per-role defaults that mirror the Phase-26 W4d
 * `role_panel_order` essentials.
 *
 * Panel IDs MUST match the canonical IDs used by the backend
 * `role_panel_order` (kept in sync with
 * `engine/ontology/role_panel_priority.py`).
 */
const MOBILE_ESSENTIAL_PANELS: Record<keyof RolePanelOrder, ReadonlySet<string>> = {
  cfo: new Set([
    "headline",
    "key_takeaways",
    "financial_impact",
    "actions",
    "why_critical",
  ]),
  ceo: new Set([
    "headline",
    "key_takeaways",
    "competitive_position",
    "actions",
    "why_critical",
  ]),
  "esg-analyst": new Set([
    "headline",
    "key_takeaways",
    "framework_alignment",
    "evidence_pack",
    "actions",
  ]),
};

export function useRolePanels(
  perspective: string | null | undefined,
  rolePanelOrder: RolePanelOrder | undefined | null,
): {
  isHidden: (panelId: string) => boolean;
  isHiddenOnMobile: (panelId: string) => boolean;
  order: string[];
} {
  return useMemo(() => {
    const role = ROLE_KEY[(perspective || "").trim().toLowerCase()];
    const block = role ? rolePanelOrder?.[role] : undefined;
    const hidden = new Set(block?.hidden ?? []);
    const order = block?.order ?? [];
    const mobileEssential = role ? MOBILE_ESSENTIAL_PANELS[role] : null;
    return {
      isHidden: (panelId: string) => hidden.has(panelId),
      isHiddenOnMobile: (panelId: string) => {
        if (!mobileEssential) return false;
        return !mobileEssential.has(panelId);
      },
      order,
    };
  }, [perspective, rolePanelOrder]);
}
