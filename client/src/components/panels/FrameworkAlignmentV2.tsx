/** FrameworkAlignmentV2 — v2.0 framework match cards with expand/collapse */

import { useState } from "react";
import type { FrameworkMatchV2 } from "../../types";
import { COLORS, RADII } from "../../lib/designTokens";

interface FrameworkAlignmentV2Props {
  frameworkMatches: FrameworkMatchV2[] | null;
}

const INITIAL_SHOW = 4;

export function FrameworkAlignmentV2({ frameworkMatches }: FrameworkAlignmentV2Props) {
  const [showAll, setShowAll] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (!frameworkMatches || frameworkMatches.length === 0) return null;

  const sorted = [...frameworkMatches].sort(
    (a, b) => (b.relevance_score ?? 0) - (a.relevance_score ?? 0),
  );
  const visible = showAll ? sorted : sorted.slice(0, INITIAL_SHOW);
  const remaining = sorted.length - INITIAL_SHOW;

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div>
      <h4
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: COLORS.textPrimary,
          marginBottom: 12,
        }}
      >
        Framework Alignment
      </h4>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {visible.map((match) => {
          const key = match.framework_id;
          const expanded = expandedIds.has(key);

          return (
            <div
              key={key}
              style={{
                background: COLORS.bgWhite,
                border: `1px solid ${COLORS.cardBorder}`,
                borderRadius: RADII.card,
                padding: "12px 14px",
                cursor: "pointer",
                transition: "box-shadow 0.15s ease",
              }}
              onClick={() => toggleExpand(key)}
            >
              {/* Header: name + badge */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 8,
                }}
              >
                <span
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: COLORS.textPrimary,
                  }}
                >
                  {match.framework_name}
                </span>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    fontFamily: "monospace",
                    background: COLORS.frameworkBg,
                    color: COLORS.framework,
                    padding: "2px 8px",
                    borderRadius: RADII.pill,
                  }}
                >
                  {match.framework_id}
                </span>
              </div>

              {/* Relevance bar */}
              <div style={{ marginBottom: 8 }}>
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
                      background: COLORS.brand,
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
                    gap: 4,
                    marginBottom: 8,
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
                        fontSize: 13,
                        color: COLORS.textMuted,
                        lineHeight: 1.45,
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
        })}
      </div>

      {remaining > 0 && !showAll && (
        <button
          onClick={() => setShowAll(true)}
          style={{
            marginTop: 10,
            background: "none",
            border: "none",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
            color: COLORS.brand,
            padding: 0,
          }}
        >
          Show {remaining} more
        </button>
      )}
    </div>
  );
}
