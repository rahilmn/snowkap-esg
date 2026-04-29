/** Phase 10 — Settings / Campaigns
 *
 * Admin-only page at /settings/campaigns. Gated by manage_drip_campaigns.
 *
 * Four tabs:
 *   Active    — campaigns currently on schedule
 *   Paused    — temporarily disabled, not firing
 *   Archived  — soft-deleted (keep send history but hide from rotation)
 *   Send history — merged send_log across all visible campaigns
 *
 * Row actions: Edit · Pause/Resume · Send now · Archive · Delete.
 *
 * Everything here writes via api.campaigns.* and cascades to the SQLite store
 * that Phase B set up. "Send now" queues a background task in the FastAPI
 * BackgroundTasks pool that calls campaign_runner.run_due_campaigns(force=True).
 */

import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  campaigns as campaignsApi,
  type Campaign,
  type CampaignStatus,
  type SendLogEntry,
} from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { CampaignFormDialog } from "@/components/campaigns/CampaignFormDialog";

type Tab = "active" | "paused" | "archived" | "history";

const DAY_NAMES = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"];

function formatCadence(c: Campaign): string {
  const time = c.send_time_utc ? `${c.send_time_utc} UTC` : "";
  if (c.cadence === "weekly" && c.day_of_week !== null) {
    return `Weekly · ${DAY_NAMES[c.day_of_week]} ${time}`.trim();
  }
  if (c.cadence === "monthly" && c.day_of_month !== null) {
    return `Monthly · Day ${c.day_of_month} ${time}`.trim();
  }
  if (c.cadence === "once") {
    return `Once · ${time}`.trim();
  }
  return c.cadence;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function SettingsCampaignsPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canManage = hasPermission("manage_drip_campaigns");

  if (!canManage) {
    return <Navigate to="/home" replace />;
  }

  return <SettingsCampaignsPageInner />;
}

function SettingsCampaignsPageInner() {
  const [tab, setTab] = useState<Tab>("active");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Campaign | null>(null);
  const [logCampaignId, setLogCampaignId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const status: CampaignStatus | undefined =
    tab === "active" ? "active" : tab === "paused" ? "paused" : tab === "archived" ? "archived" : undefined;

  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["campaigns", tab === "history" ? "all" : status],
    queryFn: () => campaignsApi.list(status),
    staleTime: 15_000,
  });

  const campaignList = data?.campaigns ?? [];

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (c: Campaign) => {
    setEditing(c);
    setDialogOpen(true);
  };

  const flashToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
  };

  const handleAction = async (action: () => Promise<unknown>, successMsg: string) => {
    try {
      await action();
      flashToast(successMsg);
      invalidate();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Action failed";
      flashToast(`Error: ${msg}`);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="flex items-start justify-between mb-5">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Drip campaigns</h1>
          <p className="text-sm text-gray-500 mt-1">
            Schedule or manually fire HTML briefs to prospects. Uses the Phase 9 render surface —
            every ₹ figure source-tagged, every framework citation section-coded.
          </p>
        </div>
        <Button size="sm" onClick={openCreate}>
          <span className="mr-1">+</span> New campaign
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-gray-200 mb-4">
        {([
          ["active", "Active"],
          ["paused", "Paused"],
          ["archived", "Archived"],
          ["history", "Send history"],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === key
                ? "border-orange-500 text-gray-900"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Toast */}
      {toast && (
        <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {toast}
        </div>
      )}

      {/* Content */}
      {isLoading && (
        <div className="flex items-center justify-center py-12 text-gray-500">
          <Spinner /> <span className="ml-2">Loading campaigns…</span>
        </div>
      )}

      {isError && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load campaigns: {error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {tab === "history" ? (
        <SendHistoryView campaigns={campaignList} />
      ) : !isLoading && campaignList.length === 0 ? (
        <EmptyState tab={tab} onCreate={openCreate} />
      ) : (
        <div className="overflow-x-auto rounded-md border border-gray-200">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left px-3 py-2 font-semibold">Name</th>
                <th className="text-left px-3 py-2 font-semibold">Cadence</th>
                <th className="text-left px-3 py-2 font-semibold">Company</th>
                <th className="text-right px-3 py-2 font-semibold">Recipients</th>
                <th className="text-left px-3 py-2 font-semibold">Last sent</th>
                <th className="text-left px-3 py-2 font-semibold">Next send</th>
                <th className="text-left px-3 py-2 font-semibold">Status</th>
                <th className="text-right px-3 py-2 font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {campaignList.map((c) => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="px-3 py-2 font-medium text-gray-900">{c.name}</td>
                  <td className="px-3 py-2 text-gray-600">{formatCadence(c)}</td>
                  <td className="px-3 py-2 text-gray-600">{c.target_company}</td>
                  <td className="px-3 py-2 text-right text-gray-600">{c.recipient_count ?? 0}</td>
                  <td className="px-3 py-2 text-gray-600">{formatDate(c.last_sent_at)}</td>
                  <td className="px-3 py-2 text-gray-600">{formatDate(c.next_send_at)}</td>
                  <td className="px-3 py-2">
                    <StatusPill status={c.status} />
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <RowActions
                      campaign={c}
                      onEdit={() => openEdit(c)}
                      onViewLog={() => setLogCampaignId(c.id)}
                      onAction={handleAction}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Send-log modal (cheap, inline) */}
      {logCampaignId && (
        <SendLogModal
          campaignId={logCampaignId}
          onClose={() => setLogCampaignId(null)}
        />
      )}

      <CampaignFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initial={editing}
        onSaved={() => flashToast(editing ? "Campaign updated" : "Campaign created")}
      />
    </div>
  );
}

function StatusPill({ status }: { status: CampaignStatus }) {
  const color = status === "active"
    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
    : status === "paused"
      ? "bg-amber-50 text-amber-700 border-amber-200"
      : "bg-gray-50 text-gray-600 border-gray-200";
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${color}`}>
      {status}
    </span>
  );
}

function RowActions({
  campaign,
  onEdit,
  onViewLog,
  onAction,
}: {
  campaign: Campaign;
  onEdit: () => void;
  onViewLog: () => void;
  onAction: (fn: () => Promise<unknown>, msg: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const wrap = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    await onAction(fn, msg);
    setBusy(false);
  };

  return (
    <div className="inline-flex gap-1">
      <Button variant="ghost" size="sm" onClick={onEdit} disabled={busy}>Edit</Button>
      {campaign.status === "active" ? (
        <Button variant="ghost" size="sm" disabled={busy}
          onClick={() => wrap(() => campaignsApi.pause(campaign.id), "Paused")}>
          Pause
        </Button>
      ) : campaign.status === "paused" ? (
        <Button variant="ghost" size="sm" disabled={busy}
          onClick={() => wrap(() => campaignsApi.resume(campaign.id), "Resumed")}>
          Resume
        </Button>
      ) : null}
      <Button variant="outline" size="sm" disabled={busy}
        onClick={() => wrap(() => campaignsApi.sendNow(campaign.id), `Queued send for ${campaign.recipient_count ?? 0} recipients`)}>
        Send now
      </Button>
      <Button variant="ghost" size="sm" onClick={onViewLog} disabled={busy}>Log</Button>
      {campaign.status !== "archived" && (
        <Button variant="ghost" size="sm" disabled={busy}
          onClick={() => wrap(() => campaignsApi.archive(campaign.id), "Archived")}>
          Archive
        </Button>
      )}
      <Button variant="ghost" size="sm" disabled={busy}
        onClick={() => {
          if (confirm(`Delete "${campaign.name}"? Send history will be preserved.`)) {
            wrap(() => campaignsApi.delete(campaign.id), "Deleted");
          }
        }}>
        Delete
      </Button>
    </div>
  );
}

function EmptyState({ tab, onCreate }: { tab: Tab; onCreate: () => void }) {
  const msg = tab === "active"
    ? "No active campaigns yet. Create one to start dripping briefs."
    : tab === "paused"
      ? "No paused campaigns."
      : "No archived campaigns.";
  return (
    <div className="rounded-lg border border-dashed border-gray-300 p-10 text-center">
      <div className="text-sm text-gray-500">{msg}</div>
      {tab === "active" && (
        <Button size="sm" className="mt-4" onClick={onCreate}>
          Create your first campaign
        </Button>
      )}
    </div>
  );
}

function SendHistoryView({ campaigns }: { campaigns: Campaign[] }) {
  // Cheap implementation: flatten send_log from every campaign into one list.
  // Fetches in parallel via react-query; adequate for admin-only page at V1 scale.
  const perCampaign = useQuery({
    queryKey: ["campaigns", "send-log-all", campaigns.map((c) => c.id).join(",")],
    queryFn: async () => {
      const pairs = await Promise.all(
        campaigns.map((c) =>
          campaignsApi.sendLog(c.id, 50).then((r) => ({ name: c.name, entries: r.entries })),
        ),
      );
      // Flatten + sort newest first
      const all = pairs.flatMap(({ name, entries }) => entries.map((e) => ({ ...e, _name: name })));
      all.sort((a, b) => b.sent_at.localeCompare(a.sent_at));
      return all;
    },
    staleTime: 10_000,
    enabled: campaigns.length > 0,
  });

  if (campaigns.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-10 text-center">
        <div className="text-sm text-gray-500">No campaigns yet — nothing to show.</div>
      </div>
    );
  }

  if (perCampaign.isLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-500">
        <Spinner /> <span className="ml-2">Loading send history…</span>
      </div>
    );
  }

  const entries = perCampaign.data ?? [];
  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
        No sends recorded yet. Hit "Send now" on an active campaign to see a row appear here.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-md border border-gray-200">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 text-xs uppercase text-gray-500">
          <tr>
            <th className="text-left px-3 py-2 font-semibold">When</th>
            <th className="text-left px-3 py-2 font-semibold">Campaign</th>
            <th className="text-left px-3 py-2 font-semibold">Recipient</th>
            <th className="text-left px-3 py-2 font-semibold">Article</th>
            <th className="text-left px-3 py-2 font-semibold">Subject</th>
            <th className="text-left px-3 py-2 font-semibold">Status</th>
            <th className="text-left px-3 py-2 font-semibold">Provider id</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {entries.map((e) => (
            <tr key={e.id} className="hover:bg-gray-50">
              <td className="px-3 py-2 text-gray-600 whitespace-nowrap">{formatDate(e.sent_at)}</td>
              <td className="px-3 py-2 text-gray-900 font-medium">{(e as SendLogEntry & { _name?: string })._name ?? ""}</td>
              <td className="px-3 py-2 text-gray-600">{e.recipient_email}</td>
              <td className="px-3 py-2 text-gray-500 font-mono text-xs">{e.article_id ?? "—"}</td>
              <td className="px-3 py-2 text-gray-600">{e.subject ?? "—"}</td>
              <td className="px-3 py-2"><SendStatusPill status={e.status} /></td>
              <td className="px-3 py-2 text-gray-500 font-mono text-xs">{e.provider_id ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SendStatusPill({ status }: { status: SendLogEntry["status"] }) {
  const map: Record<SendLogEntry["status"], string> = {
    sent: "bg-emerald-50 text-emerald-700 border-emerald-200",
    preview: "bg-blue-50 text-blue-700 border-blue-200",
    failed: "bg-red-50 text-red-700 border-red-200",
    skipped_stale: "bg-amber-50 text-amber-700 border-amber-200",
    skipped_dedup: "bg-gray-50 text-gray-600 border-gray-200",
  };
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${map[status]}`}>
      {status.replace("_", " ")}
    </span>
  );
}

function SendLogModal({ campaignId, onClose }: { campaignId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["campaign", campaignId, "send-log"],
    queryFn: () => campaignsApi.sendLog(campaignId, 50),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="w-[90vw] max-w-[720px] max-h-[80vh] overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Send history</h2>
          <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
        </div>
        {isLoading ? (
          <div className="flex items-center justify-center py-6 text-gray-500"><Spinner /></div>
        ) : (data?.entries ?? []).length === 0 ? (
          <div className="text-sm text-gray-500 py-6 text-center">No sends yet.</div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="text-xs uppercase text-gray-500">
              <tr>
                <th className="text-left py-2">When</th>
                <th className="text-left py-2">Recipient</th>
                <th className="text-left py-2">Status</th>
                <th className="text-left py-2">Provider</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data?.entries.map((e) => (
                <tr key={e.id}>
                  <td className="py-2 text-gray-600">{formatDate(e.sent_at)}</td>
                  <td className="py-2">{e.recipient_email}</td>
                  <td className="py-2"><SendStatusPill status={e.status} /></td>
                  <td className="py-2 text-gray-500 font-mono text-xs">{e.provider_id ?? e.error ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
