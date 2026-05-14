/**
 * Phase C — Inline badge rendering a Toulmin chain on a chat message.
 *
 * Shown when an assistant message carries `toulmin = {claim, grounds, warrant, ...}`.
 * Compact view by default; expands on click for the full block.
 */
import { useState } from "react";

interface ToulminBadgeProps {
  chain: {
    claim?: string;
    grounds?: string[] | string;
    warrant?: string;
    qualifier?: string;
    rebuttal?: string;
  };
}

export function ToulminBadge({ chain }: ToulminBadgeProps) {
  const [open, setOpen] = useState(false);
  if (!chain || !chain.claim) return null;
  return (
    <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="font-semibold text-amber-900"
      >
        {open ? "▼" : "▶"} Toulmin: {chain.claim}
      </button>
      {open && (
        <dl className="mt-1 space-y-1 text-amber-900">
          {chain.grounds && (
            <div>
              <dt className="font-semibold">Grounds</dt>
              <dd className="ml-3">
                {Array.isArray(chain.grounds) ? chain.grounds.join("; ") : chain.grounds}
              </dd>
            </div>
          )}
          {chain.warrant && (
            <div>
              <dt className="font-semibold">Warrant</dt>
              <dd className="ml-3">{chain.warrant}</dd>
            </div>
          )}
          {chain.qualifier && (
            <div>
              <dt className="font-semibold">Qualifier</dt>
              <dd className="ml-3">{chain.qualifier}</dd>
            </div>
          )}
          {chain.rebuttal && (
            <div>
              <dt className="font-semibold">Rebuttal</dt>
              <dd className="ml-3">{chain.rebuttal}</dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}
