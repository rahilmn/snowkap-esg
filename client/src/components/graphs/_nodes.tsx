/** Repos Integration W2 — Custom react-flow node types for Snowkap graphs.
 *
 * Each node type gets its own React component + colour palette so the
 * causal cascade reads as "ESG primitive nodes" rather than generic
 * boxes. Per the L0-L7 audit aesthetic, role accents are:
 *   CFO    — orange
 *   CEO    — emerald
 *   Analyst — blue
 *
 * Cascade primitives use a sober slate palette since they're shared
 * across roles. Event nodes are amber; outcome nodes are violet.
 */

import { Handle, Position, type NodeProps } from "reactflow";

interface BaseNodeData {
  label: string;
  sublabel?: string;
  tooltip?: string;
}

const _SHARED_STYLE: React.CSSProperties = {
  padding: "8px 12px",
  borderRadius: "8px",
  fontSize: "12px",
  fontWeight: 500,
  minWidth: "80px",
  textAlign: "center",
  border: "1.5px solid",
};

function _NodeShell({
  data,
  accent,
  background,
  border,
  text,
}: NodeProps<BaseNodeData> & {
  accent?: string;
  background: string;
  border: string;
  text: string;
}) {
  return (
    <div
      title={data.tooltip ?? data.label}
      style={{
        ..._SHARED_STYLE,
        background,
        borderColor: border,
        color: text,
        boxShadow: accent ? `0 0 0 2px ${accent}33` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: border }} />
      <div>{data.label}</div>
      {data.sublabel && (
        <div style={{ fontSize: "10px", opacity: 0.7, marginTop: "2px" }}>
          {data.sublabel}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: border }} />
    </div>
  );
}

export function PrimitiveNode(props: NodeProps<BaseNodeData>) {
  return (
    <_NodeShell
      {...props}
      background="#f1f5f9"
      border="#475569"
      text="#0f172a"
    />
  );
}

export function EventNode(props: NodeProps<BaseNodeData>) {
  return (
    <_NodeShell
      {...props}
      background="#fef3c7"
      border="#d97706"
      text="#7c2d12"
    />
  );
}

export function OutcomeNode(props: NodeProps<BaseNodeData>) {
  return (
    <_NodeShell
      {...props}
      background="#ede9fe"
      border="#7c3aed"
      text="#4c1d95"
    />
  );
}

export function CompanyNode(props: NodeProps<BaseNodeData>) {
  return (
    <_NodeShell
      {...props}
      background="#0f172a"
      border="#0f172a"
      text="#f8fafc"
    />
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export const nodeTypes = {
  primitive: PrimitiveNode,
  event: EventNode,
  outcome: OutcomeNode,
  company: CompanyNode,
};
