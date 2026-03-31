/** Impact metrics — 3-column metric grid (Stage 6.7) */

import { formatCurrency } from "@/lib/utils";

interface ImpactMetricsProps {
  impactScore: number;
  causalHops: number;
  financialExposure: number | null;
  companyName: string;
  confidence?: number | null;
}

export function ImpactMetrics({
  impactScore,
  causalHops,
  financialExposure,
  companyName,
  confidence,
}: ImpactMetricsProps) {
  void confidence; // Used in future metrics display
  const scoreColor =
    impactScore >= 80 ? "text-red-600" : impactScore >= 50 ? "text-amber-600" : "text-green-600";

  return (
    <div>
      <h4 className="text-sm font-semibold text-gray-700 mb-2">
        Impact on {companyName}
      </h4>
      <div className="grid grid-cols-3 gap-3">
        <MetricCell
          label="Impact Score"
          value={impactScore.toFixed(0)}
          color={scoreColor}
        />
        <MetricCell
          label="Causal Hops"
          value={String(causalHops)}
          color="text-indigo-600"
          sublabel={causalHops === 0 ? "Direct" : `${causalHops} hop${causalHops > 1 ? "s" : ""}`}
        />
        <MetricCell
          label="Exposure"
          value={financialExposure ? formatCurrency(financialExposure) : "—"}
          color="text-gray-900"
        />
      </div>
    </div>
  );
}

function MetricCell({
  label,
  value,
  color,
  sublabel,
}: {
  label: string;
  value: string;
  color: string;
  sublabel?: string;
}) {
  return (
    <div className="bg-gray-50 rounded-lg p-3 text-center border border-gray-100">
      <p className={`text-xl font-bold ${color}`}>{value}</p>
      <p className="text-[10px] text-muted-foreground mt-0.5">{label}</p>
      {sublabel && <p className="text-[9px] text-muted-foreground">{sublabel}</p>}
    </div>
  );
}
