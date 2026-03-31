/**
 * NarrativeIntelligence — Displays narrative arc analysis from NLP extraction.
 * Shows core claim, implied causation, stakeholder framing, and temporal framing.
 */

import { COLORS, RADII } from "../../lib/designTokens";
import type { NlpExtraction } from "../../types";

interface NarrativeIntelligenceProps {
  nlpExtraction: NlpExtraction | null;
}

const TEMPORAL_COLORS: Record<string, { bg: string; text: string }> = {
  backward: { bg: "rgba(124, 58, 237, 0.12)", text: "#7c3aed" },
  present: { bg: "rgba(37, 99, 235, 0.12)", text: "#2563eb" },
  forward: { bg: "rgba(22, 163, 74, 0.12)", text: "#16a34a" },
};

export function NarrativeIntelligence({ nlpExtraction }: NarrativeIntelligenceProps) {
  if (!nlpExtraction) return null;

  const arc = nlpExtraction.narrative_arc;
  if (!arc) return null;

  const _raw = arc.stakeholder_framing || {};
  // Clean "null"/"None" strings from LLM output
  const _clean = (v: unknown): string | null => {
    if (!v || v === "null" || v === "None" || v === "none" || v === "N/A") return null;
    return String(v);
  };
  const framing = { protagonist: _clean(_raw.protagonist), antagonist: _clean(_raw.antagonist), affected: _clean(_raw.affected) };
  const hasStakeholders = framing.protagonist || framing.antagonist || framing.affected;
  const temporalStyle = (arc.temporal_framing in TEMPORAL_COLORS
    ? TEMPORAL_COLORS[arc.temporal_framing as keyof typeof TEMPORAL_COLORS]
    : TEMPORAL_COLORS.present)!;

  return (
    <div style={{ padding: "0 0 8px" }}>
      <h3
        style={{
          fontSize: "15px",
          fontWeight: 600,
          color: COLORS.textSecondary,
          margin: "0 0 10px",
        }}
      >
        Narrative Intelligence
      </h3>

      {/* Core Claim */}
      {arc.core_claim && (
        <p
          style={{
            fontSize: "14px",
            fontWeight: 600,
            color: COLORS.textPrimary,
            lineHeight: "1.5",
            margin: "0 0 8px",
          }}
        >
          {arc.core_claim}
        </p>
      )}

      {/* Tone */}
      {nlpExtraction.tone?.primary && (
        <div style={{ display: "flex", gap: "6px", marginBottom: "8px", flexWrap: "wrap" }}>
          <span style={{
            fontSize: "11px", fontWeight: 600, padding: "2px 10px", borderRadius: "12px",
            backgroundColor: "rgba(223, 89, 0, 0.1)", color: COLORS.brand,
          }}>
            {nlpExtraction.tone.primary}
          </span>
          {nlpExtraction.tone.secondary && nlpExtraction.tone.secondary !== nlpExtraction.tone.primary && (
            <span style={{
              fontSize: "11px", fontWeight: 500, padding: "2px 10px", borderRadius: "12px",
              backgroundColor: COLORS.bgLight, color: COLORS.textMuted,
            }}>
              {nlpExtraction.tone.secondary}
            </span>
          )}
        </div>
      )}

      {/* Implied Causation */}
      {arc.implied_causation && (
        <p
          style={{
            fontSize: "13px",
            color: COLORS.textMuted,
            lineHeight: "1.5",
            margin: "0 0 10px",
          }}
        >
          {arc.implied_causation
            .split("→")
            .map((s) => s.trim())
            .join(" → ")}
        </p>
      )}

      {/* Stakeholder Framing */}
      {hasStakeholders && (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "6px",
            marginBottom: "10px",
          }}
        >
          {framing.protagonist && (
            <span
              style={{
                fontSize: "11px",
                fontWeight: 500,
                color: "#16a34a",
                backgroundColor: "rgba(22, 163, 74, 0.1)",
                padding: "2px 8px",
                borderRadius: RADII.pill,
                whiteSpace: "nowrap",
              }}
            >
              Protagonist: {framing.protagonist}
            </span>
          )}
          {framing.antagonist && (
            <span
              style={{
                fontSize: "11px",
                fontWeight: 500,
                color: "#dc2626",
                backgroundColor: "rgba(220, 38, 38, 0.1)",
                padding: "2px 8px",
                borderRadius: RADII.pill,
                whiteSpace: "nowrap",
              }}
            >
              Antagonist: {framing.antagonist}
            </span>
          )}
          {framing.affected && (
            <span
              style={{
                fontSize: "11px",
                fontWeight: 500,
                color: "#2563eb",
                backgroundColor: "rgba(37, 99, 235, 0.1)",
                padding: "2px 8px",
                borderRadius: RADII.pill,
                whiteSpace: "nowrap",
              }}
            >
              Affected: {framing.affected}
            </span>
          )}
        </div>
      )}

      {/* Temporal Framing */}
      {arc.temporal_framing && (
        <span
          style={{
            display: "inline-block",
            fontSize: "11px",
            fontWeight: 600,
            color: temporalStyle.text,
            backgroundColor: temporalStyle.bg,
            padding: "2px 10px",
            borderRadius: RADII.pill,
            textTransform: "capitalize",
          }}
        >
          {arc.temporal_framing}
        </span>
      )}
    </div>
  );
}
