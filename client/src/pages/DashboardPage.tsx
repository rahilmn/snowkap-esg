import { useQuery } from "@tanstack/react-query";
import { predictions, ontology } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { confidenceLabel, confidenceColor } from "@/lib/utils";

export function DashboardPage() {
  const name = useAuthStore((s) => s.name);

  const statsQuery = useQuery({
    queryKey: ["prediction-stats"],
    queryFn: predictions.stats,
  });

  const ontologyQuery = useQuery({
    queryKey: ["ontology-stats"],
    queryFn: ontology.stats,
  });

  const recentQuery = useQuery({
    queryKey: ["recent-predictions"],
    queryFn: () => predictions.list({ limit: 5 }),
  });

  const stats = statsQuery.data;
  const oStats = ontologyQuery.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">
          {name ? `Hello ${name}` : "Dashboard"}
        </h1>
        {name && (
          <p className="text-sm text-muted-foreground mt-1">
            Here are news that might impact you
          </p>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Predictions"
          value={stats?.total_predictions ?? "-"}
          subtitle={`${stats?.completed_count ?? 0} completed`}
        />
        <StatCard
          title="Avg Confidence"
          value={stats ? `${(stats.avg_confidence * 100).toFixed(0)}%` : "-"}
          subtitle={stats ? confidenceLabel(stats.avg_confidence) : ""}
        />
        <StatCard
          title="High Risk"
          value={stats?.high_risk_count ?? "-"}
          subtitle="Confidence > 70%"
          alert={!!stats && stats.high_risk_count > 0}
        />
        <StatCard
          title="Knowledge Graph"
          value={oStats ? String(
            (oStats.companies ?? 0) + (oStats.suppliers ?? 0) + (oStats.facilities ?? 0)
          ) : "-"}
          subtitle="Entities tracked"
        />
      </div>

      {/* Ontology Summary */}
      {oStats && (
        <Card>
          <CardHeader>
            <CardTitle>Ontology Overview</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <MiniStat label="Companies" value={oStats.companies} />
              <MiniStat label="Facilities" value={oStats.facilities} />
              <MiniStat label="Suppliers" value={oStats.suppliers} />
              <MiniStat label="Frameworks" value={oStats.frameworks} />
              <MiniStat label="Material Issues" value={oStats.material_issues} />
              <MiniStat label="Commodities" value={oStats.commodities} />
              <MiniStat label="Causal Chains" value={oStats.causal_chains} />
              <MiniStat label="Regulations" value={oStats.regulations} />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent Predictions */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Predictions</CardTitle>
        </CardHeader>
        <CardContent>
          {recentQuery.isLoading ? (
            <div className="flex justify-center py-8"><Spinner /></div>
          ) : recentQuery.data?.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4">No predictions yet. Predictions are triggered when high-impact news is detected.</p>
          ) : (
            <div className="space-y-3">
              {recentQuery.data?.map((p) => (
                <div key={p.id} className="flex items-start justify-between rounded-md border p-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{p.title}</p>
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{p.summary}</p>
                  </div>
                  <div className="ml-3 flex flex-col items-end gap-1">
                    <span className={`text-sm font-semibold ${confidenceColor(p.confidence_score)}`}>
                      {(p.confidence_score * 100).toFixed(0)}%
                    </span>
                    <Badge variant={p.status === "completed" ? "default" : "secondary"}>
                      {p.status}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatCard({
  title,
  value,
  subtitle,
  alert,
}: {
  title: string;
  value: string | number;
  subtitle: string;
  alert?: boolean;
}) {
  return (
    <Card>
      <CardContent className="p-6">
        <p className="text-sm font-medium text-muted-foreground">{title}</p>
        <p className={`text-2xl font-bold mt-1 ${alert ? "text-destructive" : ""}`}>{value}</p>
        <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>
      </CardContent>
    </Card>
  );
}

function MiniStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center">
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
