/**
 * EsgThemeBar — Compact horizontal strip of ESG theme pills.
 * Shows primary theme (filled), pillar badge, secondary themes (outlined), and confidence.
 */

import { COLORS, RADII } from "../../lib/designTokens";
import type { EsgThemes } from "../../types";

interface EsgThemeBarProps {
  esgThemes: EsgThemes | null;
}

const PILLAR_COLORS: Record<string, string> = {
  E: "#16a34a",
  S: "#2563eb",
  G: "#7c3aed",
  Environmental: "#16a34a",
  Social: "#2563eb",
  Governance: "#7c3aed",
};

function pillarColor(pillar: string): string {
  const key = pillar.charAt(0).toUpperCase();
  return PILLAR_COLORS[pillar] || PILLAR_COLORS[key] || COLORS.textSecondary;
}

function pillarLetter(pillar: string): string {
  return pillar.charAt(0).toUpperCase();
}

export function EsgThemeBar({ esgThemes }: EsgThemeBarProps) {
  if (!esgThemes) return null;

  const primaryColor = pillarColor(esgThemes.primary_pillar);
  const secondaryThemes = (esgThemes.secondary_themes || []).slice(0, 2);
  const confidence = esgThemes.confidence;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        flexWrap: "wrap",
        maxHeight: "60px",
        overflow: "hidden",
        padding: "6px 0",
      }}
    >
      {/* Primary theme — filled pill */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
          backgroundColor: primaryColor,
          color: "#ffffff",
          fontSize: "12px",
          fontWeight: 600,
          padding: "3px 10px",
          borderRadius: RADII.pill,
          lineHeight: "1.4",
          whiteSpace: "nowrap",
        }}
      >
        {esgThemes.primary_theme}
      </span>

      {/* Pillar badge — small chip */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: "20px",
          height: "20px",
          borderRadius: RADII.pill,
          backgroundColor: primaryColor,
          color: "#ffffff",
          fontSize: "11px",
          fontWeight: 700,
          lineHeight: 1,
        }}
      >
        {pillarLetter(esgThemes.primary_pillar)}
      </span>

      {/* Secondary themes — outlined pills */}
      {secondaryThemes.map((st, i) => {
        const secColor = pillarColor(st.pillar);
        return (
          <span
            key={i}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "3px",
              fontSize: "11px",
              fontWeight: 500,
              color: secColor,
              padding: "2px 8px",
              borderRadius: RADII.pill,
              border: `1px solid ${secColor}`,
              whiteSpace: "nowrap",
              lineHeight: "1.4",
            }}
          >
            {st.theme}
          </span>
        );
      })}

      {/* Confidence */}
      {confidence != null && (
        <span
          style={{
            fontSize: "11px",
            color: COLORS.textMuted,
            whiteSpace: "nowrap",
            marginLeft: "2px",
          }}
        >
          {Math.round(confidence * 100)}% conf
        </span>
      )}
    </div>
  );
}
