/** Base Version Adoption L6 — Advisor queue review surface
 *
 * Lists currently-open advisor events (`high_uncertainty_decision` +
 * `unverified_candidate`) from `data/audit/advisor_queue.jsonl` and
 * lets an analyst approve or reject each one.
 *
 * Resolution path:
 *   - `approve` on an `unverified_candidate` → calls discovery.promoter
 *     `manual_decide(promote)` server-side, so the candidate is
 *     actually committed to the ontology
 *   - `reject` on an `unverified_candidate` → calls
 *     `manual_decide(reject)`
 *   - `approve`/`reject` on a `high_uncertainty_decision` → resolution
 *     log entry only (no candidate to act on)
 *
 * Auth-gated: `manage_drip_campaigns` permission required.
 *
 * Backend: api/routes/advisor.py
 *   GET  /api/advisor/queue?tenant={slug}
 *   POST /api/advisor/resolve
 */

import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { advisor as advisorApi, type AdvisorEvent } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { BlastRadiusCanvas } from "@/components/graphs/BlastRadiusCanvas";

type Resolution = "approve" | "reject";

const EVENT_TYPE_LABELS: Record<string, string> = {
  high_uncertainty_decision: "High-uncertainty decision",
  unverified_candidate: "Unverified candidate",
};

export default function AdvisorPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  if (!hasPermission("manage_drip_campaigns")) {
    return <Navigate to="/home" replace />;
  }
  return <AdvisorInner />;
}

function AdvisorInner() {
  const queryClient = useQueryClient();
  const [tenantFilter, setTenantFilter] = useState<string>("");
  const [reviewing, setReviewing] = useState<{
    event: AdvisorEvent;
    resolution: Resolution;
  } | null>(null);
  const [rationale, setRationale] = useState("");

  const queueQuery = useQuery({
    queryKey: ["advisor-queue", tenantFilter || undefined],
    queryFn: () => advisorApi.queue(tenantFilter || undefined),
    refetchInterval: 15_000,
  });

  const resolveMutation = useMutation({
    mutationFn: (req: Parameters<typeof advisorApi.resolve>[0]) =>
      advisorApi.resolve(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["advisor-queue"] });
      setReviewing(null);
      setRationale("");
    },
  });

  const grouped = useMemo(() => {
    const events = queueQuery.data?.events ?? [];
    const byType: Record<string, AdvisorEvent[]> = {};
    for (const e of events) {
      const key = e.event_type;
      if (!byType[key]) byType[key] = [];
      byType[key].push(e);
    }
    return byType;
  }, [queueQuery.data]);

  const handleSubmit = () => {
    if (!reviewing) return;
    resolveMutation.mutate({
      event_id: reviewing.event.event_id,
      resolution: reviewing.resolution,
      rationale: rationale.trim(),
    });
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold">Advisor queue</h1>
        <p className="mt-1 text-sm text-slate-600">
          High-uncertainty decisions and unverified candidates flagged for
          analyst review. Approve / reject to clear the queue; approve on
          an unverified candidate also promotes it into the ontology.
        </p>
      </header>

      <div className="mb-4 flex items-center gap-3">
        <label className="text-sm text-slate-700">
          Filter by tenant:
          <input
            type="text"
            value={tenantFilter}
            onChange={(e) => setTenantFilter(e.target.value)}
            placeholder="adani-power"
            className="ml-2 rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        {queueQuery.isFetching && <Spinner />}
        <span className="ml-auto text-sm text-slate-500">
          {queueQuery.data?.count ?? 0} open events
        </span>
      </div>

      {queueQuery.isError && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-700">
          Failed to load advisor queue. Refresh to retry.
        </div>
      )}

      {queueQuery.data?.count === 0 && (
        <div className="rounded bg-slate-50 p-6 text-center text-sm text-slate-600">
          No open events. The queue is clear.
        </div>
      )}

      {Object.entries(grouped).map(([type, events]) => (
        <section key={type} className="mb-8">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-slate-500">
            {EVENT_TYPE_LABELS[type] ?? type} · {events.length}
          </h2>
          <ul className="space-y-2">
            {events.map((event) => (
              <EventCard
                key={event.event_id}
                event={event}
                onAction={(resolution) => setReviewing({ event, resolution })}
              />
            ))}
          </ul>
        </section>
      ))}

      {reviewing && (
        <ReviewModal
          event={reviewing.event}
          resolution={reviewing.resolution}
          rationale={rationale}
          setRationale={setRationale}
          onCancel={() => {
            setReviewing(null);
            setRationale("");
          }}
          onSubmit={handleSubmit}
          submitting={resolveMutation.isPending}
          error={resolveMutation.isError ? "Resolution failed; please retry." : null}
        />
      )}
    </div>
  );
}

function EventCard({
  event,
  onAction,
}: {
  event: AdvisorEvent;
  onAction: (resolution: Resolution) => void;
}) {
  const tags = event.tags ?? {};
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm text-slate-500">
            {new Date(event.ts).toLocaleString()} · {event.company_slug ?? "—"}
          </div>
          <div className="mt-1 text-sm font-medium text-slate-900">
            {event.toulmin?.claim ??
              event.rationale ??
              event.source_decision_type ??
              event.event_type}
          </div>
          {event.toulmin?.grounds?.length ? (
            <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
              {event.toulmin.grounds.slice(0, 3).map((g, i) => (
                <li key={i}>{g}</li>
              ))}
            </ul>
          ) : null}
          <div className="mt-2 text-xs text-slate-500">
            <span className="font-medium">Attribution:</span>{" "}
            {tags.attribution ?? "—"} ·{" "}
            <span className="font-medium">Confidence:</span>{" "}
            {tags.uncertainty ?? "—"}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="default" onClick={() => onAction("approve")}>
            Approve
          </Button>
          <Button size="sm" variant="outline" onClick={() => onAction("reject")}>
            Reject
          </Button>
        </div>
      </div>
    </li>
  );
}

function ReviewModal({
  event,
  resolution,
  rationale,
  setRationale,
  onCancel,
  onSubmit,
  submitting,
  error,
}: {
  event: AdvisorEvent;
  resolution: Resolution;
  rationale: string;
  setRationale: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50">
      <div className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl">
        <h2 className="mb-2 text-lg font-semibold">
          {resolution === "approve" ? "Approve" : "Reject"} this event
        </h2>
        <p className="mb-4 text-sm text-slate-600">
          {event.toulmin?.claim ?? event.rationale ?? event.event_type}
        </p>
        {event.event_type === "unverified_candidate" && event.candidate_id && (
          <div className="mb-4">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
              Blast radius
            </div>
            <BlastRadiusCanvas
              candidateId={event.candidate_id}
              candidateLabel={event.candidate_id}
              category={event.category ?? "unknown"}
              affected={[]}
            />
          </div>
        )}
        <label className="mb-4 block text-sm text-slate-700">
          Rationale (optional)
          <textarea
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="One-line analyst note for the audit trail"
          />
        </label>
        {error && (
          <div className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant={resolution === "approve" ? "default" : "destructive"}
            onClick={onSubmit}
            disabled={submitting}
          >
            {submitting
              ? "Submitting…"
              : resolution === "approve"
                ? "Approve"
                : "Reject"}
          </Button>
        </div>
      </div>
    </div>
  );
}
