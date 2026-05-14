/** Phase 25 W10 — "Why this matters to YOU" personal stakes card.
 *
 * The "wow factor" surface. Renders the W9 stakes_for_company block as
 * the FIRST scrollable element under the article hero, BEFORE the
 * perspective switcher and the legacy panels. A CFO opening a CRITICAL
 * article should see this without scrolling.
 *
 * Renders nothing when:
 *   - `stakes_for_company` is empty (LLM failed, REJECTED article,
 *     SECONDARY tier without on-demand re-pipeline, etc.)
 *   - Both prose blocks are missing (pure metadata = no value)
 *
 * Layout:
 *   ┌──────────────────────────────────────────────┐
 *   │ Why this matters to YOU                      │
 *   │ ─────────────────────────────  6.3% ↗        │
 *   │                              of FY revenue   │
 *   │                              at stake        │
 *   │ ┌──────────────────────────────────────────┐ │
 *   │ │ 80-120 word personal-stakes paragraph    │ │
 *   │ │ in serif large legible body              │ │
 *   │ └──────────────────────────────────────────┘ │
 *   │                                              │
 *   │ ┌──────────────┐ ┌────────────────────────┐ │
 *   │ │ Peers did    │ │ If you do nothing      │ │
 *   │ │ Tata Power...│ │ ESG fund divestment... │ │
 *   │ └──────────────┘ └────────────────────────┘ │
 *   └──────────────────────────────────────────────┘
 */

import type { ReactNode } from "react";

interface StakesForCompany {
  personal_stakes_paragraph?: string;
  revenue_pct_at_stake?: number | null;
  peer_action_summary?: string;
  do_nothing_risk_paragraph?: string;
}

interface DeepInsightWithStakes {
  stakes_for_company?: StakesForCompany | null;
  event_polarity?: "positive" | "negative" | "neutral";
}

export function PersonalStakesCard({
  insight,
}: {
  insight: DeepInsightWithStakes | null | undefined;
}) {
  const stakes = insight?.stakes_for_company;
  const polarity = insight?.event_polarity ?? "neutral";

  // Fail-safe: render nothing when we have no prose to show
  const hasPersonal = !!stakes?.personal_stakes_paragraph?.trim();
  const hasRisk = !!stakes?.do_nothing_risk_paragraph?.trim();
  if (!stakes || (!hasPersonal && !hasRisk)) {
    return null;
  }

  // Polarity-aware accent: red border for negative, emerald for positive
  const accentColor =
    polarity === "positive" ? "#16A34A" : polarity === "negative" ? "#DC2626" : "#DF5900";
  const accentTint =
    polarity === "positive" ? "rgba(22,163,74,0.06)" :
    polarity === "negative" ? "rgba(220,38,38,0.06)" : "rgba(223,89,0,0.06)";

  return (
    <div
      className="rounded-xl border-l-4 bg-white shadow-sm"
      style={{
        borderLeftColor: accentColor,
        backgroundColor: accentTint,
      }}
      data-testid="personal-stakes-card"
    >
      {/* Header band */}
      <div className="flex items-start justify-between gap-3 px-5 py-4">
        <div>
          <div
            className="text-[11px] font-semibold uppercase tracking-wider"
            style={{ color: accentColor }}
          >
            Why this matters to YOU
          </div>
          <div className="mt-1 text-xs text-slate-500">
            {polarity === "positive"
              ? "Opportunity windows you can capture"
              : polarity === "negative"
              ? "Risk exposure specific to your business"
              : "Decision context tied to your operations"}
          </div>
        </div>
        {/* Revenue % badge */}
        {typeof stakes.revenue_pct_at_stake === "number" &&
          stakes.revenue_pct_at_stake > 0 && (
            <RevenueBadge pct={stakes.revenue_pct_at_stake} accentColor={accentColor} />
          )}
      </div>

      {/* Personal stakes prose */}
      {hasPersonal && (
        <div className="px-5 pb-4">
          <div
            className="rounded-lg bg-white p-4 text-[15px] leading-relaxed text-slate-800"
            style={{
              fontFamily:
                "'Iowan Old Style', 'Apple Garamond', 'Baskerville', 'Times New Roman', serif",
            }}
          >
            {stakes.personal_stakes_paragraph}
          </div>
        </div>
      )}

      {/* Side cards: peers + do-nothing risk */}
      {(stakes.peer_action_summary || stakes.do_nothing_risk_paragraph) && (
        <div className="grid gap-3 px-5 pb-5 sm:grid-cols-2">
          {stakes.peer_action_summary?.trim() && (
            <SideCard
              title="What peers did"
              body={stakes.peer_action_summary}
              tone="slate"
            />
          )}
          {stakes.do_nothing_risk_paragraph?.trim() && (
            <SideCard
              title={
                polarity === "positive"
                  ? "If you don't move"
                  : "If you do nothing"
              }
              body={stakes.do_nothing_risk_paragraph}
              tone={polarity === "positive" ? "amber" : "rose"}
            />
          )}
        </div>
      )}
    </div>
  );
}

function RevenueBadge({
  pct,
  accentColor,
}: {
  pct: number;
  accentColor: string;
}) {
  // Display rules:
  //  < 0.5  -> "<0.5%" (de-emphasised, but show)
  //  0.5-99 -> "X.X%"
  //  >= 100 -> ">100%" (likely a calibration error; display anyway)
  const display =
    pct < 0.5 ? "<0.5%" : pct >= 100 ? ">100%" : `${pct.toFixed(1)}%`;

  return (
    <div className="text-right">
      <div
        className="text-2xl font-bold leading-none"
        style={{ color: accentColor }}
      >
        {display}
      </div>
      <div className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">
        of FY revenue
        <br />
        at stake
      </div>
    </div>
  );
}

function SideCard({
  title,
  body,
  tone,
}: {
  title: string;
  body: string;
  tone: "slate" | "amber" | "rose";
}) {
  const toneClasses = {
    slate: "border-slate-200 bg-white",
    amber: "border-amber-300 bg-amber-50",
    rose: "border-rose-300 bg-rose-50",
  }[tone];
  const titleClasses = {
    slate: "text-slate-500",
    amber: "text-amber-800",
    rose: "text-rose-800",
  }[tone];
  return (
    <div className={`rounded-lg border ${toneClasses} p-3`}>
      <div className={`text-[10px] font-semibold uppercase tracking-wider ${titleClasses}`}>
        {title}
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-slate-700">{body}</p>
    </div>
  );
}

// Helper for callers that pass an unknown-shape insight from API
export function extractStakesPayload(
  fullInsightOrPerspective: unknown
): DeepInsightWithStakes | null {
  if (!fullInsightOrPerspective || typeof fullInsightOrPerspective !== "object") {
    return null;
  }
  const obj = fullInsightOrPerspective as Record<string, unknown>;
  // Direct shape: deep_insight or { stakes_for_company, event_polarity }
  if (obj.stakes_for_company !== undefined || obj.event_polarity !== undefined) {
    return {
      stakes_for_company: obj.stakes_for_company as StakesForCompany | null,
      event_polarity: obj.event_polarity as DeepInsightWithStakes["event_polarity"],
    };
  }
  // Wrapped via full_insight (CrispView shape)
  const fi = obj.full_insight as Record<string, unknown> | undefined;
  if (fi && typeof fi === "object") {
    return {
      stakes_for_company: fi.stakes_for_company as StakesForCompany | null,
      event_polarity: fi.event_polarity as DeepInsightWithStakes["event_polarity"],
    };
  }
  return null;
}

// Re-export the type for consumers
export type { StakesForCompany, DeepInsightWithStakes };

// Small wrapper to silence the unused-ReactNode TS error when used in
// a project with strict isolatedModules
export type _PersonalStakesCardChildren = ReactNode;
