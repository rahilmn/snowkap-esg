/**
 * RiskMatrixDisplay — 10-category risk matrix with Probability × Exposure scoring.
 * Compact heatmap layout, tappable rows with rationale expansion.
 * Mobile-first at 440px viewport.
 */

import { useState } from "react";
import { COLORS } from "../../lib/designTokens";
import type { RiskMatrix } from "../../types";

interface RiskMatrixDisplayProps {
  riskMatrix: RiskMatrix | null;
}

const CLASSIFICATION_STYLES: Record<
  string,
  { bg: string; color: string }
> = {
  CRITICAL: { bg: "rgba(216, 0, 4, 0.15)", color: "#ff4044" },
  HIGH: { bg: "rgba(223, 89, 0, 0.15)", color: COLORS.brand },
  MODERATE: { bg: "rgba(245, 158, 11, 0.12)", color: "#d97706" },
  LOW: { bg: "rgba(136, 136, 136, 0.12)", color: COLORS.textSecondary },
};

function DotRow({ filled, total = 5 }: { filled: number; total?: number }) {
  const dots = [];
  for (let i = 0; i < total; i++) {
    dots.push(
      <span
        key={i}
        style={{
          display: "inline-block",
          width: "7px",
          height: "7px",
          borderRadius: "50%",
          backgroundColor: i < filled ? COLORS.brand : COLORS.textDisabled,
          marginRight: i < total - 1 ? "2px" : "0",
          transition: "background-color 0.2s",
        }}
      />
    );
  }
  return <span style={{ display: "inline-flex", alignItems: "center" }}>{dots}</span>;
}

export function RiskMatrixDisplay({ riskMatrix }: RiskMatrixDisplayProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!riskMatrix || !riskMatrix.categories || riskMatrix.categories.length === 0) {
    return null;
  }

  const sorted = [...riskMatrix.categories].sort(
    (a, b) => b.risk_score - a.risk_score
  );

  const topRiskIds = new Set(
    (riskMatrix.top_risks || []).slice(0, 3).map((r) => r.category_id)
  );

  const maxPossible = riskMatrix.categories.length * 25; // 5×5 per category
  // total_score is the raw sum (0-250), aggregate_score is normalized (0-1)
  // Display the raw total, derive percentage from it
  const totalScore = riskMatrix.total_score ?? Math.round((riskMatrix.aggregate_score ?? 0) * maxPossible);
  const percentage = maxPossible > 0 ? Math.round((totalScore / maxPossible) * 100) : 0;

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: "14px",
        }}
      >
        <h3
          style={{
            fontSize: "15px",
            fontWeight: 600,
            color: COLORS.textSecondary,
            margin: 0,
          }}
        >
          Risk Assessment
        </h3>
        <div style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
          <span
            style={{
              fontSize: "26px",
              fontWeight: 700,
              color: percentage >= 60 ? "#ff4044" : percentage >= 35 ? COLORS.brand : COLORS.textPrimary,
              lineHeight: 1,
            }}
          >
            {totalScore}
          </span>
          <span style={{ fontSize: "14px", color: COLORS.textMuted, fontWeight: 500 }}>
            /{maxPossible}
          </span>
          <span
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: COLORS.textMuted,
              marginLeft: "4px",
            }}
          >
            ({percentage}%)
          </span>
        </div>
      </div>
      {/* Context label */}
      <p style={{ fontSize: "10px", color: COLORS.textMuted, margin: "0 0 10px 0", textAlign: "right" }}>
        across {riskMatrix.categories.length} risk categories &middot; Prob &times; Exp = Score (max 25 each)
      </p>

      {/* Column headers */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 52px 52px 42px 72px",
          alignItems: "center",
          padding: "0 0 6px 10px",
          borderBottom: `1px solid ${COLORS.textDisabled}`,
        }}
      >
        <span style={{ fontSize: "10px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>
          Category
        </span>
        <span style={{ fontSize: "10px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "center" }}>
          Prob
        </span>
        <span style={{ fontSize: "10px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "center" }}>
          Exp
        </span>
        <span style={{ fontSize: "10px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "center" }}>
          Score
        </span>
        <span style={{ fontSize: "10px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", textAlign: "center" }}>
          Level
        </span>
      </div>

      {/* Category rows */}
      {sorted.map((cat) => {
        const isTopRisk = topRiskIds.has(cat.category_id);
        const isExpanded = expandedId === cat.category_id;
        const cls = (cat.classification in CLASSIFICATION_STYLES
          ? CLASSIFICATION_STYLES[cat.classification]
          : CLASSIFICATION_STYLES.LOW)!;

        return (
          <div key={cat.category_id}>
            <button
              onClick={() =>
                setExpandedId(isExpanded ? null : cat.category_id)
              }
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 52px 52px 42px 72px",
                alignItems: "center",
                width: "100%",
                padding: "10px 0 10px 10px",
                border: "none",
                borderBottom: `1px solid ${COLORS.textDisabled}`,
                borderLeft: isTopRisk ? `3px solid ${COLORS.brand}` : "3px solid transparent",
                background: isExpanded ? COLORS.bgLight : "none",
                cursor: "pointer",
                textAlign: "left",
                transition: "background-color 0.15s",
              }}
            >
              {/* Category name */}
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: isTopRisk ? 600 : 400,
                  color: COLORS.textPrimary,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  paddingRight: "6px",
                }}
              >
                {cat.category_name}
              </span>

              {/* Probability dots */}
              <span style={{ textAlign: "center" }}>
                <DotRow filled={cat.probability} />
              </span>

              {/* Exposure dots */}
              <span style={{ textAlign: "center" }}>
                <DotRow filled={cat.exposure} />
              </span>

              {/* Score number */}
              <span
                style={{
                  fontSize: "13px",
                  fontWeight: 700,
                  color: cat.risk_score >= 20 ? "#ff4044" : cat.risk_score >= 12 ? COLORS.brand : COLORS.textPrimary,
                  textAlign: "center",
                }}
              >
                {cat.risk_score}
              </span>

              {/* Classification badge */}
              <span
                style={{
                  fontSize: "10px",
                  fontWeight: 700,
                  padding: "3px 8px",
                  borderRadius: "4px",
                  backgroundColor: cls.bg,
                  color: cls.color,
                  textAlign: "center",
                  textTransform: "uppercase",
                  letterSpacing: "0.3px",
                  justifySelf: "center",
                }}
              >
                {cat.classification}
              </span>
            </button>

            {/* Expanded rationale */}
            {isExpanded && cat.rationale && (
              <div
                style={{
                  padding: "10px 14px 14px 16px",
                  backgroundColor: COLORS.bgLight,
                  borderBottom: `1px solid ${COLORS.textDisabled}`,
                  borderLeft: isTopRisk ? `3px solid ${COLORS.brand}` : "3px solid transparent",
                }}
              >
                <p
                  style={{
                    fontSize: "13px",
                    color: COLORS.textSecondary,
                    lineHeight: "1.55",
                    margin: 0,
                  }}
                >
                  {cat.rationale}
                </p>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
