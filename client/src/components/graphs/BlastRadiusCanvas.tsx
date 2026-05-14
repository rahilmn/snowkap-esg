/** Repos Integration W2 — Blast-radius canvas for advisor approvals
 *
 * Before an analyst approves an unverified candidate, this canvas
 * shows the candidate at the centre + edges to every entity / article /
 * downstream rule that would be affected by the promotion.
 *
 * Helps the analyst answer "what changes if I approve this?" in 3
 * seconds instead of reading a JSON diff.
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

export interface BlastRadiusCanvasProps {
  candidateId: string;
  candidateLabel: string;
  category: string;
  affected: Array<{
    id: string;
    label: string;
    kind: "article" | "rule" | "entity" | "framework";
    note?: string;
  }>;
}

export function BlastRadiusCanvas({
  candidateId,
  candidateLabel,
  category,
  affected,
}: BlastRadiusCanvasProps) {
  const { nodes, edges } = useMemo(() => {
    const centerX = 280;
    const centerY = 140;
    const radius = 120;

    const center: Node = {
      id: candidateId,
      position: { x: centerX, y: centerY },
      data: { label: candidateLabel, sublabel: category },
      type: "event",
    };

    const ringNodes: Node[] = affected.map((a, i) => {
      const angle = (i / Math.max(1, affected.length)) * Math.PI * 2;
      return {
        id: a.id,
        position: {
          x: centerX + Math.cos(angle) * radius,
          y: centerY + Math.sin(angle) * radius,
        },
        data: { label: a.label, sublabel: a.kind, tooltip: a.note },
        type: a.kind === "article" ? "primitive" : "outcome",
      };
    });

    const ringEdges: Edge[] = affected.map((a, i) => ({
      id: `e${i}`,
      source: candidateId,
      target: a.id,
      style: {
        stroke: a.kind === "rule" ? "#dc2626" : "#94a3b8",
        strokeWidth: a.kind === "rule" ? 2 : 1.5,
      },
    }));

    return { nodes: [center, ...ringNodes], edges: ringEdges };
  }, [candidateId, candidateLabel, category, affected]);

  if (affected.length === 0) {
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
        No downstream effects detected. Safe to approve.
      </div>
    );
  }

  return (
    <div
      style={{
        height: "280px",
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
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} color="#cbd5e1" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
