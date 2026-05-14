/** Repos Integration W3 — Sentiment-trajectory chart with confidence bands.
 *
 * Renders the forecaster output: central projected polarity line +
 * 68% confidence band. Used by:
 *   - ArticleDetailSheet hero metric (12-month outlook)
 *   - StrategicHorizonPanel (CEO 3-year view, see sibling component)
 *   - OutlookTile (HomePage 30-day stacked-area)
 *
 * Built over `recharts` which is already in the stack (no new dep).
 */

import {
  Area,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface TrajectoryPoint {
  month: string; // YYYY-MM
  central: number;
  lo: number;
  hi: number;
}

interface TrajectoryChartProps {
  trajectory: TrajectoryPoint[];
  height?: number;
  /** Display label for the y-axis */
  metricLabel?: string;
  /** When true, hide y-axis labels for a more compact display */
  compact?: boolean;
}

export function TrajectoryChart({
  trajectory,
  height = 200,
  metricLabel = "Polarity",
  compact = false,
}: TrajectoryChartProps) {
  if (!trajectory || trajectory.length === 0) {
    return (
      <div
        style={{
          height,
          background: "#f8fafc",
          borderRadius: "8px",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#64748b",
          fontSize: "13px",
        }}
      >
        No trajectory data yet.
      </div>
    );
  }

  // Compute the area as {month, central, range: [lo, hi]} — recharts
  // renders a range-Area from a tuple value.
  const data = trajectory.map((p) => ({
    month: p.month,
    central: p.central,
    range: [p.lo, p.hi] as [number, number],
  }));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 16, left: -10, bottom: 4 }}>
          <XAxis
            dataKey="month"
            tick={{ fontSize: 10, fill: "#64748b" }}
            axisLine={{ stroke: "#cbd5e1" }}
            tickLine={false}
          />
          <YAxis
            domain={[-1, 1]}
            tick={compact ? false : { fontSize: 10, fill: "#64748b" }}
            axisLine={{ stroke: "#cbd5e1" }}
            tickLine={false}
            label={
              compact
                ? undefined
                : {
                    value: metricLabel,
                    angle: -90,
                    position: "insideLeft",
                    style: { fontSize: 10, fill: "#64748b" },
                  }
            }
          />
          <Tooltip
            contentStyle={{
              fontSize: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 6,
            }}
            formatter={(value: unknown, name: string) => {
              if (name === "range" && Array.isArray(value)) {
                return [`${value[0].toFixed(2)} – ${value[1].toFixed(2)}`, "68% band"];
              }
              return [(value as number).toFixed(2), "Central"];
            }}
          />
          <Area
            type="monotone"
            dataKey="range"
            stroke="none"
            fill="#3b82f6"
            fillOpacity={0.15}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="central"
            stroke="#1e40af"
            strokeWidth={2}
            dot={{ r: 2 }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
