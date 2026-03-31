/**
 * UnlockFullAnalysis — CTA card shown below RiskSpotlight on FEED-tier articles.
 * Encourages users to trigger full AI analysis for high-relevance stories.
 */

import { COLORS, RADII } from "../../lib/designTokens";

interface UnlockFullAnalysisProps {
  relevanceScore: number;
  onAskAI: () => void;
}

export function UnlockFullAnalysis({
  relevanceScore,
  onAskAI,
}: UnlockFullAnalysisProps) {
  return (
    <div
      style={{
        border: `1.5px dashed ${COLORS.textDisabled}`,
        borderRadius: RADII.card,
        backgroundColor: COLORS.bgLight,
        padding: "14px 16px",
        textAlign: "center",
      }}
    >
      {/* Divider line art */}
      <div
        style={{
          fontSize: "12px",
          color: COLORS.textDisabled,
          letterSpacing: "2px",
          marginBottom: "8px",
          userSelect: "none",
        }}
      >
        ━━━━━━━━━━━━
      </div>

      {/* Relevance score text */}
      <p
        style={{
          fontSize: "13px",
          fontWeight: 600,
          color: COLORS.textPrimary,
          margin: "0 0 4px 0",
        }}
      >
        This article scored{" "}
        <span style={{ color: COLORS.brand }}>{relevanceScore}/10</span>{" "}
        relevance
      </p>

      {/* Sub-text */}
      <p
        style={{
          fontSize: "12px",
          color: COLORS.textMuted,
          lineHeight: "1.45",
          margin: "0 0 12px 0",
        }}
      >
        Full risk matrix, framework alignment, and deep insight are available
        for high-impact stories (7+)
      </p>

      {/* CTA Button */}
      <button
        onClick={onAskAI}
        style={{
          width: "100%",
          padding: "10px 0",
          fontSize: "14px",
          fontWeight: 600,
          color: "#ffffff",
          backgroundColor: COLORS.darkCard,
          border: "none",
          borderRadius: RADII.button,
          cursor: "pointer",
          transition: "opacity 0.15s",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLButtonElement).style.opacity = "0.85";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.opacity = "1";
        }}
      >
        Ask AI for Full Analysis
      </button>
    </div>
  );
}
