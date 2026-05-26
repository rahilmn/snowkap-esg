/** Phase 29 — Per-role essential blocks + filter helpers.
 *
 * Single source of truth for what each role sees up-front. Mirrors the
 * table in §29.2 of the plan. Caller decides the layout; this hook
 * just exposes the rules.
 *
 * Three things live here:
 *   1. `ESSENTIAL_BLOCKS[role]` — set of block ids the role sees up-front.
 *   2. `getRecommendationLensesFor(role)` — which `Recommendation.type`
 *      values to keep when filtering AI Recommendations.
 *   3. `getRiskCategoriesFor(role)` — which TEMPLES categories to keep
 *      when filtering the Risk Assessment table (CFO only).
 *   4. `getImpactAnalysisKeysFor(role)` — which Impact-Analysis sub-blocks
 *      to render up-front.
 *
 * Constants are exported so tests can pin them.
 */

export type Role = "cfo" | "ceo" | "esg-analyst";

/** Canonical block ids. These match the per-panel methodology entries
 * in `engine/analysis/methodology_provenance.py:METRIC_DISPATCH` so the
 * info popover can look up methodology for any block by id. */
export const BLOCK_IDS = {
  // Role-specific
  ROLE_HEADLINE: "role_headline",
  ROLE_TAKEAWAYS: "role_takeaways",
  ROLE_ACTION: "role_action",
  // CEO-only
  BOARD_PARAGRAPH: "board_paragraph",
  STAKEHOLDER_MAP: "stakeholder_map",
  THREE_YEAR_TRAJECTORY: "three_year_trajectory",
  COMMUNICATIONS_SCRIPTS: "communications_scripts",
  // CFO-only
  ONTOLOGY_DIMENSIONS: "ontology_dimensions",
  // Analyst-only
  KPI_TABLE: "kpi_table",
  // Common
  KEY_TAKEAWAYS: "key_takeaways",
  FINANCIAL_IMPACT: "financial_impact",
  RISK_MATRIX: "risk_matrix",
  ESG_RELEVANCE_SCORE: "esg_relevance_score",
  IMPACT_ANALYSIS: "impact_analysis",
  FRAMEWORK_ALIGNMENT: "framework_alignment",
  AI_RECOMMENDATIONS: "ai_recommendations",
  EXECUTIVE_INSIGHT: "executive_insight",
} as const;

export type BlockId = typeof BLOCK_IDS[keyof typeof BLOCK_IDS];

/**
 * Per-role essential block set. Anything NOT in here for a given role
 * goes into the "Full analysis" collapsed accordion at the bottom of
 * the article detail sheet.
 *
 * Source: §29.2 of the approved plan.
 */
export const ESSENTIAL_BLOCKS: Record<Role, ReadonlySet<BlockId>> = {
  cfo: new Set<BlockId>([
    "role_headline",           // "P&L exposure" 1-liner + 3 tags
    "role_takeaways",          // "What matters for CFO" (3 bullets)
    "role_action",             // "Action" 2-liner
    "ontology_dimensions",     // CFO-only tags row
    "key_takeaways",           // common, kept
    "financial_impact",        // common, kept for CFO
    "risk_matrix",             // common, filtered to financial cats (see getRiskCategoriesFor)
    "impact_analysis",         // common, filtered (see getImpactAnalysisKeysFor)
    "ai_recommendations",      // common, filtered (see getRecommendationLensesFor)
    "executive_insight",
  ]),
  ceo: new Set<BlockId>([
    "role_headline",           // "Strategic angle" 1-liner + 3 tags
    "board_paragraph",         // CEO-only
    "stakeholder_map",         // CEO-only
    "three_year_trajectory",   // CEO-only
    "communications_scripts",  // CEO-only, RENDERED collapsed
    "key_takeaways",
    "impact_analysis",
    "ai_recommendations",
    "executive_insight",
  ]),
  "esg-analyst": new Set<BlockId>([
    "role_headline",
    "kpi_table",               // Analyst-only
    "key_takeaways",
    "esg_relevance_score",     // Analyst sees full 6D
    "risk_matrix",             // Analyst sees full table
    "impact_analysis",
    "framework_alignment",     // Analyst-only
    "ai_recommendations",
    "executive_insight",
  ]),
};

/** Returns true when `blockId` is in the active role's essential set. */
export function isEssential(role: Role, blockId: BlockId): boolean {
  return ESSENTIAL_BLOCKS[role].has(blockId);
}


/**
 * AI Recommendations filter — which `Recommendation.type` values does
 * the role see up-front? Recommendations of other types are hidden
 * behind a "Show all N recommendations" CTA.
 *
 * Mirrors §29.5 of the plan. The `type` field comes from
 * `engine/analysis/recommendation_engine.py:Recommendation.type` which
 * is one of `strategic | financial | esg_positioning | operational |
 * compliance`.
 */
export const RECOMMENDATION_LENSES_BY_ROLE: Record<Role, ReadonlySet<string>> = {
  cfo: new Set(["financial", "operational"]),
  ceo: new Set(["strategic", "esg_positioning"]),
  "esg-analyst": new Set(["compliance", "esg_positioning"]),
};

export function getRecommendationLensesFor(role: Role): ReadonlySet<string> {
  return RECOMMENDATION_LENSES_BY_ROLE[role];
}


/**
 * Risk Assessment filter — which TEMPLES categories does the CFO see?
 * CEO sees an accordion-only summary (handled by the layout, not this
 * filter). Analyst sees ALL 7 — function returns null to signal "no filter".
 */
export const RISK_CATEGORIES_BY_ROLE: Record<Role, ReadonlySet<string> | null> = {
  cfo: new Set(["Economic", "Market & Uncertainty", "Supply Chain Risk"]),
  ceo: null,            // not in essentials, but if shown via accordion, no filter
  "esg-analyst": null,  // full table
};

export function getRiskCategoriesFor(role: Role): ReadonlySet<string> | null {
  return RISK_CATEGORIES_BY_ROLE[role];
}


/**
 * Impact Analysis sub-block filter — which of the 6 sub-blocks does
 * each role see up-front? Hidden ones surface behind a "Show all 6
 * dimensions" CTA.
 *
 * Sub-block ids come from `DeepInsight.impact_analysis` keys:
 * `esg_positioning`, `capital_allocation`, `valuation_cashflow`,
 * `compliance_regulatory`, `supply_chain_transmission`, `people_demand`.
 */
export const IMPACT_KEYS_BY_ROLE: Record<Role, ReadonlySet<string>> = {
  cfo: new Set(["valuation_cashflow", "capital_allocation"]),
  ceo: new Set(["esg_positioning", "people_demand"]),
  "esg-analyst": new Set(["compliance_regulatory"]),
};

export function getImpactAnalysisKeysFor(role: Role): ReadonlySet<string> {
  return IMPACT_KEYS_BY_ROLE[role];
}


// Apply functions — given the raw block + role, return the filtered
// projection + a flag saying whether anything was hidden.


export interface FilterResult<T> {
  visible: T[];
  hiddenCount: number;
}


export function filterRecommendations<T extends { type?: string }>(
  recs: T[],
  role: Role,
): FilterResult<T> {
  const lenses = getRecommendationLensesFor(role);
  const visible = recs.filter(r => r.type && lenses.has(r.type));
  return { visible, hiddenCount: Math.max(0, recs.length - visible.length) };
}


export function filterRiskCategories<T extends { category?: string }>(
  risks: T[],
  role: Role,
): FilterResult<T> {
  const cats = getRiskCategoriesFor(role);
  if (cats === null) {
    // No filter — analyst etc. sees everything.
    return { visible: risks, hiddenCount: 0 };
  }
  const visible = risks.filter(r => r.category && cats.has(r.category));
  return { visible, hiddenCount: Math.max(0, risks.length - visible.length) };
}


export function filterImpactAnalysis(
  impact: Record<string, string | undefined> | null | undefined,
  role: Role,
): { visible: Record<string, string>; hiddenCount: number } {
  if (!impact) return { visible: {}, hiddenCount: 0 };
  const keys = getImpactAnalysisKeysFor(role);
  const visible: Record<string, string> = {};
  let hiddenCount = 0;
  for (const [k, v] of Object.entries(impact)) {
    if (!v) continue;
    if (keys.has(k)) {
      visible[k] = v;
    } else {
      hiddenCount += 1;
    }
  }
  return { visible, hiddenCount };
}


// React hook wrapper — gives the layout component everything it needs
// in one call. Stateless; pure derivation.

export function useRoleEssentials(role: Role | null | undefined) {
  const safeRole: Role = (role ?? "cfo") as Role;
  return {
    role: safeRole,
    isEssential: (id: BlockId) => isEssential(safeRole, id),
    essentialBlocks: ESSENTIAL_BLOCKS[safeRole],
    recommendationLenses: getRecommendationLensesFor(safeRole),
    riskCategories: getRiskCategoriesFor(safeRole),
    impactKeys: getImpactAnalysisKeysFor(safeRole),
  };
}
