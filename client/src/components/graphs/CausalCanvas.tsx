/** Repos Integration W2 — Interactive causal cascade canvas
 *
 * Replaces the static `CausalChainViz` with a react-flow graph that:
 *   - lays out the cascade left-to-right (event → hops → company)
 *   - renders custom node types per primitive class
 *   - shows β + lag on each edge as a tooltip-style label
 *   - is fully interactive (drag, zoom, fit-view)
 *
 * Data shape consumed:
 *   {
 *     hops: number,
 *     relationshipType?: string,
 *     explanation?: string,
 *     impactScore?: number,
 *     primitives?: Array<{ id: string; label: string; sublabel?: string }>,
 *     edges?: Array<{ source: string; target: string; beta?: number; lag?: number }>,
 *   }
 *
 * When `primitives` / `edges` aren't provided (legacy callers from
 * CausalChainViz), falls back to a deterministic Event → Hop1 → … → Company
 * chain matching the old visualisation but interactive.
 */

import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  type Edge,
  type Node,
} from "reactflow";

import { nodeTypes } from "./_nodes";

import "reactflow/dist/style.css";

export interface CausalCanvasProps {
  hops: number;
  relationshipType?: string;
  explanation?: string | null;
  impactScore?: number;
  primitives?: Array<{ id: string; label: string; sublabel?: string }>;
  edges?: Array<{
    source: string;
    target: string;
    beta?: number;
    lag?: number;
  }>;
}

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

export function CausalCanvas({
  hops,
  relationshipType,
  explanation,
  impactScore,
  primitives,
  edges,
}: CausalCanvasProps) {
  const { nodes, rfEdges } = useMemo(() => {
    const xStep = 180;
    const y = 80;

    // Path A — explicit primitives + edges from upstream
    if (primitives && primitives.length > 0) {
      const ns: Node[] = primitives.map((p, i) => ({
        id: p.id,
        position: { x: 40 + i * xStep, y },
        data: { label: p.label, sublabel: p.sublabel },
        type:
          i === 0
            ? "event"
            : i === primitives.length - 1
              ? "company"
              : "primitive",
      }));
      const es: Edge[] = (edges ?? []).map((e, i) => ({
        id: `e${i}`,
        source: e.source,
        target: e.target,
        label:
          e.beta !== undefined
            ? `β=${e.beta.toFixed(2)}${e.lag !== undefined ? ` · ${e.lag}mo` : ""}`
            : undefined,
        labelBgPadding: [4, 4],
        labelBgStyle: { fill: "#fff", fillOpacity: 0.9 },
        style: { stroke: "#64748b", strokeWidth: 1.5 },
        animated: false,
      }));
      return { nodes: ns, rfEdges: es };
    }

    // Path B — fallback chain matching legacy CausalChainViz shape
    const safeHops = Math.max(1, Math.min(8, hops || 1));
    const totalNodes = safeHops + 1;
    const fallbackNodes: Node[] = Array.from({ length: totalNodes }, (_, i) => ({
      id: `n${i}`,
      position: { x: 40 + i * xStep, y },
      data: {
        label:
          i === 0
            ? "Event"
            : i === safeHops
              ? "Company"
              : `Hop ${i}`,
        sublabel:
          i === 0 && relationshipType
            ? RELATIONSHIP_LABELS[relationshipType] ?? relationshipType
            : undefined,
        tooltip: explanation ?? undefined,
      },
      type: i === 0 ? "event" : i === safeHops ? "company" : "primitive",
    }));
    const fallbackEdges: Edge[] = Array.from({ length: safeHops }, (_, i) => ({
      id: `e${i}`,
      source: `n${i}`,
      target: `n${i + 1}`,
      style: { stroke: "#64748b", strokeWidth: 1.5 },
    }));
    return { nodes: fallbackNodes, rfEdges: fallbackEdges };
  }, [hops, relationshipType, explanation, primitives, edges]);

  return (
    <div
      style={{
        height: "240px",
        width: "100%",
        background: "#f8fafc",
        borderRadius: "8px",
        border: "1px solid #e2e8f0",
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} color="#cbd5e1" />
        <Controls showInteractive={false} />
      </ReactFlow>
      {impactScore !== undefined && (
        <div
          style={{
            fontSize: "11px",
            color: "#64748b",
            marginTop: "4px",
            textAlign: "right",
          }}
        >
          Impact score: {impactScore.toFixed(2)}
        </div>
      )}
    </div>
  );
}
