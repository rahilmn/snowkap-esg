/** FrameworkAlignmentV2 — v2.1 framework match cards with tiered display,
 *  mandatory badge, and profitability link.
 *
 *  Thresholds:
 *  - HIGH relevance: score >= 0.5 — shown expanded by default
 *  - LOW relevance: score 0.2-0.49 — behind "View N other frameworks"
 *  - IRRELEVANT: score < 0.2 — completely hidden
 */

import { useState } from "react";
import type { FrameworkMatchV2 } from "../../types";
import { COLORS, RADII } from "../../lib/designTokens";

interface FrameworkAlignmentV2Props {
  frameworkMatches: FrameworkMatchV2[] | null;
}

const HIGH_THRESHOLD = 0.5;
const LOW_THRESHOLD = 0.2;

export function FrameworkAlignmentV2({ frameworkMatches }: FrameworkAlignmentV2Props) {
  const [showLowRelevance, setShowLowRelevance] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (!frameworkMatches || frameworkMatches.length === 0) return null;

  const sorted = [...frameworkMatches].sort(
    (a, b) => (b.relevance_score ?? 0) - (a.relevance_score ?? 0),
  );
  // HIGH: score >= 0.5 — shown expanded by default
  const highRelevance = sorted.filter((m) => (m.relevance_score ?? 0) >= HIGH_THRESHOLD);
  // LOW: score 0.2-0.49 — behind toggle
  const lowRelevance = sorted.filter(
    (m) => (m.relevance_score ?? 0) >= LOW_THRESHOLD && (m.relevance_score ?? 0) < HIGH_THRESHOLD,
  );
  // IRRELEVANT: score < 0.2 — completely hidden (not rendered at all)

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const renderFrameworkCard = (match: FrameworkMatchV2, dimmed: boolean) => {
    const key = match.framework_id;
    const expanded = expandedIds.has(key);
    const isMandatory = match.is_mandatory;
    const profitabilityLink = match.profitability_link;

    return (
      <div
        key={key}
        style={{
          background: COLORS.bgWhite,
          border: `1px solid ${COLORS.cardBorder}`,
          borderRadius: RADII.card,
          padding: "10px 12px",
          cursor: "pointer",
          transition: "box-shadow 0.15s ease",
          opacity: dimmed ? 0.55 : 1,
        }}
        onClick={() => toggleExpand(key)}
      >
        {/* Header: name + badges */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 6,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap", flex: 1, minWidth: 0 }}>
            <span
              style={{
                fontSize: 13,
                fontWeight: 700,
                color: dimmed ? COLORS.textMuted : COLORS.textPrimary,
              }}
            >
              {match.framework_name}
            </span>
            {isMandatory && (
              <span
                style={{
                  fontSize: 8,
                  fontWeight: 700,
                  color: "#dc2626",
                  backgroundColor: "rgba(220,38,38,0.07)",
                  padding: "1px 4px",
                  borderRadius: "3px",
                  letterSpacing: "0.3px",
                  textTransform: "uppercase",
                }}
              >
                MANDATORY
              </span>
            )}
            {profitabilityLink && (
              <span
                style={{
                  fontSize: 10,
                  color: COLORS.textMuted,
                  fontStyle: "italic",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                Impact: {profitabilityLink}
              </span>
            )}
          </div>
          <span
            style={{
              fontSize: 9,
              fontWeight: 600,
              fontFamily: "monospace",
              background: COLORS.frameworkBg,
              color: COLORS.framework,
              padding: "1px 6px",
              borderRadius: RADII.pill,
              flexShrink: 0,
            }}
          >
            {match.framework_id}
          </span>
        </div>

        {/* Relevance bar */}
        <div style={{ marginBottom: 6 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 3,
            }}
          >
            <span
              style={{
                fontSize: 10,
                color: COLORS.textMuted,
              }}
            >
              Relevance
            </span>
            <span
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: COLORS.textSecondary,
              }}
            >
              {(match.relevance_score * 100).toFixed(0)}%
            </span>
          </div>
          <div
            style={{
              height: 4,
              borderRadius: 2,
              background: COLORS.bgLight,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${Math.min(match.relevance_score * 100, 100)}%`,
                background: dimmed ? COLORS.textMuted : COLORS.brand,
                borderRadius: 2,
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>

        {/* Triggered sections pills */}
        {match.triggered_sections.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 3,
              marginBottom: 6,
            }}
          >
            {match.triggered_sections.map((section) => (
              <span
                key={section}
                style={{
                  fontSize: 10,
                  fontWeight: 500,
                  fontFamily: "monospace",
                  background: COLORS.brandLight,
                  color: COLORS.brand,
                  padding: "2px 7px",
                  borderRadius: RADII.pill,
                  whiteSpace: "nowrap",
                }}
              >
                {section}
              </span>
            ))}
          </div>
        )}

        {/* Triggered questions (question-level citations, e.g. Q14, Q15) */}
        {match.triggered_questions && match.triggered_questions.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 6 }}>
            {match.triggered_questions.slice(0, 6).map((q) => (
              <span
                key={q}
                title={q}
                style={{
                  fontSize: 9,
                  fontWeight: 500,
                  fontFamily: "monospace",
                  background: "rgba(223,89,0,0.04)",
                  color: COLORS.textSecondary,
                  border: `1px solid rgba(223,89,0,0.15)`,
                  padding: "1px 6px",
                  borderRadius: RADII.pill,
                  whiteSpace: "nowrap",
                  maxWidth: 160,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {(q.split("(")[0] ?? "").trim()}
              </span>
            ))}
            {match.triggered_questions.length > 6 && (
              <span style={{ fontSize: 9, color: COLORS.textMuted, padding: "1px 4px" }}>
                +{match.triggered_questions.length - 6} more
              </span>
            )}
          </div>
        )}

        {/* Compliance implications */}
        {match.compliance_implications.length > 0 && (
          <ul
            style={{
              margin: 0,
              paddingLeft: 16,
              listStyleType: "disc",
            }}
          >
            {match.compliance_implications.map((imp, i) => (
              <li
                key={i}
                style={{
                  fontSize: 12,
                  color: COLORS.textMuted,
                  lineHeight: 1.4,
                  marginBottom: 2,
                }}
              >
                {imp}
              </li>
            ))}
          </ul>
        )}

        {/* Expandable alignment notes */}
        {match.alignment_notes.length > 0 && (
          <>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                marginTop: 6,
                gap: 4,
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  color: COLORS.textMuted,
                  fontWeight: 500,
                }}
              >
                {expanded ? "Hide" : "Show"} alignment notes
              </span>
              <span
                style={{
                  fontSize: 10,
                  color: COLORS.textMuted,
                  transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
                  transition: "transform 0.2s ease",
                  display: "inline-block",
                }}
              >
                ▼
              </span>
            </div>

            {expanded && (
              <div
                style={{
                  marginTop: 8,
                  paddingTop: 8,
                  borderTop: `1px solid ${COLORS.cardBorder}`,
                }}
              >
                {match.alignment_notes.map((note, i) => (
                  <p
                    key={i}
                    style={{
                      fontSize: 12,
                      color: COLORS.textSecondary,
                      lineHeight: 1.5,
                      margin: 0,
                      marginBottom: i < match.alignment_notes.length - 1 ? 4 : 0,
                    }}
                  >
                    {note}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  return (
    <div>
      <h4
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: COLORS.textSecondary,
          marginBottom: 10,
          textTransform: "uppercase",
          letterSpacing: "0.4px",
        }}
      >
        Framework Alignment
      </h4>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {highRelevance.map((match) => renderFrameworkCard(match, false))}
      </div>

      {lowRelevance.length > 0 && (
        <>
          <button
            onClick={() => setShowLowRelevance((prev) => !prev)}
            style={{
              marginTop: 10,
              background: "none",
              border: "none",
              cursor: "pointer",
              fontSize: 12,
              fontWeight: 500,
              color: COLORS.textMuted,
              padding: 0,
            }}
          >
            {showLowRelevance ? "Hide" : "View"} {lowRelevance.length} other framework{lowRelevance.length !== 1 ? "s" : ""}
          </button>

          {showLowRelevance && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
              {lowRelevance.map((match) => renderFrameworkCard(match, true))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
