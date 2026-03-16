/** Causal chain visualization — horizontal node chain with decay (Stage 6.7) */

interface CausalChainVizProps {
  hops: number;
  relationshipType: string;
  explanation: string | null;
  impactScore: number;
}

const HOP_DECAY = [1.0, 0.7, 0.4, 0.2, 0.1];

const RELATIONSHIP_LABELS: Record<string, string> = {
  directOperational: "Direct Operation",
  supplyChainUpstream: "Supply Chain (Up)",
  supplyChainDownstream: "Supply Chain (Down)",
  geographicProximity: "Geographic Proximity",
  regulatoryContagion: "Regulatory Contagion",
  industrySpillover: "Industry Spillover",
  commodityChain: "Commodity Chain",
  workforceIndirect: "Workforce (Indirect)",
  waterSharedBasin: "Shared Water Basin",
  pollutionDispersion: "Pollution Dispersion",
  climateRiskExposure: "Climate Risk",
  laborContractor: "Labor Contractor",
  communityAffected: "Community Impact",
  regulatoryJurisdiction: "Regulatory Jurisdiction",
  ownershipChain: "Ownership Chain",
  investorExposure: "Investor Exposure",
  customerConcentration: "Customer Concentration",
};

export function CausalChainViz({
  hops,
  relationshipType,
  explanation,
  impactScore,
}: CausalChainVizProps) {
  const nodes = Array.from({ length: hops + 1 }, (_, i) => ({
    label: i === 0 ? "Event" : i === hops ? "Company" : `Hop ${i}`,
    decay: HOP_DECAY[i] ?? 0.1,
  }));

  const relLabel = RELATIONSHIP_LABELS[relationshipType] || relationshipType.replace(/([A-Z])/g, " $1").trim();

  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-700 mb-2">Causal Chain</h4>
      <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
        {/* Relationship type badge */}
        <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 mb-3 inline-block">
          {relLabel}
        </span>

        {/* Node chain */}
        <div className="flex items-center gap-1 overflow-x-auto mt-2">
          {nodes.map((node, i) => (
            <div key={i} className="flex items-center">
              <div
                className="flex flex-col items-center"
                style={{ opacity: 0.4 + node.decay * 0.6 }}
              >
                <div
                  className="w-10 h-10 rounded-full bg-indigo-500 text-white text-[10px] font-bold flex items-center justify-center"
                  style={{ transform: `scale(${0.7 + node.decay * 0.3})` }}
                >
                  {(node.decay * impactScore).toFixed(0)}
                </div>
                <span className="text-[9px] text-muted-foreground mt-1 whitespace-nowrap">
                  {node.label}
                </span>
              </div>
              {i < nodes.length - 1 && (
                <div className="mx-1 text-gray-300 text-lg">→</div>
              )}
            </div>
          ))}
        </div>

        {/* Explanation */}
        {explanation && (
          <p className="text-xs text-gray-600 mt-3 leading-relaxed">{explanation}</p>
        )}
      </div>
    </div>
  );
}
