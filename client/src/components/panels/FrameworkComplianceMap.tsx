/** Framework Compliance Map — per-framework indicator cards (Stage 6.6) */

import { useState } from "react";
import type { FrameworkHit } from "@/types";
import { getFrameworkColor, parseFrameworkTag, inferFrameworks, FRAMEWORK_LABELS } from "@/lib/frameworkMeta";

interface FrameworkComplianceMapProps {
  frameworkHits: FrameworkHit[];
  frameworks: string[];
  esgPillar: string | null;
}

const INITIAL_SHOW = 4;

export function FrameworkComplianceMap({
  frameworkHits,
  frameworks,
  esgPillar,
}: FrameworkComplianceMapProps) {
  const [showAll, setShowAll] = useState(false);

  // Use framework_hits if available, else build from framework strings, else infer
  let hits: FrameworkHit[] = frameworkHits;
  if (!hits || hits.length === 0) {
    const tags = frameworks.length > 0 ? frameworks : inferFrameworks(esgPillar);
    hits = tags.map((tag) => {
      const parsed = parseFrameworkTag(tag);
      return {
        framework: parsed.framework,
        indicator: parsed.indicator,
        indicator_name: null,
        relevance: null,
        explanation: null,
      };
    });
  }

  if (hits.length === 0) {
    return (
      <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
        <h4 className="text-sm font-semibold text-gray-700 mb-2">Framework Alignment</h4>
        <p className="text-sm text-muted-foreground">Framework analysis pending</p>
        <button className="mt-2 text-xs text-indigo-600 font-medium hover:underline">
          Request Analysis
        </button>
      </div>
    );
  }

  const visible = showAll ? hits : hits.slice(0, INITIAL_SHOW);
  const remaining = hits.length - INITIAL_SHOW;

  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-700 mb-3">Framework Alignment</h4>
      <div className="grid grid-cols-2 gap-2">
        {visible.map((hit, i) => (
          <FrameworkCard key={`${hit.framework}-${hit.indicator}-${i}`} hit={hit} />
        ))}
      </div>
      {remaining > 0 && !showAll && (
        <button
          onClick={() => setShowAll(true)}
          className="mt-2 text-xs text-indigo-600 font-medium hover:underline"
        >
          Show {remaining} more
        </button>
      )}
    </div>
  );
}

function FrameworkCard({ hit }: { hit: FrameworkHit }) {
  const colorClasses = getFrameworkColor(hit.framework);
  const label = FRAMEWORK_LABELS[hit.framework] || hit.framework;

  return (
    <div className={`rounded-lg border p-3 ${colorClasses}`}>
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-bold">{label}</span>
        {hit.indicator && (
          <span className="text-[10px] font-mono opacity-75">{hit.indicator}</span>
        )}
      </div>
      {hit.indicator_name && (
        <p className="text-[11px] mt-0.5 opacity-80">{hit.indicator_name}</p>
      )}
      {hit.relevance != null && (
        <div className="mt-1.5">
          <div className="h-1 bg-black/10 rounded-full overflow-hidden">
            <div
              className="h-full bg-current opacity-60 rounded-full transition-all"
              style={{ width: `${hit.relevance * 100}%` }}
            />
          </div>
          <p className="text-[9px] mt-0.5 opacity-60">
            Relevance: {(hit.relevance * 100).toFixed(0)}%
          </p>
        </div>
      )}
      {hit.explanation && (
        <p className="text-[10px] mt-1 opacity-70 line-clamp-2">{hit.explanation}</p>
      )}
    </div>
  );
}
