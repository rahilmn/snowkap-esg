/**
 * GeographicSignalPanel — Displays geographic intelligence: locations,
 * regulatory jurisdictions, supply chain overlap, and geo-risk flags.
 */

import { COLORS, RADII } from "../../lib/designTokens";
import type { GeographicSignal } from "../../types";

interface GeographicSignalPanelProps {
  geoSignal: GeographicSignal | null;
}

export function GeographicSignalPanel({ geoSignal }: GeographicSignalPanelProps) {
  if (!geoSignal) return null;

  const locations = geoSignal.locations_detected || [];
  const jurisdictions = geoSignal.regulatory_jurisdictions || {};
  const supplyChainOverlap = geoSignal.supply_chain_overlap;
  const geoRiskFlags = geoSignal.geo_risk_flags || [];

  const jurisdictionEntries = Object.entries(jurisdictions);
  const hasAny =
    locations.length > 0 ||
    jurisdictionEntries.length > 0 ||
    !!supplyChainOverlap ||
    geoRiskFlags.length > 0;

  if (!hasAny) return null;

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
        Geographic Intelligence
      </h3>

      {/* Locations Detected */}
      {locations.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <h4
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: COLORS.textMuted,
              margin: "0 0 6px",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Locations Detected
          </h4>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
            {locations.map((loc, i) => (
              <span
                key={i}
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
                {loc}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Regulatory Jurisdictions */}
      {jurisdictionEntries.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <h4
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: COLORS.textMuted,
              margin: "0 0 6px",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Regulatory Jurisdictions
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {jurisdictionEntries.map(([country, frameworks]) => (
              <div
                key={country}
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: "6px",
                  fontSize: "13px",
                  lineHeight: "1.5",
                }}
              >
                <span style={{ fontWeight: 600, color: COLORS.textPrimary, whiteSpace: "nowrap" }}>
                  {country}
                </span>
                <span style={{ color: COLORS.textMuted, fontSize: "12px" }}>→</span>
                <span style={{ color: COLORS.textSecondary, fontSize: "12px" }}>
                  {(frameworks || []).join(", ")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Supply Chain Overlap */}
      {supplyChainOverlap && (
        <div style={{ marginBottom: "12px" }}>
          <h4
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: COLORS.textMuted,
              margin: "0 0 6px",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Supply Chain Overlap
          </h4>
          <p
            style={{
              fontSize: "13px",
              color: COLORS.textPrimary,
              lineHeight: "1.5",
              margin: 0,
            }}
          >
            {supplyChainOverlap}
          </p>
        </div>
      )}

      {/* Geo-Risk Flags */}
      {geoRiskFlags.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <h4
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: COLORS.textMuted,
              margin: "0 0 6px",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Geo-Risk Flags
          </h4>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
            {geoRiskFlags.map((flag, i) => (
              <span
                key={i}
                style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "#dc2626",
                  backgroundColor: "rgba(220, 38, 38, 0.1)",
                  padding: "2px 8px",
                  borderRadius: RADII.pill,
                  whiteSpace: "nowrap",
                }}
              >
                {flag}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
