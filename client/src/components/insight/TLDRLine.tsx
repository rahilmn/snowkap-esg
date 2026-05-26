/** Phase 33 — TL;DR line above the UnifiedAnalysisCard.
 *
 * Renders the one-line "Critical — ₹X Cr exposure and {dominant signal} is
 * the strongest driver" summary at the top of the article-detail sheet so
 * the reader gets the verdict before the 4 bullets.
 *
 * Reads `deep_insight.criticality_summary` (Phase 28 — Feature 2) plus the
 * criticality band for the colour-coded chip. Hidden when both are empty
 * (REJECTED articles, low-confidence classifications).
 */
import { COLORS } from "@/lib/designTokens";

interface Props {
  summary: string;
  band?: string | null;
}

const BAND_TINT: Record<string, { bg: string; fg: string; border: string }> = {
  CRITICAL: { bg: "#FEF2F2", fg: "#991B1B", border: "#FECACA" },
  HIGH:     { bg: "#FFF7ED", fg: "#9A3412", border: "#FED7AA" },
  MEDIUM:   { bg: "#FFFBEB", fg: "#92400E", border: "#FDE68A" },
  LOW:      { bg: "#F0FDF4", fg: "#065F46", border: "#BBF7D0" },
};

export function TLDRLine({ summary, band }: Props) {
  if (!summary?.trim()) return null;
  const tint = band ? BAND_TINT[band.toUpperCase()] : null;
  return (
    <section
      aria-label="Article TL;DR"
      style={{
        margin: "16px 24px 0",
        padding: "10px 14px",
        background: tint ? tint.bg : "#F8FAFC",
        border: `1px solid ${tint ? tint.border : "#E2E8F0"}`,
        borderRadius: 10,
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
      }}
    >
      {tint && band && (
        <span style={{
          flex: "0 0 auto",
          fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
          textTransform: "uppercase",
          padding: "3px 8px", borderRadius: 999,
          background: "#FFFFFF",
          color: tint.fg,
          border: `1px solid ${tint.border}`,
          marginTop: 1,
        }}>
          {band.toUpperCase()}
        </span>
      )}
      <p style={{
        margin: 0,
        fontSize: 13.5, lineHeight: 1.55,
        color: COLORS.textPrimary,
        fontWeight: 600,
      }}>
        {summary}
      </p>
    </section>
  );
}
