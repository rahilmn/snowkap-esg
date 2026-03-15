import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { predictions } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { confidenceColor, confidenceLabel, formatCurrency } from "@/lib/utils";
import type { PredictionReport, PredictionDetail } from "@/types";

export function PredictionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const statsQuery = useQuery({
    queryKey: ["prediction-stats"],
    queryFn: predictions.stats,
  });

  const listQuery = useQuery({
    queryKey: ["predictions"],
    queryFn: () => predictions.list({ limit: 50 }),
  });

  const detailQuery = useQuery({
    queryKey: ["prediction", selectedId],
    queryFn: () => predictions.get(selectedId!),
    enabled: !!selectedId,
  });

  const stats = statsQuery.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">MiroFish Predictions</h1>

      {/* Stats Bar */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MiniStat label="Total" value={stats.total_predictions} />
          <MiniStat label="Completed" value={stats.completed_count} />
          <MiniStat label="Pending" value={stats.pending_count} />
          <MiniStat label="High Risk" value={stats.high_risk_count} alert />
          <MiniStat label="Avg Confidence" value={`${(stats.avg_confidence * 100).toFixed(0)}%`} />
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Predictions List */}
        <div className="lg:col-span-1 space-y-3">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Reports</h2>
          {listQuery.isLoading ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : (
            listQuery.data?.map((p) => (
              <PredictionCard
                key={p.id}
                prediction={p}
                isSelected={selectedId === p.id}
                onSelect={() => setSelectedId(p.id)}
              />
            ))
          )}
          {listQuery.data?.length === 0 && (
            <p className="text-sm text-muted-foreground py-4">
              No predictions yet. They are triggered automatically for high-impact news (score &gt;70).
            </p>
          )}
        </div>

        {/* Detail Panel */}
        <div className="lg:col-span-2">
          {selectedId && detailQuery.data ? (
            <PredictionDetailPanel prediction={detailQuery.data} />
          ) : (
            <Card>
              <CardContent className="py-16 text-center text-muted-foreground">
                Select a prediction to view details
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function PredictionCard({
  prediction: p,
  isSelected,
  onSelect,
}: {
  prediction: PredictionReport;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <Card
      className={`cursor-pointer transition-all hover:shadow-md ${isSelected ? "ring-2 ring-primary" : ""}`}
      onClick={onSelect}
    >
      <CardContent className="p-4">
        <div className="flex justify-between items-start">
          <p className="text-sm font-medium line-clamp-2 flex-1">{p.title}</p>
          <span className={`text-lg font-bold ml-2 ${confidenceColor(p.confidence_score)}`}>
            {(p.confidence_score * 100).toFixed(0)}%
          </span>
        </div>
        <div className="flex items-center gap-2 mt-2">
          <Badge variant={p.status === "completed" ? "default" : "secondary"} className="text-[10px]">
            {p.status}
          </Badge>
          {p.time_horizon && (
            <span className="text-xs text-muted-foreground">{p.time_horizon} term</span>
          )}
          {p.financial_impact && (
            <span className="text-xs text-muted-foreground">{formatCurrency(p.financial_impact)}</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function PredictionDetailPanel({ prediction: p }: { prediction: PredictionDetail }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{p.title}</CardTitle>
        <CardDescription>{p.summary}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Confidence & Financial */}
        <div className="grid grid-cols-3 gap-4">
          <div className="rounded-md border p-3 text-center">
            <p className={`text-2xl font-bold ${confidenceColor(p.confidence_score)}`}>
              {(p.confidence_score * 100).toFixed(0)}%
            </p>
            <p className="text-xs text-muted-foreground">Confidence ({confidenceLabel(p.confidence_score)})</p>
          </div>
          <div className="rounded-md border p-3 text-center">
            <p className="text-2xl font-bold">
              {p.financial_impact ? formatCurrency(p.financial_impact) : "N/A"}
            </p>
            <p className="text-xs text-muted-foreground">Financial Impact</p>
          </div>
          <div className="rounded-md border p-3 text-center">
            <p className="text-2xl font-bold capitalize">{p.time_horizon ?? "N/A"}</p>
            <p className="text-xs text-muted-foreground">Time Horizon</p>
          </div>
        </div>

        {/* Prediction Text */}
        {p.prediction_text && (
          <div>
            <h3 className="text-sm font-semibold mb-2">Prediction</h3>
            <p className="text-sm text-muted-foreground whitespace-pre-line">{p.prediction_text}</p>
          </div>
        )}

        {/* Agent Consensus */}
        {p.agent_consensus && (
          <div>
            <h3 className="text-sm font-semibold mb-2">Agent Consensus</h3>
            <div className="space-y-2 text-sm">
              {p.agent_consensus.risk_level && (
                <div className="flex gap-2">
                  <span className="text-muted-foreground">Risk Level:</span>
                  <Badge variant={p.agent_consensus.risk_level === "high" ? "destructive" : "secondary"}>
                    {p.agent_consensus.risk_level}
                  </Badge>
                </div>
              )}
              {p.agent_consensus.analysis && (
                <p className="text-muted-foreground">{p.agent_consensus.analysis}</p>
              )}
              {p.agent_consensus.recommendation && (
                <div className="rounded-md bg-muted p-3">
                  <p className="text-xs font-medium mb-1">Recommendation</p>
                  <p className="text-sm">{p.agent_consensus.recommendation}</p>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Simulation Runs */}
        {p.simulation_runs?.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold mb-2">Simulation Runs</h3>
            <div className="space-y-2">
              {p.simulation_runs.map((run) => (
                <div key={run.id} className="flex items-center gap-4 text-xs border rounded-md p-2">
                  <span>{run.agent_count} agents</span>
                  <span>{run.rounds} rounds</span>
                  <span>Convergence: {(run.convergence_score * 100).toFixed(0)}%</span>
                  <span>{run.duration_seconds}s</span>
                  <Badge variant={run.status === "completed" ? "default" : "secondary"} className="ml-auto text-[10px]">
                    {run.status}
                  </Badge>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MiniStat({ label, value, alert }: { label: string; value: number | string; alert?: boolean }) {
  return (
    <Card>
      <CardContent className="p-3 text-center">
        <p className={`text-xl font-bold ${alert ? "text-destructive" : ""}`}>{value}</p>
        <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      </CardContent>
    </Card>
  );
}
