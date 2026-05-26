/** Phase 25 W6 — Settings / Batch Onboard
 *
 * Admin-only page at /settings/onboard/batch. Gated by `manage_drip_campaigns`.
 *
 * Purpose: upload a HubSpot deals CSV → parse → preview the 17 eligible
 * customer rows → review per-row disambiguation flags → commit (enqueues
 * every row through the existing onboarding worker pipeline).
 *
 * The flow is intentionally TWO-STEP (preview → commit) because:
 *   1. Preview surfaces the auto-resolvable vs needs-review counts so
 *      the operator can spot a mis-mapped row before queuing 17 jobs
 *   2. Many companies in the CSV are private (MAHLE, Sajjan, Tata
 *      AutoComp) and the disambiguator returns PRIVATE: placeholder
 *      tickers — operator should see these BEFORE the worker tries to
 *      yfinance-resolve them
 *
 * Backend: api/routes/batch_onboard.py — POST /api/admin/onboard/batch[/preview]
 * Single-company onboard flow at /settings/onboard remains unchanged.
 */

import { useMemo, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { admin as adminApi, type BatchOnboardPreviewResponse, type BatchOnboardCommitResponse } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";

export default function SettingsBatchOnboardPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  if (!hasPermission("manage_drip_campaigns")) {
    return <Navigate to="/now" replace />;
  }
  return <SettingsBatchOnboardInner />;
}

function SettingsBatchOnboardInner() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BatchOnboardPreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<BatchOnboardCommitResponse | null>(null);
  const [skipExisting, setSkipExisting] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const previewMutation = useMutation({
    mutationFn: (f: File) => adminApi.batchOnboardPreview(f),
    onSuccess: (data) => {
      setPreview(data);
      setCommitResult(null);
    },
  });

  const commitMutation = useMutation({
    mutationFn: (f: File) => adminApi.batchOnboardCommit(f, skipExisting),
    onSuccess: (data) => {
      setCommitResult(data);
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setPreview(null);
    setCommitResult(null);
  };

  const handlePreview = () => {
    if (!file) return;
    previewMutation.mutate(file);
  };

  const handleCommit = () => {
    if (!file) return;
    if (!confirm(
      `Enqueue ${preview?.total_eligible ?? "?"} customer onboarding jobs? ` +
      `This kicks off ticker resolution + financial fetch + 10-article ESG pipeline ` +
      `for each. Estimated wall-clock: ~30 min per customer.`
    )) return;
    commitMutation.mutate(file);
  };

  const handleReset = () => {
    setFile(null);
    setPreview(null);
    setCommitResult(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <div className="mx-auto max-w-[960px] px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">
          Batch onboard customers (HubSpot CSV)
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Upload a HubSpot deals export. The platform filters to{" "}
          <span className="font-mono text-xs">Active Status = "Active" AND Deal Stage ∈ &#123;Won, Negotiation&#125;</span>{" "}
          and shows a preview before committing.
          Each row enqueues a full onboarding pipeline (ticker resolution + financials + 10 ESG articles).
        </p>
      </header>

      {/* Step 1 — file upload */}
      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Step 1 · Upload CSV
        </h2>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={handleFileChange}
          className="block w-full text-sm text-slate-700 file:mr-3 file:rounded file:border file:border-slate-300 file:bg-slate-50 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-slate-700 hover:file:bg-slate-100"
        />
        {file && (
          <div className="mt-2 text-xs text-slate-600">
            Selected: <span className="font-mono">{file.name}</span> ({(file.size / 1024).toFixed(1)} KB)
          </div>
        )}
        <div className="mt-4 flex items-center gap-2">
          <Button
            onClick={handlePreview}
            disabled={!file || previewMutation.isPending}
            className="bg-slate-900 px-4 py-1.5 text-xs text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {previewMutation.isPending ? <><Spinner /> Parsing…</> : "Parse + preview"}
          </Button>
          {(preview || commitResult) && (
            <Button
              onClick={handleReset}
              className="bg-slate-200 px-3 py-1.5 text-xs text-slate-800 hover:bg-slate-300"
            >
              Reset
            </Button>
          )}
        </div>
        {previewMutation.isError && (
          <div className="mt-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800">
            Preview failed: {String(previewMutation.error)}
          </div>
        )}
      </section>

      {/* Step 2 — preview */}
      {preview && !commitResult && (
        <RosterPreview
          preview={preview}
          skipExisting={skipExisting}
          setSkipExisting={setSkipExisting}
          onCommit={handleCommit}
          committing={commitMutation.isPending}
          commitError={commitMutation.isError ? String(commitMutation.error) : null}
        />
      )}

      {/* Step 3 — commit result */}
      {commitResult && (
        <CommitResult result={commitResult} />
      )}
    </div>
  );
}

function RosterPreview(props: {
  preview: BatchOnboardPreviewResponse;
  skipExisting: boolean;
  setSkipExisting: (v: boolean) => void;
  onCommit: () => void;
  committing: boolean;
  commitError: string | null;
}) {
  const p = props.preview;
  const grouped = useMemo(() => {
    const won = p.roster.filter((r) => r.deal_stage === "Won");
    const negotiation = p.roster.filter((r) => r.deal_stage === "Negotiation");
    return { won, negotiation };
  }, [p.roster]);

  return (
    <section className="mb-6 rounded-lg border border-slate-200 bg-white p-5">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Step 2 · Review eligible roster
        </h2>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-700">
          {p.total_eligible} customers
        </span>
      </div>

      {/* Summary stats */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Won customers" value={p.won_count} tone="emerald" />
        <Stat label="In Negotiation" value={p.negotiation_count} tone="orange" />
        <Stat label="Auto-resolvable" value={p.auto_resolvable} tone="slate" />
        <Stat label="Need review" value={p.needs_review} tone={p.needs_review > 0 ? "amber" : "slate"} />
      </div>

      {/* Country breakdown */}
      {p.countries.length > 0 && (
        <div className="mb-4 text-xs text-slate-600">
          Countries: {p.countries.join(" · ")}
        </div>
      )}

      {/* Won group */}
      {grouped.won.length > 0 && (
        <details open className="mb-3">
          <summary className="cursor-pointer text-sm font-semibold text-emerald-800">
            Won customers ({grouped.won.length})
          </summary>
          <div className="mt-2 space-y-1.5">
            {grouped.won.map((r) => <RosterRow key={r.record_id} row={r} />)}
          </div>
        </details>
      )}

      {/* Negotiation group */}
      {grouped.negotiation.length > 0 && (
        <details open className="mb-3">
          <summary className="cursor-pointer text-sm font-semibold text-orange-800">
            In Negotiation ({grouped.negotiation.length})
          </summary>
          <div className="mt-2 space-y-1.5">
            {grouped.negotiation.map((r) => <RosterRow key={r.record_id} row={r} />)}
          </div>
        </details>
      )}

      {/* Commit controls */}
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-slate-200 pt-4">
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input
            type="checkbox"
            checked={props.skipExisting}
            onChange={(e) => props.setSkipExisting(e.target.checked)}
          />
          Skip slugs already onboarded
        </label>
        <Button
          onClick={props.onCommit}
          disabled={props.committing}
          className="bg-emerald-600 px-4 py-1.5 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {props.committing ? <><Spinner /> Enqueueing…</> : `Commit + enqueue ${p.total_eligible} jobs`}
        </Button>
      </div>
      {props.commitError && (
        <div className="mt-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800">
          Commit failed: {props.commitError}
        </div>
      )}
    </section>
  );
}

function RosterRow(props: { row: BatchOnboardPreviewResponse["roster"][number] }) {
  const r = props.row;
  return (
    <div
      className={`rounded border ${
        r.needs_disambiguation
          ? "border-amber-300 bg-amber-50"
          : "border-slate-200 bg-white"
      } px-3 py-2 text-xs`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-semibold text-slate-900">{r.company_name}</span>
        <code className="text-[10px] text-slate-500">{r.slug}</code>
      </div>
      <div className="mt-0.5 text-[11px] text-slate-600">
        {r.region} · {r.headquarter_country}
        {r.amount_inr !== null && ` · ₹${(r.amount_inr / 100000).toFixed(1)} L`}
        {r.deal_owner && ` · ${r.deal_owner}`}
      </div>
      {r.needs_disambiguation && r.disambiguation_candidates.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[10px] font-semibold text-amber-800">
            ⚠ Needs review · {r.disambiguation_candidates.length} candidate{r.disambiguation_candidates.length > 1 ? "s" : ""}
          </summary>
          <div className="mt-1 space-y-0.5 text-[10px]">
            {r.disambiguation_candidates.map((c, i) => (
              <div key={i} className="text-slate-700">
                <code>{c.ticker}</code> · {c.display_name} · {c.industry_hint}
                {c.is_private && <span className="ml-1 text-slate-500">(private)</span>}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function CommitResult(props: { result: BatchOnboardCommitResponse }) {
  const r = props.result;
  return (
    <section className="rounded-lg border border-emerald-300 bg-emerald-50 p-5">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-emerald-800">
        Step 3 · Commit complete
      </h2>
      <p className="mb-3 text-sm text-slate-700">
        <strong>{r.enqueued_job_ids.length}</strong> jobs enqueued.{" "}
        {r.skipped_already_existing.length > 0 && (
          <>
            <strong>{r.skipped_already_existing.length}</strong> skipped (slug already existed):{" "}
            <code className="text-xs">{r.skipped_already_existing.join(", ")}</code>.{" "}
          </>
        )}
        Worker is now processing them in the background. Check{" "}
        <a href="/home" className="text-emerald-700 underline">/home</a>{" "}
        in 20-30 minutes for the first wave of intelligence.
      </p>
      <div className="text-xs text-slate-600">
        Job IDs: <code>{r.enqueued_job_ids.join(", ")}</code>
      </div>
    </section>
  );
}

function Stat(props: {
  label: string;
  value: number;
  tone: "emerald" | "orange" | "slate" | "amber";
}) {
  const toneClasses = {
    emerald: "bg-emerald-50 text-emerald-800 border-emerald-200",
    orange: "bg-orange-50 text-orange-800 border-orange-200",
    slate: "bg-slate-50 text-slate-700 border-slate-200",
    amber: "bg-amber-50 text-amber-800 border-amber-200",
  }[props.tone];
  return (
    <div className={`rounded border ${toneClasses} px-3 py-2 text-center`}>
      <div className="text-xl font-bold">{props.value}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide opacity-80">
        {props.label}
      </div>
    </div>
  );
}
