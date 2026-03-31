/**
 * Fix 3: Vertical causal chain visualization.
 *
 * FIXED: 0-hop directOperational shows simplified "Direct Impact" view.
 * Multi-hop chains (1+) show proper 1st/2nd/3rd Order propagation.
 * No data = "No causal analysis available" message.
 */

import { COLORS } from "../../lib/designTokens";

interface ChainNode {
  order: string;
  label: string;
  description: string;
  isOpportunity?: boolean;
  isHighlight?: boolean;
  isCompetitiveIntel?: boolean;
  isSectorNews?: boolean;
}

interface VerticalCausalChainProps {
  chainPath?: Array<{ nodes?: string[]; edges?: string[] }>;
  explanation?: string;
  relationshipType?: string;
  hops?: number;
  frameworks?: string[];
  articleTitle?: string;
  confidence?: number;
}

const RELATIONSHIP_LABELS: Record<string, string> = {
  directOperational: "Direct Operational",
  supplyChainUpstream: "Supply Chain (Upstream)",
  supplyChainDownstream: "Supply Chain (Downstream)",
  regulatoryContagion: "Regulatory Contagion",
  geographicProximity: "Geographic Proximity",
  industrySpillover: "Industry Spillover",
  workforceIndirect: "Workforce (Indirect)",
  commodityChain: "Commodity Chain",
  waterSharedBasin: "Shared Water Basin",
  climateRiskExposure: "Climate Risk Exposure",
  ownershipChain: "Ownership Chain",
  investorExposure: "Investor Exposure",
  customerConcentration: "Customer Concentration",
};

function buildNodes(props: VerticalCausalChainProps): ChainNode[] {
  const { chainPath, explanation, relationshipType, hops, frameworks, confidence } = props;
  const nodes: ChainNode[] = [];
  const relLabel = RELATIONSHIP_LABELS[relationshipType || ""] || relationshipType || "Impact";
  const fwList = (frameworks || []).slice(0, 5).join(", ");
  const fwCount = (frameworks || []).length;
  const confPct = confidence != null ? `${Math.round(confidence * 100)}%` : null;

  // Detect "Competitive Intelligence" or "Sector News" from backend explanation
  const isCompetitiveIntel = explanation?.startsWith("Competitive Intelligence:");
  const isSectorNews = explanation?.startsWith("Sector News:");
  const matchCategory = isCompetitiveIntel ? "Competitive Intelligence" : isSectorNews ? "Sector News" : null;

  // === 0-HOP DIRECT MATCH: Simplified view ===
  if ((hops || 0) === 0 && chainPath && chainPath[0]?.nodes) {
    const pathNodes = chainPath[0].nodes;
    // Clean up raw URIs — extract human-readable names
    const cleanName = (raw: string | undefined) => {
      if (!raw) return "Entity";
      // Strip "company_<uuid>" or "facility_<uuid>" patterns
      if (/^(company|facility|supplier|competitor|region)[ _]/.test(raw)) {
        return raw.replace(/^(company|facility|supplier|competitor|region)[ _]/, "").replace(/[-_][a-f0-9]{4,}/gi, "").trim() || raw;
      }
      return raw;
    };
    const companyName = pathNodes.length >= 1 ? cleanName(pathNodes[pathNodes.length - 1]) : "Company";

    // Use the explanation from the backend if available — it's more descriptive
    const directDesc = props.explanation && !props.explanation.includes("->")
      ? props.explanation
      : `This article directly impacts ${companyName} through ${relLabel.toLowerCase()}.` +
        (confPct ? ` Confidence: ${confPct}.` : "");

    nodes.push({
      order: matchCategory || "Direct Impact",
      label: matchCategory ? matchCategory : relLabel,
      description: directDesc,
      isHighlight: !matchCategory,
      isCompetitiveIntel: isCompetitiveIntel || false,
      isSectorNews: isSectorNews || false,
    });

    // Frameworks affected
    if (fwCount) {
      nodes.push({
        order: "Frameworks",
        label: `${fwCount} Frameworks Affected`,
        description: `Relevant frameworks: ${(frameworks || []).join(", ")}.`,
      });
    }

    // Opportunity
    nodes.push({
      order: "Opportunity",
      label: "Strategic Response",
      description: fwList
        ? `Proactive alignment with ${fwList} positions the company ahead of regulatory requirements and strengthens ESG standing.`
        : "Proactive response strengthens ESG positioning and stakeholder confidence.",
      isOpportunity: true,
    });

    return nodes;
  }

  // === MULTI-HOP CHAIN: Full 1st/2nd/3rd Order view ===
  if (chainPath && chainPath[0]?.nodes) {
    const pathNodes = chainPath[0].nodes;
    // Clean up raw URIs
    const cleanNode = (raw: string | undefined) => {
      if (!raw) return "Entity";
      return raw
        .replace(/^(company|facility|supplier|competitor|region|commodity)[ _]/i, "")
        .replace(/[-_][a-f0-9]{8,}/gi, "")
        .replace(/_/g, " ")
        .trim() || raw;
    };

    for (let i = 0; i < pathNodes.length; i++) {
      const ordinal = i === 0 ? "1st" : i === 1 ? "2nd" : i === 2 ? "3rd" : `${i + 1}th`;
      const label = i === 0 ? relLabel
        : i === pathNodes.length - 1 ? "Company Impact"
        : i === 1 ? "Propagation"
        : "Indirect Impact";

      nodes.push({
        order: `${ordinal} Order`,
        label,
        description: cleanNode(pathNodes[i]) || `Impact propagation (hop ${i})`,
      });
    }
  } else if (explanation) {
    // No chain_path but we have an explanation
    nodes.push({
      order: matchCategory || "Impact",
      label: matchCategory || relLabel,
      description: explanation,
      isCompetitiveIntel: isCompetitiveIntel || false,
      isSectorNews: isSectorNews || false,
    });
  }

  // Add opportunity node
  if (nodes.length > 0) {
    nodes.push({
      order: "Opportunity",
      label: "Strategic Response",
      description: fwList
        ? `Proactive alignment with ${fwList} positions the company ahead of regulatory requirements.`
        : "Proactive response strengthens ESG positioning.",
      isOpportunity: true,
    });
  }

  return nodes;
}

export function VerticalCausalChain(props: VerticalCausalChainProps) {
  const displayNodes = buildNodes(props);

  if (displayNodes.length === 0) {
    return (
      <div style={{ padding: "16px 33px" }}>
        <p style={{ fontSize: "14px", color: COLORS.textMuted, textAlign: "center" }}>
          No causal chain analysis available for this article.
          <br />
          <span style={{ fontSize: "13px" }}>
            This article may not have a direct impact on your company.
          </span>
        </p>
      </div>
    );
  }

  return (
    <div className="relative" style={{ paddingLeft: "33px", paddingRight: "16px" }}>
      {/* Vertical dashed line */}
      {displayNodes.length > 1 && (
        <div
          className="absolute"
          style={{
            left: "37px",
            top: "15px",
            bottom: "15px",
            width: "0px",
            borderLeft: `2px dashed ${COLORS.textDisabled}`,
          }}
        />
      )}

      <div className="space-y-5">
        {displayNodes.map((node, i) => (
          <div key={i} className="relative flex items-start gap-4">
            {/* Circle node */}
            <div
              className="flex-shrink-0 mt-1"
              style={{
                width: node.isHighlight ? "14px" : "10px",
                height: node.isHighlight ? "14px" : "10px",
                borderRadius: "50%",
                border: node.isOpportunity ? "none"
                  : node.isCompetitiveIntel ? `3px solid #2563eb`
                  : node.isSectorNews ? `3px solid #6b7280`
                  : node.isHighlight ? `3px solid ${COLORS.brand}`
                  : `2px solid ${COLORS.textDisabled}`,
                backgroundColor: node.isOpportunity ? COLORS.opportunity
                  : node.isCompetitiveIntel ? "rgba(37,99,235,0.15)"
                  : node.isSectorNews ? "rgba(107,114,128,0.15)"
                  : node.isHighlight ? COLORS.brandLight
                  : COLORS.bgWhite,
                position: "relative",
                zIndex: 2,
              }}
            />

            {/* Content card */}
            <div
              className="flex-1 rounded-lg p-3"
              style={{
                backgroundColor: node.isOpportunity ? COLORS.opportunityBg
                  : node.isCompetitiveIntel ? "rgba(37,99,235,0.06)"
                  : node.isSectorNews ? "rgba(107,114,128,0.06)"
                  : node.isHighlight ? "rgba(223, 89, 0, 0.06)"
                  : COLORS.bgLight,
                borderRadius: "8px",
                border: node.isCompetitiveIntel ? "1px solid rgba(37,99,235,0.2)"
                  : node.isSectorNews ? "1px solid rgba(107,114,128,0.2)"
                  : node.isHighlight ? `1px solid ${COLORS.brandLight}` : "none",
              }}
            >
              <p
                className="font-medium"
                style={{ fontSize: "15px", color: COLORS.textPrimary }}
              >
                {node.order} &bull; {node.label}
              </p>
              <p
                className="mt-1"
                style={{ fontSize: "14px", color: COLORS.textSecondary, lineHeight: "1.5" }}
              >
                {node.description}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
