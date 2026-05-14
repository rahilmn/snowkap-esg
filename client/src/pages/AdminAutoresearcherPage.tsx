/** Autoresearcher Phase B — admin dashboard
 *
 * Karpathy-style experiment browser:
 *  - leaderboard of top kept experiments by metric delta
 *  - full ledger (newest-first) with filters by tier
 *  - "Run experiment" button (tier=system | tenant | user)
 *
 * Auth-gated: `manage_drip_campaigns` permission required.
 *
 * Backend: api/routes/autoresearcher.py
 *   GET  /api/autoresearcher/experiments?tier=&limit=
 *   GET  /api/autoresearcher/leaderboard?tier=&top_n=
 *   POST /api/autoresearcher/run
 */

import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  autoresearcher as autoresearcherApi,
  type AutoresearcherExperiment,
} from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

type Tier = "system" | "tenant" | "user";

export default function AdminAutoresearcherPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  if (!hasPermission("manage_drip_campaigns")) {
    return <Navigate to="/home" replace />;
  }
  return <AdminAutoresearcherInner />;
}

function AdminAutoresearcherInner() {
  const queryClient = useQueryClient();
  const [tier, setTier] = useState<Tier>("system");
  const [tenantSlug, setTenantSlug] = useState("");
  const [userId, setUserId] = useState("");
  const [budget, setBudget] = useState(20);

  const experimentsQuery = useQuery({
    queryKey: ["autoresearcher-experiments", tier],
    queryFn: () => autoresearcherApi.experiments({ tier, limit: 50 }),
    refetchInterval: 30_000,
  });

  const leaderboardQuery = useQuery({
    queryKey: ["autoresearcher-leaderboard", tier],
    queryFn: () => autoresearcherApi.leaderboard({ tier, top_n: 10 }),
    refetchInterval: 30_000,
  });

  const runMutation = useMutation({
    mutationFn: (req: Parameters<typeof autoresearcherApi.run>[0]) =>
      autoresearcherApi.run(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["autoresearcher-experiments"] });
      queryClient.invalidateQueries({ queryKey: ["autoresearcher-leaderboard"] });
    },
  });

  const handleRun = () => {
    runMutation.mutate({
      tier,
      tenant_slug: tier === "tenant" ? tenantSlug : undefined,
      user_id: tier === "user" ? userId : undefined,
      budget,
      seed: 42,
      keep_threshold: 0.02,
    });
  };

  const stats = useMemo(() => {
    const items = experimentsQuery.data?.experiments ?? [];
    return {
      total: items.length,
      keeps: items.filter((e) => e.decision === "keep").length,
      discards: items.filter((e) => e.decision === "discard").length,
    };
  }, [experimentsQuery.data]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Autoresearcher</h1>
        <p className="mt-1 text-sm text-slate-600">
          Karpathy-style autonomous calibration loop. Continuously proposes
          ontology / scorer perturbations, replays them against a held-out
          corpus, and keeps changes that improve the composite metric.
        </p>
      </header>

      {/* Tier picker + Run button */}
      <section className="mb-8 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm text-slate-700">
            Tier:
            <select
              value={tier}
              onChange={(e) => setTier(e.target.value as Tier)}
              className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="system">system</option>
              <option value="tenant">tenant</option>
              <option value="user">user</option>
            </select>
          </label>

          {tier === "tenant" && (
            <label className="text-sm text-slate-700">
              Tenant slug:
              <input
                value={tenantSlug}
                onChange={(e) => setTenantSlug(e.target.value)}
                className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="adani-power"
              />
            </label>
          )}

          {tier === "user" && (
            <label className="text-sm text-slate-700">
              User id:
              <input
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="alice@example.com"
              />
            </label>
          )}

          <label className="text-sm text-slate-700">
            Budget:
            <input
              type="number"
              min={1}
              max={2000}
              value={budget}
              onChange={(e) => setBudget(parseInt(e.target.value || "0", 10))}
              className="ml-2 w-20 rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>

          <Button
            onClick={handleRun}
            disabled={
              runMutation.isPending ||
              (tier === "tenant" && !tenantSlug) ||
              (tier === "user" && !userId)
            }
          >
            {runMutation.isPending ? "Running…" : "Run experiment"}
          </Button>
        </div>

        {runMutation.isSuccess && runMutation.data && (
          <div className="mt-3 rounded bg-emerald-50 p-3 text-sm text-emerald-900">
            <strong>Run complete.</strong> {runMutation.data.n_keeps} keeps,
            {" "}{runMutation.data.n_discards} discards, {runMutation.data.n_errors} errors.
            Top Δ: {runMutation.data.top_delta?.toFixed(4) ?? "0.0000"}
            {runMutation.data.top_knob_id && (
              <span> ({runMutation.data.top_knob_id})</span>
            )}
          </div>
        )}

        {runMutation.isError && (
          <div className="mt-3 rounded bg-red-50 p-3 text-sm text-red-700">
            Run failed. Check the backend logs.
          </div>
        )}
      </section>

      {/* Stats */}
      <section className="mb-6 grid grid-cols-3 gap-3">
        <StatCard label="Experiments" value={stats.total} />
        <StatCard label="Keeps" value={stats.keeps} highlight="emerald" />
        <StatCard label="Discards" value={stats.discards} highlight="slate" />
      </section>

      {/* Leaderboard */}
      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">
          Top hits (tier = {tier})
        </h2>
        {leaderboardQuery.isLoading ? (
          <Spinner />
        ) : (leaderboardQuery.data?.entries ?? []).length === 0 ? (
          <div className="rounded bg-slate-50 p-4 text-sm text-slate-500">
            No kept experiments yet at this tier.
          </div>
        ) : (
          <ExperimentTable
            experiments={leaderboardQuery.data?.entries ?? []}
          />
        )}
      </section>

      {/* Full ledger */}
      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">
          Experiment ledger (newest first, last 50)
        </h2>
        {experimentsQuery.isLoading ? (
          <Spinner />
        ) : (experimentsQuery.data?.experiments ?? []).length === 0 ? (
          <div className="rounded bg-slate-50 p-4 text-sm text-slate-500">
            No experiments recorded yet. Click <em>Run experiment</em> above.
          </div>
        ) : (
          <ExperimentTable
            experiments={experimentsQuery.data?.experiments ?? []}
          />
        )}
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  highlight,
}: {
  label: string;
  value: number;
  highlight?: "emerald" | "slate";
}) {
  const colour =
    highlight === "emerald"
      ? "text-emerald-700"
      : highlight === "slate"
        ? "text-slate-700"
        : "text-slate-900";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div className={`mt-1 text-2xl font-semibold ${colour}`}>{value}</div>
    </div>
  );
}

function ExperimentTable({
  experiments,
}: {
  experiments: AutoresearcherExperiment[];
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-slate-600">Time</th>
            <th className="px-3 py-2 text-left font-medium text-slate-600">Knob kind</th>
            <th className="px-3 py-2 text-left font-medium text-slate-600">Knob id</th>
            <th className="px-3 py-2 text-right font-medium text-slate-600">Δ metric</th>
            <th className="px-3 py-2 text-left font-medium text-slate-600">Decision</th>
          </tr>
        </thead>
        <tbody>
          {experiments.map((e) => (
            <tr key={e.experiment_id} className="border-t border-slate-200">
              <td className="px-3 py-2 text-slate-600">{new Date(e.ts).toLocaleString()}</td>
              <td className="px-3 py-2 text-slate-700">{e.knob_kind}</td>
              <td className="px-3 py-2">
                <code className="rounded bg-slate-100 px-1 text-xs">{e.knob_id}</code>
              </td>
              <td
                className={`px-3 py-2 text-right font-mono ${
                  e.metric_delta > 0
                    ? "text-emerald-700"
                    : e.metric_delta < 0
                      ? "text-red-700"
                      : "text-slate-500"
                }`}
              >
                {e.metric_delta >= 0 ? "+" : ""}
                {e.metric_delta.toFixed(4)}
              </td>
              <td className="px-3 py-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    e.decision === "keep"
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-slate-100 text-slate-700"
                  }`}
                >
                  {e.decision}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
