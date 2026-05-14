/**
 * Phase C — Inline card rendering an MCP tool invocation in the chat stream.
 *
 * Shows pending → running → done state with progress beats. When the tool
 * is destructive AND the server returned `signoff_required`, renders an
 * "Authorise" button that resends the chat message with the verbatim
 * sign-off phrase appended.
 */
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";

interface ToolInvocationCardProps {
  tool: string;
  state: "pending" | "running" | "ok" | "error" | "signoff_required";
  result?: Record<string, unknown> | null;
  error?: { code: string; message: string } | null;
  signoffPhrase?: string | null;
  onAuthorize?: (phrase: string) => void;
}

export function ToolInvocationCard({
  tool, state, result, error, signoffPhrase, onAuthorize,
}: ToolInvocationCardProps): ReactNode {
  const badge =
    state === "ok" ? "✓"
      : state === "error" ? "✕"
      : state === "signoff_required" ? "⚠"
      : "…";

  const borderColour =
    state === "ok" ? "border-green-300"
      : state === "error" ? "border-red-300"
      : state === "signoff_required" ? "border-amber-300"
      : "border-gray-300";

  return (
    <div className={`mt-2 rounded border ${borderColour} bg-white p-2 text-xs`}>
      <div className="flex items-center justify-between">
        <span className="font-semibold">
          {badge} Tool · {tool}
        </span>
        <span className="text-gray-500">{state}</span>
      </div>
      {state === "signoff_required" && signoffPhrase && onAuthorize && (
        <div className="mt-2 flex items-center gap-2">
          <span className="text-amber-900">
            Reply <code className="rounded bg-amber-100 px-1">{signoffPhrase}</code> to authorize.
          </span>
          <Button onClick={() => onAuthorize(signoffPhrase)} size="sm">Authorize</Button>
        </div>
      )}
      {state === "ok" && result && (
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-gray-50 p-1 text-[10px]">
          {JSON.stringify(result, null, 2)}
        </pre>
      )}
      {state === "error" && error && (
        <div className="mt-1 text-red-700">
          {error.code}: {error.message}
        </div>
      )}
    </div>
  );
}
