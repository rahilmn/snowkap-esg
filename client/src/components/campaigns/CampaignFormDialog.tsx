/** Phase 10 — CampaignFormDialog
 *
 * Create-or-edit a drip campaign in a Radix Dialog. Same form shape for both
 * modes; when `initial` is passed we're editing and PATCH instead of POST.
 *
 * The form exposes everything the API accepts:
 *   - Name (required, 3–60 chars)
 *   - Target company (dropdown from /api/admin/tenants — targets + onboarded)
 *   - Article selection (radio): latest HOME-tier article | specific article_id
 *   - Cadence (radio): Once / Weekly / Monthly + conditional day/time pickers
 *   - Recipients (textarea, one email per line — Phase 9 name_from_email handles
 *     the greeting, so bare emails are fine; generic mailboxes surface inline)
 *   - Optional sender note (800 char cap, same copy as ShareArticleButton)
 *   - CTA URL + label (defaulted to Snowkap contact-us)
 *
 * Save / Save & Send now — after the Save call returns, "Send now" invokes
 * the 202 endpoint on the new id.
 *
 * Preview: a button that opens an inline HTML render of what the first
 * recipient will see. Useful as a sanity check before queuing a batch.
 */

import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import {
  admin,
  campaigns as campaignsApi,
  type Campaign,
  type CampaignCadence,
  type CampaignPreview,
  type ArticleSelection,
} from "@/lib/api";

interface CampaignFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pass a campaign to edit it; omit to create a new one. */
  initial?: Campaign | null;
  /** Called after a successful save (create or update). */
  onSaved?: (c: Campaign) => void;
}

interface FormState {
  name: string;
  target_company: string;
  article_selection: ArticleSelection;
  article_id: string;
  cadence: CampaignCadence;
  day_of_week: number;
  day_of_month: number;
  send_time_utc: string;
  cta_url: string;
  cta_label: string;
  sender_note: string;
  recipients_raw: string; // textarea — one email per line
}

const EMPTY: FormState = {
  name: "",
  target_company: "",
  article_selection: "latest_home",
  article_id: "",
  cadence: "weekly",
  day_of_week: 0,
  day_of_month: 1,
  send_time_utc: "09:00",
  cta_url: "https://snowkap.com/contact-us/",
  cta_label: "Book a demo with Snowkap",
  sender_note: "",
  recipients_raw: "",
};

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function CampaignFormDialog({ open, onOpenChange, initial, onSaved }: CampaignFormDialogProps) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recipientError, setRecipientError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<CampaignPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Load tenant list for the company dropdown (admin-only endpoint, fine here)
  const { data: tenants } = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: () => admin.tenants(),
    enabled: open,
    staleTime: 60_000,
  });

  // Rehydrate form when opening (for edit mode) or reset (create mode)
  useEffect(() => {
    if (!open) return;
    if (initial) {
      setForm({
        name: initial.name,
        target_company: initial.target_company,
        article_selection: initial.article_selection,
        article_id: initial.article_id ?? "",
        cadence: initial.cadence,
        day_of_week: initial.day_of_week ?? 0,
        day_of_month: initial.day_of_month ?? 1,
        send_time_utc: initial.send_time_utc ?? "09:00",
        cta_url: initial.cta_url ?? "https://snowkap.com/contact-us/",
        cta_label: initial.cta_label ?? "Book a demo with Snowkap",
        sender_note: initial.sender_note ?? "",
        recipients_raw: "",
      });
    } else {
      setForm(EMPTY);
    }
    setError(null);
    setRecipientError(null);
  }, [open, initial]);

  // Auto-pick the first tenant as the default company if none chosen yet
  useEffect(() => {
    if (!open || form.target_company) return;
    const first = tenants?.[0];
    if (first) {
      setForm((f) => ({ ...f, target_company: first.slug }));
    }
  }, [open, form.target_company, tenants]);

  const parsedRecipients = useMemo(() => {
    return form.recipients_raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }, [form.recipients_raw]);

  const isEdit = !!initial;

  const validate = (): string | null => {
    if (form.name.trim().length < 3) return "Name must be at least 3 characters.";
    if (!form.target_company) return "Pick a target company.";
    if (form.article_selection === "specific" && !form.article_id.trim()) {
      return "Article ID is required for 'specific article' selection.";
    }
    if (form.cadence === "weekly" && (form.day_of_week < 0 || form.day_of_week > 6)) {
      return "Pick a day of the week.";
    }
    if (form.cadence === "monthly" && (form.day_of_month < 1 || form.day_of_month > 28)) {
      return "Day of month must be 1–28 (29–31 not supported).";
    }
    if (!isEdit && parsedRecipients.length === 0) {
      return "Add at least one recipient (one email per line).";
    }
    // Rough email sanity — API does fuller validation
    const bad = parsedRecipients.find((e) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e));
    if (bad) return `"${bad}" doesn't look like a valid email.`;
    return null;
  };

  const handleSave = async (andSendNow: boolean) => {
    setError(null);
    const v = validate();
    if (v) {
      setError(v);
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        target_company: form.target_company,
        article_selection: form.article_selection,
        article_id: form.article_selection === "specific" ? form.article_id.trim() : null,
        cadence: form.cadence,
        day_of_week: form.cadence === "weekly" ? form.day_of_week : null,
        day_of_month: form.cadence === "monthly" ? form.day_of_month : null,
        send_time_utc: form.send_time_utc,
        cta_url: form.cta_url,
        cta_label: form.cta_label,
        sender_note: form.sender_note || null,
      };

      let saved: Campaign;
      if (isEdit && initial) {
        saved = await campaignsApi.patch(initial.id, payload);
        if (parsedRecipients.length > 0) {
          await campaignsApi.replaceRecipients(
            saved.id,
            parsedRecipients.map((email) => ({ email })),
          );
        }
      } else {
        saved = await campaignsApi.create({
          ...payload,
          recipients: parsedRecipients.map((email) => ({ email })),
        });
      }

      if (andSendNow) {
        await campaignsApi.sendNow(saved.id);
      }

      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      queryClient.invalidateQueries({ queryKey: ["campaign", saved.id] });
      onSaved?.(saved);
      onOpenChange(false);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-[50%] top-[50%] z-50 w-[92vw] max-w-[640px] max-h-[92vh] overflow-y-auto translate-x-[-50%] translate-y-[-50%] rounded-lg border border-gray-200 bg-white p-6 shadow-lg focus:outline-none"
        >
          <Dialog.Title className="text-lg font-semibold text-gray-900">
            {isEdit ? "Edit campaign" : "New campaign"}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-gray-500">
            Schedule a recurring email or fire one off now. Uses the exact same
            HTML Phase 9's Share button produces — all ₹ figures source-tagged.
          </Dialog.Description>

          <div className="mt-5 space-y-4">
            {/* Name */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">Name</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Weekly Tata Power digest"
                maxLength={60}
              />
            </div>

            {/* Target company */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">Target company</label>
              <select
                className="w-full h-9 rounded-md border border-gray-200 bg-transparent px-3 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-gray-400"
                value={form.target_company}
                onChange={(e) => setForm({ ...form, target_company: e.target.value })}
              >
                <option value="">— Select —</option>
                {tenants?.map((t) => (
                  <option key={t.slug} value={t.slug}>
                    {t.name}
                    {t.source === "onboarded" ? " · prospect" : ""}
                    {t.article_count ? ` · ${t.article_count} articles` : ""}
                  </option>
                ))}
              </select>
            </div>

            {/* Article selection */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">Which article?</label>
              <div className="flex flex-col gap-2">
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    checked={form.article_selection === "latest_home"}
                    onChange={() => setForm({ ...form, article_selection: "latest_home" })}
                  />
                  Latest HOME-tier article (most recent high-materiality signal)
                </label>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    checked={form.article_selection === "specific"}
                    onChange={() => setForm({ ...form, article_selection: "specific" })}
                  />
                  Specific article ID
                </label>
                {form.article_selection === "specific" && (
                  <Input
                    className="mt-1"
                    placeholder="article_id from detail URL"
                    value={form.article_id}
                    onChange={(e) => setForm({ ...form, article_id: e.target.value })}
                  />
                )}
              </div>
            </div>

            {/* Cadence */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">Cadence</label>
              <div className="flex gap-2 mb-2">
                {(["once", "weekly", "monthly"] as CampaignCadence[]).map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setForm({ ...form, cadence: c })}
                    className={`rounded-md border px-3 py-1.5 text-xs font-medium capitalize transition ${
                      form.cadence === c
                        ? "border-orange-500 bg-orange-500 text-white"
                        : "border-gray-200 bg-white text-gray-700 hover:border-gray-400"
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
              <div className="flex gap-3 items-end">
                {form.cadence === "weekly" && (
                  <div className="flex-1">
                    <label className="block text-[11px] text-gray-500 mb-1">Day of week</label>
                    <select
                      className="w-full h-9 rounded-md border border-gray-200 bg-transparent px-3 text-sm"
                      value={form.day_of_week}
                      onChange={(e) => setForm({ ...form, day_of_week: Number(e.target.value) })}
                    >
                      {DAY_NAMES.map((name, i) => (
                        <option key={name} value={i}>{name}</option>
                      ))}
                    </select>
                  </div>
                )}
                {form.cadence === "monthly" && (
                  <div className="flex-1">
                    <label className="block text-[11px] text-gray-500 mb-1">Day of month (1–28)</label>
                    <Input
                      type="number"
                      min={1}
                      max={28}
                      value={form.day_of_month}
                      onChange={(e) => setForm({ ...form, day_of_month: Number(e.target.value) })}
                    />
                  </div>
                )}
                <div className="flex-1">
                  <label className="block text-[11px] text-gray-500 mb-1">Time UTC</label>
                  <Input
                    value={form.send_time_utc}
                    onChange={(e) => setForm({ ...form, send_time_utc: e.target.value })}
                    placeholder="09:00"
                  />
                </div>
              </div>
              {form.cadence === "monthly" && (
                <div className="mt-1 text-[11px] text-gray-500">
                  29–31 not supported — stops working in February.
                </div>
              )}
            </div>

            {/* Recipients */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">
                Recipients — one email per line
                {parsedRecipients.length > 0 && (
                  <span className="ml-2 text-gray-500 font-normal">
                    ({parsedRecipients.length} found)
                  </span>
                )}
              </label>
              <textarea
                className="w-full h-28 rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-gray-400 font-mono"
                placeholder={"ambalika.mehrotra@mintedit.com\nceo@et.com\n..."}
                value={form.recipients_raw}
                onChange={(e) => setForm({ ...form, recipients_raw: e.target.value })}
              />
              {isEdit && parsedRecipients.length === 0 && (
                <div className="text-[11px] text-gray-500 mt-1">
                  Leave blank to keep existing recipient list unchanged.
                </div>
              )}
              {recipientError && <div className="text-xs text-red-600 mt-1">{recipientError}</div>}
            </div>

            {/* Sender note */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1.5">
                Sender note (optional)
              </label>
              <textarea
                className="w-full h-20 rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-gray-400"
                placeholder="Context for the recipient — why you're sending this, what to look at first…"
                value={form.sender_note}
                onChange={(e) => setForm({ ...form, sender_note: e.target.value })}
                maxLength={800}
              />
            </div>

            {/* CTA */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1.5">CTA URL</label>
                <Input
                  value={form.cta_url}
                  onChange={(e) => setForm({ ...form, cta_url: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1.5">CTA label</label>
                <Input
                  value={form.cta_label}
                  onChange={(e) => setForm({ ...form, cta_label: e.target.value })}
                />
              </div>
            </div>

            {error && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
              <Dialog.Close asChild>
                <Button variant="ghost" size="sm" disabled={saving}>Cancel</Button>
              </Dialog.Close>
              {isEdit && initial && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={saving || previewLoading}
                  title="Render the HTML that will be sent without actually sending"
                  onClick={async () => {
                    setPreviewLoading(true);
                    try {
                      const p = await campaignsApi.preview(initial.id);
                      setPreviewData(p);
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "Preview failed");
                    } finally {
                      setPreviewLoading(false);
                    }
                  }}
                >
                  {previewLoading ? <Spinner /> : "Preview HTML"}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={saving}
                onClick={() => handleSave(false)}
              >
                {saving ? <Spinner /> : "Save"}
              </Button>
              <Button size="sm" disabled={saving} onClick={() => handleSave(true)}>
                {saving ? <Spinner /> : "Save & Send now"}
              </Button>
            </div>

            {!isEdit && (
              <div className="text-[11px] text-gray-500 text-right">
                Tip: Save the draft first, then re-open it to see the Preview HTML button.
              </div>
            )}
          </div>

          {previewData && (
            <PreviewModal data={previewData} onClose={() => setPreviewData(null)} />
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function PreviewModal({ data, onClose }: { data: CampaignPreview; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-[96vw] max-w-[760px] max-h-[92vh] overflow-hidden rounded-lg bg-white shadow-lg flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between p-4 border-b border-gray-200">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide">Email preview</div>
            <div className="text-sm font-semibold text-gray-900 mt-0.5">{data.subject}</div>
            <div className="text-xs text-gray-500 mt-1">
              To: {data.recipient} — Greeting: {data.recipient_name ? `"Dear ${data.recipient_name},"` : "(no name — generic greeting)"}
            </div>
            <div className="text-[10px] text-gray-400 font-mono mt-1">
              article: {data.article_id} · {data.html_length.toLocaleString()} chars
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
        </div>
        <iframe
          title="Email preview"
          srcDoc={data.html}
          className="flex-1 w-full border-0 bg-gray-50"
          style={{ minHeight: "480px" }}
          sandbox=""
        />
      </div>
    </div>
  );
}
