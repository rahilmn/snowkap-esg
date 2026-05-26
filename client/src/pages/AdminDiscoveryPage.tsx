/** Phase 24 (W2) — Admin discovery review surface
 *
 * Closes the Phase 19 self-evolving ontology loop. Tier-1 candidates
 * (entities + frameworks meeting confidence + article-count thresholds)
 * auto-promote inside the engine. Everything else lands in
 * `data/ontology/discovery_staging.json` and used to sit there forever
 * with no review path. This page gives the admin three actions per
 * staged candidate:
 *
 *   • Promote — insert triples into `data/ontology/discovered.ttl`
 *   • Reject  — mark dismissed, never re-stage
 *   • Defer   — review later (status = `deferred`, stays in buffer)
 *
 * Reject + defer REQUIRE a Toulmin justification (claim + grounds +
 * warrant). Promote does not (the candidate's confidence + article
 * count is the implicit warrant). Every decision writes to
 * `data/audit/promotion_log.jsonl` via engine.audit.append_promotion.
 *
 * Auth-gated: `manage_drip_campaigns` permission required (super admin).
 * Non-admin tokens see a redirect to /home.
 *
 * Backend: api/routes/discovery.py
 *   GET  /api/admin/discovery/staged
 *   POST /api/admin/discovery/decide
 *   GET  /api/admin/discovery/history
 */

import { useMemo, useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { admin as adminApi } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

type Decision = "promote" | "reject" | "defer";

const CATEGORY_LABELS: Record<string, string> = {
  entity: "Entities",
  framework: "Frameworks",
  event: "Event types",
  theme: "Themes",
  edge: "Causal edges",
  weight: "Materiality weights",
  stakeholder: "Stakeholders",
};

export default function AdminDiscoveryPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  if (!hasPermission("manage_drip_campaigns")) {
    return <Navigate to="/now" replace />;
  }
  return <AdminDiscoveryInner />;
}

function AdminDiscoveryInner() {
  const queryClient = useQueryClient();
  const [activeCategory, setActiveCategory] = useState<string | undefined>(undefined);
  const [reviewing, setReviewing] = useState<{
    candidate_id: string;
    label: string;
    decision: Decision;
  } | null>(null);

  const stagedQuery = useQuery({
    queryKey: ["admin-discovery-staged", activeCategory],
    queryFn: () => adminApi.discoveryStaged(activeCategory, 100),
  });

  const historyQuery = useQuery({
    queryKey: ["admin-discovery-history"],
    queryFn: () => adminApi.discoveryHistory(20),
  });

  const decideMutation = useMutation({
    mutationFn: (req: Parameters<typeof adminApi.discoveryDecide>[0]) =>
      adminApi.discoveryDecide(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-discovery-staged"] });
      queryClient.invalidateQueries({ queryKey: ["admin-discovery-history"] });
      setReviewing(null);
    },
  });

  const candidates = stagedQuery.data?.candidates ?? [];
  const byCategory = stagedQuery.data?.by_category ?? {};
  const total = stagedQuery.data?.count ?? 0;

  return (
    <div className="mx-auto max-w-[960px] px-6 py-8">
      <header className="mb-6 flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            Discovery review queue
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Self-evolving ontology candidates awaiting human approval. Every decision
            is logged to <code className="rounded bg-slate-100 px-1 text-[12px]">data/audit/promotion_log.jsonl</code>.
          </p>
        </div>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
          {total} pending
        </span>
      </header>

      {/* Category tabs */}
      <div className="mb-4 flex flex-wrap gap-2">
        <CategoryTab
          label="All"
          count={Object.values(byCategory).reduce((a, b) => a + b, 0)}
          active={activeCategory === undefined}
          onClick={() => setActiveCategory(undefined)}
        />
        {Object.entries(CATEGORY_LABELS).map(([cat, label]) => (
          <CategoryTab
            key={cat}
            label={label}
            count={byCategory[cat] ?? 0}
            active={activeCategory === cat}
            onClick={() => setActiveCategory(cat)}
          />
        ))}
      </div>

      {/* Staged candidates */}
      {stagedQuery.isLoading && (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      )}

      {stagedQuery.isError && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800">
          Failed to load staged candidates. Check API logs.
        </div>
      )}

      {!stagedQuery.isLoading && candidates.length === 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-600">
          No pending candidates in this category. The promoter runs every 30 minutes.
        </div>
      )}

      <div className="space-y-3">
        {candidates.map((c) => (
          <CandidateRow
            key={c.candidate_id}
            candidate={c}
            onDecide={(decision) =>
              setReviewing({ candidate_id: c.candidate_id, label: c.label, decision })
            }
          />
        ))}
      </div>

      {/* Recent decisions */}
      <section className="mt-12">
        <h2 className="mb-3 text-lg font-semibold text-slate-900">
          Recent decisions
        </h2>
        {historyQuery.data && historyQuery.data.entries.length > 0 ? (
          <div className="space-y-2">
            {historyQuery.data.entries.map((e) => (
              <HistoryRow key={e.ts + e.candidate_id} entry={e} />
            ))}
          </div>
        ) : (
          <div className="text-sm text-slate-500">No decisions logged yet.</div>
        )}
      </section>

      {/* Toulmin modal */}
      {reviewing && (
        <DecideModal
          reviewing={reviewing}
          onCancel={() => setReviewing(null)}
          onSubmit={(toulmin) =>
            decideMutation.mutate({
              candidate_id: reviewing.candidate_id,
              decision: reviewing.decision,
              toulmin,
            })
          }
          isSubmitting={decideMutation.isPending}
          error={decideMutation.isError ? String(decideMutation.error) : null}
        />
      )}
    </div>
  );
}

function CategoryTab(props: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
        props.active
          ? "bg-slate-900 text-white"
          : "bg-slate-100 text-slate-700 hover:bg-slate-200"
      }`}
    >
      {props.label}{" "}
      <span className={`ml-1 ${props.active ? "text-slate-300" : "text-slate-500"}`}>
        {props.count}
      </span>
    </button>
  );
}

function CandidateRow(props: {
  candidate: {
    candidate_id: string;
    category: string;
    label: string;
    confidence: number;
    article_ids: string[];
    sources: string[];
    companies: string[];
    last_seen: string;
    data: Record<string, unknown>;
  };
  onDecide: (decision: Decision) => void;
}) {
  const c = props.candidate;
  const articleCount = c.article_ids.length;
  const sourceCount = new Set(c.sources).size;
  const conf = (c.confidence * 100).toFixed(0);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-2">
            <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
              {c.category}
            </span>
            <span className="text-[11px] text-slate-500">
              confidence {conf}% · {articleCount} articles · {sourceCount} sources
            </span>
          </div>
          <div className="text-sm font-semibold text-slate-900">{c.label}</div>
          <div className="mt-1 text-[11px] text-slate-500">
            {c.companies.length > 0 && <>Companies: {c.companies.join(", ")} · </>}
            Last seen: {c.last_seen ? c.last_seen.slice(0, 10) : "—"}
          </div>
          {Object.keys(c.data ?? {}).length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-slate-500 hover:text-slate-700">
                Inspect category-specific data
              </summary>
              <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-[10px] text-slate-700">
                {JSON.stringify(c.data, null, 2)}
              </pre>
            </details>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1.5">
          <Button
            onClick={() => props.onDecide("promote")}
            className="bg-emerald-600 px-3 py-1 text-xs hover:bg-emerald-700"
          >
            Promote
          </Button>
          <Button
            onClick={() => props.onDecide("defer")}
            className="bg-slate-200 px-3 py-1 text-xs text-slate-800 hover:bg-slate-300"
          >
            Defer
          </Button>
          <Button
            onClick={() => props.onDecide("reject")}
            className="bg-red-100 px-3 py-1 text-xs text-red-800 hover:bg-red-200"
          >
            Reject
          </Button>
        </div>
      </div>
    </div>
  );
}

function HistoryRow(props: {
  entry: {
    ts: string;
    decision: Decision;
    candidate_id: string;
    category: string;
    candidate_payload: Record<string, unknown>;
    toulmin?: { claim: string };
    user_id?: string;
  };
}) {
  const e = props.entry;
  const tint =
    e.decision === "promote"
      ? "bg-emerald-50 text-emerald-800 border-emerald-200"
      : e.decision === "reject"
      ? "bg-red-50 text-red-800 border-red-200"
      : "bg-slate-50 text-slate-700 border-slate-200";
  const label = (e.candidate_payload?.label as string) ?? e.candidate_id;
  return (
    <div className={`rounded border ${tint} px-3 py-2 text-xs`}>
      <div className="flex items-baseline justify-between">
        <span>
          <span className="font-semibold uppercase tracking-wide">{e.decision}</span>
          {" · "}
          {e.category} · {label}
        </span>
        <span className="text-[10px] opacity-70">
          {e.ts.slice(0, 16).replace("T", " ")}
        </span>
      </div>
      {e.toulmin?.claim && (
        <div className="mt-0.5 text-[11px] opacity-80">
          {e.toulmin.claim}
        </div>
      )}
      {e.user_id && (
        <div className="mt-0.5 text-[10px] opacity-60">by {e.user_id}</div>
      )}
    </div>
  );
}

function DecideModal(props: {
  reviewing: { candidate_id: string; label: string; decision: Decision };
  onCancel: () => void;
  onSubmit: (toulmin: {
    claim: string;
    grounds: string[];
    warrant: string;
    qualifier?: string;
    rebuttal?: string;
  } | undefined) => void;
  isSubmitting: boolean;
  error: string | null;
}) {
  const { decision, label, candidate_id } = props.reviewing;
  const toulminRequired = decision !== "promote";
  const [claim, setClaim] = useState(
    decision === "promote"
      ? `Promote: ${label} meets the confidence + article-count threshold.`
      : decision === "reject"
      ? `Reject: ${label} should not enter the ontology.`
      : `Defer: ${label} needs more evidence before deciding.`
  );
  const [groundsRaw, setGroundsRaw] = useState("");
  const [warrant, setWarrant] = useState("");
  const [qualifier, setQualifier] = useState("");
  const [rebuttal, setRebuttal] = useState("");

  const grounds = useMemo(
    () =>
      groundsRaw
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    [groundsRaw]
  );

  const canSubmit = useMemo(() => {
    if (!toulminRequired) return true;
    return !!claim.trim() && grounds.length >= 1 && !!warrant.trim();
  }, [toulminRequired, claim, grounds, warrant]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={props.onCancel}
    >
      <div
        className="w-full max-w-[560px] rounded-xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold text-slate-900">
              {decision === "promote" && "Promote candidate"}
              {decision === "reject" && "Reject candidate"}
              {decision === "defer" && "Defer candidate"}
            </h2>
            <code className="text-[10px] text-slate-500">{candidate_id}</code>
          </div>
          <div className="mt-1 text-sm text-slate-600">{label}</div>
        </div>

        <div className="space-y-3 px-5 py-4 text-sm">
          <Field label="Claim" required>
            <textarea
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
              rows={2}
              className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
          </Field>

          <Field
            label={`Grounds (one per line, ≥1${toulminRequired ? " required" : ""})`}
            required={toulminRequired}
          >
            <textarea
              value={groundsRaw}
              onChange={(e) => setGroundsRaw(e.target.value)}
              rows={4}
              placeholder={"e.g.\n- only 2 articles seen\n- single source\n- entity name ambiguous"}
              className="w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-xs"
            />
            <div className="mt-1 text-[10px] text-slate-500">
              Parsed into {grounds.length} {grounds.length === 1 ? "ground" : "grounds"}.
            </div>
          </Field>

          <Field label="Warrant (rule cited)" required={toulminRequired}>
            <input
              type="text"
              value={warrant}
              onChange={(e) => setWarrant(e.target.value)}
              placeholder="e.g. Snowkap entity admissibility policy / Phase 19 Tier-1 rule"
              className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
          </Field>

          <Field label="Qualifier (optional)">
            <input
              type="text"
              value={qualifier}
              onChange={(e) => setQualifier(e.target.value)}
              placeholder="e.g. confidence 0.85"
              className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
          </Field>

          <Field label="Rebuttal — what would flip this decision (optional)">
            <input
              type="text"
              value={rebuttal}
              onChange={(e) => setRebuttal(e.target.value)}
              placeholder="e.g. if 2+ sources surface within 14 days, re-review"
              className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
          </Field>

          {props.error && (
            <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800">
              {props.error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-200 px-5 py-3">
          <Button
            onClick={props.onCancel}
            className="bg-slate-200 px-3 py-1.5 text-xs text-slate-800 hover:bg-slate-300"
          >
            Cancel
          </Button>
          <Button
            disabled={!canSubmit || props.isSubmitting}
            onClick={() => {
              const toulmin = {
                claim: claim.trim(),
                grounds,
                warrant: warrant.trim() || "n/a",
                qualifier: qualifier.trim() || undefined,
                rebuttal: rebuttal.trim() || undefined,
              };
              // Promote with empty justification ⇒ omit toulmin entirely
              props.onSubmit(
                !toulminRequired && !groundsRaw.trim() && !warrant.trim()
                  ? undefined
                  : toulmin
              );
            }}
            className={`px-3 py-1.5 text-xs ${
              decision === "promote"
                ? "bg-emerald-600 hover:bg-emerald-700"
                : decision === "reject"
                ? "bg-red-600 hover:bg-red-700"
                : "bg-slate-700 hover:bg-slate-800"
            }`}
          >
            {props.isSubmitting ? "Saving…" : `Confirm ${decision}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field(props: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {props.label}
        {props.required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {props.children}
    </div>
  );
}
