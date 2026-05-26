/** Phase 29 — Role summary 2-liner at the top of the article detail.
 *
 * Renders:
 *   1. "Critical because: …" — role-agnostic global criticality summary
 *      (one sentence anchored on the dominant component + financial
 *      exposure). Source: `DeepInsight.criticality_summary` (stamped at
 *      write time) OR fetched from `/methodology` for back-compat.
 *   2. "Why this matters to you (CFO/CEO/Analyst): …" — role-specific
 *      framing from `DeepInsight.role_explainer[role].why_important_for_me`.
 *
 * Hidden entirely when both values are empty (REJECTED / low-confidence
 * articles).
 */
import { COLORS } from "@/lib/designTokens";

interface Props {
  /** Global criticality summary. Empty string when not available. */
  criticalitySummary: string;
  /** Role-specific "why it matters to you" framing. */
  whyItMattersToYou: string;
  /** Active role label for the prefix. */
  role: "cfo" | "ceo" | "esg-analyst";
  /** Band tag color anchoring. */
  band?: string | null;
}

const ROLE_LABEL: Record<Props["role"], string> = {
  cfo: "CFO",
  ceo: "CEO",
  "esg-analyst": "Analyst",
};

const BAND_COLOR: Record<string, { bg: string; fg: string }> = {
  CRITICAL: { bg: "#DC26261A", fg: "#DC2626" },
  HIGH: { bg: "#DF59001A", fg: "#DF5900" },
  MEDIUM: { bg: "#F59E0B1A", fg: "#92400E" },
  LOW: { bg: "#10B9811A", fg: "#065F46" },
};

export function RoleSummary({
  criticalitySummary, whyItMattersToYou, role, band,
}: Props) {
  const hasGlobal = !!criticalitySummary?.trim();
  const hasRole = !!whyItMattersToYou?.trim();
  if (!hasGlobal && !hasRole) return null;

  const bandStyle = band && BAND_COLOR[band] ? BAND_COLOR[band] : null;

  return (
    <section
      aria-label="Why this is critical and why it matters to you"
      style={{
        margin: "16px 24px 0",
        padding: "12px 14px",
        border: "1px solid #E2E8F0",
        borderRadius: 10,
        background: "#FFFFFF",
      }}
    >
      {hasGlobal && (
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 8,
          marginBottom: hasRole ? 8 : 0,
        }}>
          {bandStyle && (
            <span style={{
              flex: "0 0 auto", fontSize: 9, fontWeight: 800,
              letterSpacing: 0.5, textTransform: "uppercase",
              padding: "3px 6px", borderRadius: 6,
              background: bandStyle.bg, color: bandStyle.fg,
              marginTop: 1,
            }}>
              {band}
            </span>
          )}
          <p style={{
            margin: 0, fontSize: 13, lineHeight: 1.5,
            color: COLORS.textPrimary, fontWeight: 600,
          }}>
            {criticalitySummary}
          </p>
        </div>
      )}

      {hasRole && (
        <div style={{ display: "flex", alignItems: "flex-start", gap: 6 }}>
          <span style={{
            flex: "0 0 auto", fontSize: 9, fontWeight: 800,
            letterSpacing: 0.5, textTransform: "uppercase",
            color: COLORS.brand, marginTop: 3,
          }}>
            Why it matters to you · {ROLE_LABEL[role]}
          </span>
          <p style={{
            margin: 0, fontSize: 12.5, lineHeight: 1.55,
            color: COLORS.textSecondary,
          }}>
            {whyItMattersToYou}
          </p>
        </div>
      )}
    </section>
  );
}
