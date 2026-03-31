/**
 * NlpEvidencePanel — NLP evidence subsections: entities, quantitative claims,
 * regulatory references, supply chain references. Meant for use inside a collapsible Section.
 */

import { COLORS, RADII } from "../../lib/designTokens";
import type { NlpExtraction } from "../../types";

interface NlpEvidencePanelProps {
  nlpExtraction: NlpExtraction | null;
}

const ENTITY_TYPE_COLORS: Record<string, string> = {
  ORG: "#2563eb",
  PERSON: "#7c3aed",
  GPE: "#16a34a",
  LOC: "#16a34a",
  DATE: "#d97706",
  MONEY: "#dc2626",
  PRODUCT: "#0891b2",
  LAW: "#9333ea",
  NORP: "#64748b",
};

export function NlpEvidencePanel({ nlpExtraction }: NlpEvidencePanelProps) {
  if (!nlpExtraction) return null;

  const signals = nlpExtraction.esg_signals;
  if (!signals) return null;

  const entities = signals.named_entities || [];
  const quantitative = signals.quantitative_claims || [];
  const regulatory = signals.regulatory_references || [];
  const supplyChain = signals.supply_chain_references || [];

  const hasAny =
    entities.length > 0 ||
    quantitative.length > 0 ||
    regulatory.length > 0 ||
    supplyChain.length > 0;

  if (!hasAny) return null;

  return (
    <div style={{ padding: "0 0 4px" }}>
      <h3
        style={{
          fontSize: "15px",
          fontWeight: 600,
          color: COLORS.textSecondary,
          margin: "0 0 10px",
        }}
      >
        NLP Evidence
      </h3>

      {/* Named Entities */}
      {entities.length > 0 && (
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
            Named Entities
          </h4>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
            {entities.map((ent, i) => {
              const typeColor = ENTITY_TYPE_COLORS[ent.type] || COLORS.textSecondary;
              return (
                <span
                  key={i}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "3px",
                    fontSize: "11px",
                    color: typeColor,
                    backgroundColor: `${typeColor}14`,
                    padding: "2px 8px",
                    borderRadius: RADII.pill,
                    whiteSpace: "nowrap",
                    lineHeight: "1.4",
                  }}
                >
                  <span style={{ fontWeight: 700, fontSize: "10px", opacity: 0.7 }}>
                    {ent.type}
                  </span>
                  <span style={{ fontWeight: 500 }}>{ent.text}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Quantitative Claims */}
      {quantitative.length > 0 && (
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
            Quantitative Claims
          </h4>
          <ul style={{ margin: 0, paddingLeft: "16px" }}>
            {quantitative.map((claim, i) => (
              <li
                key={i}
                style={{
                  fontSize: "13px",
                  color: COLORS.textPrimary,
                  lineHeight: "1.6",
                  marginBottom: "2px",
                }}
              >
                {claim}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Regulatory References */}
      {regulatory.length > 0 && (
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
            Regulatory References
          </h4>
          <ul style={{ margin: 0, paddingLeft: "16px" }}>
            {regulatory.map((ref, i) => (
              <li
                key={i}
                style={{
                  fontSize: "13px",
                  color: COLORS.textPrimary,
                  lineHeight: "1.6",
                  marginBottom: "2px",
                }}
              >
                {ref}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Supply Chain References */}
      {supplyChain.length > 0 && (
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
            Supply Chain References
          </h4>
          <ul style={{ margin: 0, paddingLeft: "16px" }}>
            {supplyChain.map((ref, i) => (
              <li
                key={i}
                style={{
                  fontSize: "13px",
                  color: COLORS.textPrimary,
                  lineHeight: "1.6",
                  marginBottom: "2px",
                }}
              >
                {ref}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
