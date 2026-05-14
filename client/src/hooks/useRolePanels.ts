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
 *   - `order`: the canonical render order for the role (frontend can use this
 *      later to actually reorder the JSX; today we only use it to prune).
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

export function useRolePanels(
  perspective: string | null | undefined,
  rolePanelOrder: RolePanelOrder | undefined | null,
): { isHidden: (panelId: string) => boolean; order: string[] } {
  return useMemo(() => {
    const role = ROLE_KEY[(perspective || "").trim().toLowerCase()];
    const block = role ? rolePanelOrder?.[role] : undefined;
    const hidden = new Set(block?.hidden ?? []);
    const order = block?.order ?? [];
    return {
      isHidden: (panelId: string) => hidden.has(panelId),
      order,
    };
  }, [perspective, rolePanelOrder]);
}
