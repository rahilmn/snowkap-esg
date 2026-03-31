/**
 * RiskSpotlight — Lightweight risk display for FEED-tier articles.
 * Shows exactly 3 top risks with classification badges and rationale.
 * No heatmap dots, no aggregate score.
 */

import { COLORS, RADII } from "../../lib/designTokens";

interface RiskSpotlightProps {
  topRisks: Array<{
    category_name: string;
    classification: string;
    rationale: string;
  }>;
}

const FALLBACK_CLASS = {
  bg: "rgba(136, 136, 136, 0.10)",
  text: COLORS.textSecondary,
  border: COLORS.textSecondary,
} as const;

const CLASSIFICATION_COLORS: Record<
  string,
  { bg: string; text: string; border: string }
> = {
  HIGH: {
    bg: "rgba(223, 89, 0, 0.12)",
    text: "#df5900",
    border: "#df5900",
  },
  MODERATE: {
    bg: "rgba(245, 158, 11, 0.12)",
    text: "#d97706",
    border: "#d97706",
  },
  LOW: FALLBACK_CLASS,
};

function getClassColors(
  classification: string
): { bg: string; text: string; border: string } {
  const key = classification.toUpperCase();
  return (key in CLASSIFICATION_COLORS ? CLASSIFICATION_COLORS[key] : FALLBACK_CLASS)!;
}

export function RiskSpotlight({ topRisks }: RiskSpotlightProps) {
  if (!topRisks || topRisks.length === 0) return null;

  const displayed = topRisks.slice(0, 3);

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          marginBottom: "10px",
        }}
      >
        <span
          style={{
            fontSize: "14px",
            fontWeight: 600,
            color: COLORS.textSecondary,
          }}
        >
          Risk Spotlight
        </span>
        <span
          style={{
            fontSize: "10px",
            fontWeight: 600,
            color: COLORS.textMuted,
            backgroundColor: COLORS.bgLight,
            padding: "2px 7px",
            borderRadius: RADII.pill,
            textTransform: "uppercase",
            letterSpacing: "0.4px",
          }}
        >
          top 3
        </span>
      </div>

      {/* Risk rows */}
      <div style={{ display: "flex", flexDirection: "column", gap: "0px" }}>
        {displayed.map((risk, idx) => {
          const cls = getClassColors(risk.classification);

          return (
            <div
              key={idx}
              style={{
                borderLeft: `3px solid ${cls.border}`,
                padding: "8px 0 8px 10px",
                borderBottom:
                  idx < displayed.length - 1
                    ? `1px solid ${COLORS.textDisabled}`
                    : "none",
              }}
            >
              {/* Top line: name + badge */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                }}
              >
                <span
                  style={{
                    fontSize: "13px",
                    fontWeight: 600,
                    color: COLORS.textPrimary,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                    marginRight: "8px",
                  }}
                >
                  {risk.category_name}
                </span>
                <span
                  style={{
                    fontSize: "10px",
                    fontWeight: 700,
                    padding: "2px 8px",
                    borderRadius: "4px",
                    backgroundColor: cls.bg,
                    color: cls.text,
                    textTransform: "uppercase",
                    letterSpacing: "0.3px",
                    whiteSpace: "nowrap",
                    flexShrink: 0,
                  }}
                >
                  {risk.classification}
                </span>
              </div>

              {/* Rationale */}
              {risk.rationale && (
                <p
                  style={{
                    fontSize: "12px",
                    color: COLORS.textMuted,
                    fontStyle: "italic",
                    lineHeight: "1.45",
                    margin: "4px 0 0 0",
                    overflow: "hidden",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                  }}
                >
                  {risk.rationale}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
