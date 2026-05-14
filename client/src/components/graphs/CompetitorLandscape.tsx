/** Repos Integration W2 — Competitor landscape map
 *
 * Renders the target tenant + its known competitors as a force-directed
 * network. Each company is a node; edges represent shared ESG risks /
 * industries / regulatory zones.
 *
 * Props are intentionally minimal so the component can be embedded
 * on HomePage with whatever competitor data is on hand. When no
 * competitor data is available, renders a graceful empty state.
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

export interface CompetitorLandscapeProps {
  tenantSlug: string;
  tenantName: string;
  competitors: Array<{
    slug: string;
    name: string;
    sharedRisks?: string[];
  }>;
  onCompetitorClick?: (slug: string) => void;
}

export function CompetitorLandscape({
  tenantSlug,
  tenantName,
  competitors,
  onCompetitorClick,
}: CompetitorLandscapeProps) {
  const { nodes, edges } = useMemo(() => {
    // Centre node = the target tenant. Competitors arranged on a circle
    // around it. This is a deterministic layout — react-flow keeps it
    // stable across renders.
    const centerX = 300;
    const centerY = 160;
    const radius = 150;

    const centerNode: Node = {
      id: tenantSlug,
      position: { x: centerX, y: centerY },
      data: { label: tenantName, sublabel: "this tenant" },
      type: "company",
    };

    const compNodes: Node[] = competitors.map((c, i) => {
      const angle = (i / Math.max(1, competitors.length)) * Math.PI * 2;
      return {
        id: c.slug,
        position: {
          x: centerX + Math.cos(angle) * radius,
          y: centerY + Math.sin(angle) * radius,
        },
        data: {
          label: c.name,
          sublabel: c.sharedRisks?.length
            ? `${c.sharedRisks.length} shared risk${c.sharedRisks.length > 1 ? "s" : ""}`
            : undefined,
          tooltip: c.sharedRisks?.join(" · "),
        },
        type: "primitive",
      };
    });

    const compEdges: Edge[] = competitors.map((c, i) => ({
      id: `e${i}`,
      source: tenantSlug,
      target: c.slug,
      label:
        c.sharedRisks && c.sharedRisks.length > 0
          ? c.sharedRisks[0]
          : undefined,
      labelBgPadding: [4, 4],
      labelBgStyle: { fill: "#fff", fillOpacity: 0.9 },
      style: { stroke: "#94a3b8", strokeWidth: 1.5 },
    }));

    return { nodes: [centerNode, ...compNodes], edges: compEdges };
  }, [tenantSlug, tenantName, competitors]);

  if (competitors.length === 0) {
    return (
      <div
        style={{
          padding: "16px",
          background: "#f8fafc",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          fontSize: "13px",
          color: "#64748b",
          textAlign: "center",
        }}
      >
        No competitor data available for {tenantName}.
      </div>
    );
  }

  return (
    <div
      style={{
        height: "340px",
        width: "100%",
        background: "#f8fafc",
        borderRadius: "8px",
        border: "1px solid #e2e8f0",
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable
        nodesConnectable={false}
        onNodeClick={(_evt, node) => {
          if (node.id !== tenantSlug && onCompetitorClick) {
            onCompetitorClick(node.id);
          }
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="#cbd5e1" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
