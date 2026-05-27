/** Phase 32 — UnifiedAnalysisCard
 *
 * Single, horizontally-consumable analysis card that replaces the
 * per-role split (CFO / CEO / ESG Analyst). Renders four bullets in
 * news-flow order:
 *
 *   1. What changed       — the event itself
 *   2. Why it matters     — industry/company materiality + ₹ stakes
 *   3. What it triggers   — concrete obligations (frameworks + actions)
 *   4. What to watch      — forward signal (trajectory + indicators + benchmarks)
 *
 * Each bullet has an `(i)` icon that opens the MethodologyDrawer scoped
 * to that bullet's methodology entry. No role toggle anywhere.
 *
 * Hidden entirely when `analysis` is null/undefined (pre-Phase-32 article
 * that hasn't been re-enriched yet — ArticleDetailSheet falls back to the
 * legacy role view in that case).
 */
import { useState } from "react";
import type {
  UnifiedAnalysis,
  UnifiedAnalysisWhatChanged,
  UnifiedAnalysisWhyItMatters,
  UnifiedAnalysisWhatItTriggers,
  UnifiedAnalysisWhatToWatch,
} from "@/types";
import { COLORS } from "@/lib/designTokens";
import { MethodologyDrawer } from "@/components/explainer/MethodologyDrawer";
import { EditorialLede } from "@/components/insight/EditorialLede";

type BulletKey = "what_changed" | "why_it_matters" | "what_it_triggers" | "what_to_watch";

interface Props {
  analysis: UnifiedAnalysis;
  articleId: string;
}

const BAND_TINT: Record<string, { bg: string; fg: string; border: string }> = {
  CRITICAL: { bg: "#FEF2F2", fg: "#991B1B", border: "#FECACA" },
  HIGH: { bg: "#FFF7ED", fg: "#9A3412", border: "#FED7AA" },
  MEDIUM: { bg: "#FFFBEB", fg: "#92400E", border: "#FDE68A" },
  LOW: { bg: "#F0FDF4", fg: "#065F46", border: "#BBF7D0" },
};

const POLARITY_TINT: Record<string, { bg: string; fg: string }> = {
  positive: { bg: "#DCFCE7", fg: "#15803D" },
  negative: { bg: "#FEE2E2", fg: "#B91C1C" },
  neutral: { bg: "#E5E7EB", fg: "#374151" },
};

const TRAJECTORY_TINT: Record<string, { fg: string; symbol: string }> = {
  declining: { fg: "#B91C1C", symbol: "↓" },
  stable: { fg: "#6B7280", symbol: "→" },
  improving: { fg: "#15803D", symbol: "↑" },
};

/**
 * Phase 33 — translate the 3-horizon trajectory shape into a plain-English
 * sentence so readers don't have to decode "3m → stable / 6m → stable /
 * 12m → stable" on their own.
 */
function explainTrajectory(h3?: string, h6?: string, h12?: string, confidence?: string): string {
  const valid = (s?: string) => !!s && (s === "declining" || s === "stable" || s === "improving");
  const conf = (confidence || "").toLowerCase();
  const confPhrase = conf === "high" ? "we're confident in this read"
    : conf === "medium" ? "medium-confidence read"
    : conf === "low" ? "low confidence — treat as a thin signal"
    : "";

  if (!valid(h3) && !valid(h6) && !valid(h12)) {
    return "Not enough historical news to project a trajectory yet — keep watching.";
  }

  // All-same shortcuts
  if (h3 === "stable" && h6 === "stable" && h12 === "stable") {
    return `No major sentiment shift expected over the next year — keep watching, but no urgent action needed.${confPhrase ? ` (${confPhrase}.)` : ""}`;
  }
  if (h3 === "declining" && h6 === "declining" && h12 === "declining") {
    return `Sentiment is trending downward across all three horizons — plan a response narrative for the next investor touchpoint.${confPhrase ? ` (${confPhrase}.)` : ""}`;
  }
  if (h3 === "improving" && h6 === "improving" && h12 === "improving") {
    return `Sentiment is improving across all three horizons — reinforce the recovery narrative in the next earnings cycle.${confPhrase ? ` (${confPhrase}.)` : ""}`;
  }

  // Mixed
  if (h3 === "declining" && h12 === "improving") {
    return `Short-term headwind, but the longer-term trajectory recovers. Lead with the recovery narrative externally; brief the board on the near-term dip.${confPhrase ? ` (${confPhrase}.)` : ""}`;
  }
  if (h3 === "improving" && h12 === "declining") {
    return `Near-term boost but headed downhill — capitalise on the current opening while planning for the medium-term reversal.${confPhrase ? ` (${confPhrase}.)` : ""}`;
  }

  // Fallback dynamic sentence
  const segments: string[] = [];
  if (valid(h3)) segments.push(`3 months out: ${h3}`);
  if (valid(h6)) segments.push(`6 months out: ${h6}`);
  if (valid(h12)) segments.push(`12 months out: ${h12}`);
  return segments.join(". ") + `.${confPhrase ? ` (${confPhrase}.)` : ""}`;
}

function InfoIcon({ onClick, ariaLabel }: { onClick: () => void; ariaLabel: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        width: 22, height: 22, minWidth: 22,
        borderRadius: "50%",
        border: "1px solid #CBD5E1",
        background: "#FFFFFF",
        color: COLORS.textSecondary,
        cursor: "pointer",
        fontSize: 12, fontWeight: 700,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        padding: 0,
        transition: "background 120ms ease, color 120ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = COLORS.brand;
        e.currentTarget.style.color = "#FFFFFF";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "#FFFFFF";
        e.currentTarget.style.color = COLORS.textSecondary;
      }}
    >
      i
    </button>
  );
}

function BulletHeader({
  ordinal, title, onInfoClick,
}: {
  ordinal: number; title: string; onInfoClick: () => void;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      marginBottom: 8,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          flex: "0 0 auto",
          width: 26, height: 26, borderRadius: 13,
          background: COLORS.brand, color: "#FFFFFF",
          fontSize: 12, fontWeight: 800,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
        }}>
          {ordinal}
        </span>
        <h3 style={{
          margin: 0, fontSize: 11, fontWeight: 700,
          letterSpacing: 0.6, textTransform: "uppercase",
          color: COLORS.textSecondary,
        }}>
          {title}
        </h3>
      </div>
      <InfoIcon
        onClick={onInfoClick}
        ariaLabel={`How we built the "${title}" bullet`}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bullet 1 — What changed
// ---------------------------------------------------------------------------

function WhatChangedBullet({ data, onInfoClick }: {
  data: UnifiedAnalysisWhatChanged; onInfoClick: () => void;
}) {
  const polTint = data.polarity ? POLARITY_TINT[data.polarity] : null;
  const publishedDate = data.published_at ? new Date(data.published_at).toLocaleDateString("en-IN", {
    day: "numeric", month: "short", year: "numeric",
  }) : "";

  return (
    <section style={cardStyle}>
      <BulletHeader ordinal={1} title="What changed" onInfoClick={onInfoClick} />
      <p style={{
        margin: 0, fontSize: 15, lineHeight: 1.5,
        color: COLORS.textPrimary, fontWeight: 600,
      }}>
        {data.headline || "—"}
      </p>
      <div style={{
        marginTop: 10, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8,
        fontSize: 12, color: COLORS.textSecondary,
      }}>
        {polTint && (
          <span style={{
            padding: "2px 8px", borderRadius: 999,
            background: polTint.bg, color: polTint.fg,
            fontSize: 10, fontWeight: 700, textTransform: "capitalize",
          }}>
            {data.polarity}
          </span>
        )}
        {data.source && <span>{data.source}</span>}
        {data.source && publishedDate && <span style={{ opacity: 0.5 }}>·</span>}
        {publishedDate && <span>{publishedDate}</span>}
        {data.url && (
          <>
            <span style={{ opacity: 0.5 }}>·</span>
            <a
              href={data.url} target="_blank" rel="noopener noreferrer"
              style={{ color: COLORS.brand, textDecoration: "none" }}
            >
              Read source →
            </a>
          </>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Bullet 2 — Why it matters
// ---------------------------------------------------------------------------

function WhyItMattersBullet({ data, onInfoClick }: {
  data: UnifiedAnalysisWhyItMatters; onInfoClick: () => void;
}) {
  const tint = data.materiality_band ? BAND_TINT[data.materiality_band] : null;
  const exposureLabel = data.financial_exposure?.label
    || (data.financial_exposure?.amount_cr ? `~₹${data.financial_exposure.amount_cr.toLocaleString("en-IN")} Cr` : null);

  return (
    <section style={cardStyle}>
      <BulletHeader ordinal={2} title="Why it matters" onInfoClick={onInfoClick} />
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 8 }}>
        {tint && (
          <span style={{
            padding: "3px 10px", borderRadius: 999,
            background: tint.bg, color: tint.fg, border: `1px solid ${tint.border}`,
            fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
          }}>
            {data.materiality_band}
          </span>
        )}
        {exposureLabel && (
          <span style={{
            padding: "3px 10px", borderRadius: 999,
            background: "#F1F5F9", color: COLORS.textPrimary,
            fontSize: 11, fontWeight: 700,
          }}>
            {exposureLabel}
          </span>
        )}
        {data.warning === "sasb_unmapped" && (
          <span
            title="No SASB sector mapping for this company — using a neutral materiality weight"
            style={{
              padding: "3px 10px", borderRadius: 999,
              background: "#FEF9C3", color: "#854D0E",
              fontSize: 10, fontWeight: 700,
            }}
          >
            SASB unmapped
          </span>
        )}
      </div>
      {data.criticality_summary && (
        <p style={{
          margin: "8px 0 0", fontSize: 14, lineHeight: 1.55,
          color: COLORS.textPrimary, fontWeight: 600,
        }}>
          {data.criticality_summary}
        </p>
      )}
      {data.stakes_for_company && (
        <p style={{
          margin: "8px 0 0", fontSize: 13, lineHeight: 1.6,
          color: COLORS.textSecondary,
        }}>
          {data.stakes_for_company}
        </p>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Bullet 3 — What it triggers
// ---------------------------------------------------------------------------

function WhatItTriggersBullet({ data, onInfoClick }: {
  data: UnifiedAnalysisWhatItTriggers; onInfoClick: () => void;
}) {
  const hasFrameworks = (data.frameworks || []).length > 0;
  const hasActions = (data.recommended_actions || []).length > 0;
  if (!hasFrameworks && !hasActions) {
    return (
      <section style={cardStyle}>
        <BulletHeader ordinal={3} title="What it triggers" onInfoClick={onInfoClick} />
        <p style={{ margin: 0, fontSize: 13, color: COLORS.textSecondary, fontStyle: "italic" }}>
          No mandatory framework triggers or recommended actions identified.
        </p>
      </section>
    );
  }

  return (
    <section style={cardStyle}>
      <BulletHeader ordinal={3} title="What it triggers" onInfoClick={onInfoClick} />
      {hasFrameworks && (
        <div style={{ marginBottom: hasActions ? 12 : 0 }}>
          <div style={subLabelStyle}>Frameworks</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {data.frameworks.map((f, idx) => (
              <span key={`${f.code}-${idx}`} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "4px 9px", borderRadius: 6,
                background: f.is_mandatory ? "#FEE2E2" : "#F1F5F9",
                border: f.is_mandatory ? "1px solid #FCA5A5" : "1px solid #E2E8F0",
                fontSize: 11, fontWeight: 600,
                color: f.is_mandatory ? "#991B1B" : COLORS.textPrimary,
              }}>
                <span>{f.code}{f.section ? ` · ${f.section}` : ""}</span>
                {f.is_mandatory && (
                  <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: 0.5 }}>MANDATORY</span>
                )}
                {f.deadline_days != null && (
                  <span style={{ fontSize: 10, opacity: 0.75 }}>· {f.deadline_days}d</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}
      {hasActions && (
        <div>
          <div style={subLabelStyle}>Recommended actions</div>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 8 }}>
            {data.recommended_actions.map((a, idx) => (
              <li key={idx} style={{
                padding: "8px 10px",
                background: "#F8FAFC",
                border: "1px solid #E2E8F0",
                borderRadius: 8,
                fontSize: 13, lineHeight: 1.45,
                color: COLORS.textPrimary,
              }}>
                <div style={{ fontWeight: 600 }}>{a.title}</div>
                {(a.deadline || a.owner) && (
                  <div style={{ fontSize: 11, color: COLORS.textSecondary, marginTop: 3 }}>
                    {a.deadline && <span>by {a.deadline}</span>}
                    {a.deadline && a.owner && <span style={{ opacity: 0.5 }}> · </span>}
                    {a.owner && <span>owner: {a.owner}</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Bullet 4 — What to watch
// ---------------------------------------------------------------------------

function WhatToWatchBullet({ data, onInfoClick }: {
  data: UnifiedAnalysisWhatToWatch; onInfoClick: () => void;
}) {
  const traj = data.sentiment_trajectory as { horizon_3m?: string; horizon_6m?: string; horizon_12m?: string; confidence?: string } | undefined;
  const hasTrajectory = !!(traj && (traj.horizon_3m || traj.horizon_6m || traj.horizon_12m));
  const hasRisks = (data.top_risk_categories || []).length > 0;
  const hasLeadIndicators = (data.lead_indicators || []).length > 0;
  const hasBenchmarks = (data.benchmarks || []).length > 0;

  if (!hasTrajectory && !hasRisks && !hasLeadIndicators && !hasBenchmarks) {
    return (
      <section style={cardStyle}>
        <BulletHeader ordinal={4} title="What to watch" onInfoClick={onInfoClick} />
        <p style={{ margin: 0, fontSize: 13, color: COLORS.textSecondary, fontStyle: "italic" }}>
          No forward signals available yet for this article.
        </p>
      </section>
    );
  }

  return (
    <section style={cardStyle}>
      <BulletHeader ordinal={4} title="What to watch" onInfoClick={onInfoClick} />
      {hasTrajectory && traj && (
        <div style={{ marginBottom: 12 }}>
          <div style={subLabelStyle}>
            Sentiment trajectory
            {traj.confidence && (
              <span style={{
                marginLeft: 6, padding: "1px 6px", borderRadius: 4,
                background: "#F1F5F9", color: COLORS.textSecondary,
                fontSize: 9, fontWeight: 700, textTransform: "uppercase",
              }}>
                {traj.confidence}
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {(["horizon_3m", "horizon_6m", "horizon_12m"] as const).map((k) => {
              const dir = traj[k] || "";
              const tint = TRAJECTORY_TINT[dir];
              const label = k.replace("horizon_", "");
              return (
                <div key={k} style={{
                  padding: "6px 12px", borderRadius: 8,
                  background: "#FFFFFF", border: "1px solid #E2E8F0",
                  minWidth: 70, textAlign: "center",
                }}>
                  <div style={{ fontSize: 10, color: COLORS.textSecondary, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
                    {label}
                  </div>
                  <div style={{
                    fontSize: 13, fontWeight: 700, marginTop: 2,
                    color: tint?.fg || COLORS.textPrimary,
                  }}>
                    {tint?.symbol} {dir || "—"}
                  </div>
                </div>
              );
            })}
          </div>
          {/* Phase 33 — plain-English translation under the 3 chips */}
          <p style={{
            margin: "10px 0 0", fontSize: 12.5, lineHeight: 1.55,
            color: COLORS.textSecondary, fontStyle: "italic",
          }}>
            {explainTrajectory(traj.horizon_3m, traj.horizon_6m, traj.horizon_12m, traj.confidence)}
          </p>
        </div>
      )}
      {hasRisks && (
        <div style={{ marginBottom: hasLeadIndicators || hasBenchmarks ? 12 : 0 }}>
          <div style={subLabelStyle}>Top risk categories</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {data.top_risk_categories.map((r, idx) => (
              <span key={`${r}-${idx}`} style={{
                padding: "3px 9px", borderRadius: 999,
                background: "#FEF2F2", color: "#991B1B",
                fontSize: 11, fontWeight: 600,
                border: "1px solid #FECACA",
              }}>
                {r}
              </span>
            ))}
          </div>
        </div>
      )}
      {hasLeadIndicators && (
        <div style={{ marginBottom: hasBenchmarks ? 12 : 0 }}>
          <div style={subLabelStyle}>Lead indicators</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: COLORS.textSecondary, lineHeight: 1.5 }}>
            {data.lead_indicators.map((li, idx) => <li key={idx}>{li}</li>)}
          </ul>
        </div>
      )}
      {hasBenchmarks && (
        <div>
          <div style={subLabelStyle}>External benchmarks</div>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
            {data.benchmarks.map((b, idx) => (
              <li key={idx} style={{
                fontSize: 12, color: COLORS.textPrimary,
                display: "flex", justifyContent: "space-between", gap: 8,
              }}>
                <span style={{ fontWeight: 600 }}>{b.source}</span>
                <span>{b.metric}: <strong>{b.value}</strong></span>
                {b.as_of && (
                  <span style={{ color: COLORS.textSecondary, fontSize: 11 }}>as of {b.as_of}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Shared styles
// ---------------------------------------------------------------------------

const cardStyle: React.CSSProperties = {
  padding: "14px 16px",
  borderRadius: 12,
  background: "#FFFFFF",
  border: "1px solid #E2E8F0",
  boxShadow: "0 1px 3px rgba(15, 23, 42, 0.04)",
};

const subLabelStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: 0.6,
  textTransform: "uppercase",
  color: COLORS.textSecondary,
  marginBottom: 6,
  display: "flex",
  alignItems: "center",
};

// ---------------------------------------------------------------------------
// Top-level
// ---------------------------------------------------------------------------

export function UnifiedAnalysisCard({ analysis, articleId }: Props) {
  const [openBullet, setOpenBullet] = useState<BulletKey | null>(null);

  if (!analysis) return null;

  return (
    <section
      aria-label="Unified analysis"
      style={{
        margin: "16px 24px 0",
        display: "flex", flexDirection: "column", gap: 12,
      }}
    >
      {/* Phase 39 — editorial lede. Story-style opener that sits above
          the 4-bullet structure. Returns null when no lede present so the
          layout falls back to Phase 32 behaviour cleanly. */}
      <EditorialLede lede={analysis.lede} />
      <WhatChangedBullet
        data={analysis.what_changed}
        onInfoClick={() => setOpenBullet("what_changed")}
      />
      <WhyItMattersBullet
        data={analysis.why_it_matters}
        onInfoClick={() => setOpenBullet("why_it_matters")}
      />
      <WhatItTriggersBullet
        data={analysis.what_it_triggers}
        onInfoClick={() => setOpenBullet("what_it_triggers")}
      />
      <WhatToWatchBullet
        data={analysis.what_to_watch}
        onInfoClick={() => setOpenBullet("what_to_watch")}
      />
      {openBullet && (
        <MethodologyDrawer
          articleId={articleId}
          panelId={openBullet}
          onClose={() => setOpenBullet(null)}
        />
      )}
    </section>
  );
}
