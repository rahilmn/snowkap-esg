/** Share button + dialog (Phase 9).
 *
 * Drops into ArticleDetailSheet (or any article view). User clicks Share,
 * types a recipient email, optionally previews, then sends. Name is
 * auto-extracted from the email for the greeting.
 *
 * Uses @radix-ui/react-dialog (already installed). No new deps.
 */

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { news } from "@/lib/api";

interface ShareArticleButtonProps {
  articleId: string;
  /** Optional: CSS class for the trigger button (for alignment in toolbars) */
  className?: string;
  /** Optional: tone of the button (default = ghost, so it sits in toolbars) */
  variant?: "default" | "outline" | "ghost";
  /** Optional: label text (default "Share") */
  label?: string;
  /** Optional: custom CTA base URL passed to the renderer */
  readMoreBase?: string;
  /** Called after a successful send — caller can show a toast, analytics, etc. */
  onSent?: (result: SendResult) => void;
}

interface SendResult {
  status: "sent" | "preview" | "failed";
  recipient: string;
  recipient_name: string | null;
  subject: string;
  provider_id: string;
  error: string;
}

type Step = "idle" | "form" | "preview" | "sending" | "success" | "error";

export function ShareArticleButton({
  articleId,
  className,
  variant = "ghost",
  label = "Share",
  readMoreBase,
  onSent,
}: ShareArticleButtonProps) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<Step>("form");
  const [email, setEmail] = useState("");
  const [senderNote, setSenderNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [previewSubject, setPreviewSubject] = useState("");
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<SendResult | null>(null);

  const reset = () => {
    setStep("form");
    setError(null);
    setPreviewSubject("");
    setPreviewName(null);
    setLastResult(null);
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      // Small delay so reset doesn't flash during close animation
      setTimeout(reset, 150);
    }
  };

  const handlePreview = async () => {
    if (!email || !email.includes("@")) {
      setError("Enter a valid email address");
      return;
    }
    setError(null);
    setStep("sending");
    try {
      const res = await news.sharePreview(articleId, {
        recipient_email: email,
        sender_note: senderNote || undefined,
        read_more_base: readMoreBase,
      });
      setPreviewSubject(res.subject);
      setPreviewName(res.recipient_name);
      setStep("preview");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Preview failed";
      setError(msg);
      setStep("form");
    }
  };

  const handleSend = async () => {
    if (!email || !email.includes("@")) {
      setError("Enter a valid email address");
      return;
    }
    setError(null);
    setStep("sending");
    try {
      const res = await news.share(articleId, {
        recipient_email: email,
        sender_note: senderNote || undefined,
        read_more_base: readMoreBase,
      });
      setLastResult(res as SendResult);
      if (res.status === "sent" || res.status === "preview") {
        setStep("success");
        onSent?.(res as SendResult);
      } else {
        setError(res.error || "Send failed");
        setStep("error");
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Send failed";
      setError(msg);
      setStep("error");
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Trigger asChild>
        <Button variant={variant} size="sm" className={className}>
          <ShareIcon />
          <span className="ml-1.5">{label}</span>
        </Button>
      </Dialog.Trigger>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-[50%] top-[50%] z-50 w-[90vw] max-w-[520px] translate-x-[-50%] translate-y-[-50%] rounded-lg border border-gray-200 bg-white p-6 shadow-lg focus:outline-none">
          <Dialog.Title className="text-lg font-semibold text-gray-900">
            Share this brief
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-gray-500">
            Sends a one-article HTML brief to the recipient.
            The greeting uses the first name auto-extracted from their email.
          </Dialog.Description>

          {step === "form" && (
            <div className="mt-5 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1.5">
                  Recipient email
                </label>
                <Input
                  type="email"
                  placeholder="ambalika.mehrotra@mintedit.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1.5">
                  Add a short note (optional)
                </label>
                <textarea
                  className="w-full h-20 rounded-md border border-gray-200 bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-gray-400"
                  placeholder="Context for the recipient — why you're sending this, what to look at first..."
                  value={senderNote}
                  onChange={(e) => setSenderNote(e.target.value)}
                  maxLength={800}
                />
              </div>
              {error && (
                <div className="text-xs text-red-600">{error}</div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Dialog.Close asChild>
                  <Button variant="ghost" size="sm">Cancel</Button>
                </Dialog.Close>
                <Button variant="outline" size="sm" onClick={handlePreview}>
                  Preview
                </Button>
                <Button size="sm" onClick={handleSend}>
                  Send
                </Button>
              </div>
            </div>
          )}

          {step === "sending" && (
            <div className="mt-6 flex items-center justify-center gap-2 text-sm text-gray-600">
              <Spinner /> Rendering…
            </div>
          )}

          {step === "preview" && (
            <div className="mt-5 space-y-4">
              <div className="rounded-md bg-gray-50 border border-gray-200 p-3 text-xs space-y-1">
                <div><span className="font-semibold text-gray-700">To:</span> {email}</div>
                <div>
                  <span className="font-semibold text-gray-700">Greeting:</span>{" "}
                  {previewName ? `"Dear ${previewName},"` : "(no name extracted — neutral greeting)"}
                </div>
                <div><span className="font-semibold text-gray-700">Subject:</span> {previewSubject}</div>
              </div>
              <div className="text-xs text-gray-500">
                Looks right? Hit Send to deliver via Resend.
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" size="sm" onClick={() => setStep("form")}>
                  Back
                </Button>
                <Button size="sm" onClick={handleSend}>
                  Send
                </Button>
              </div>
            </div>
          )}

          {step === "success" && (
            <div className="mt-5 space-y-3">
              <div className="rounded-md border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
                <div className="flex items-center gap-2 font-semibold">
                  <CheckIcon /> {lastResult?.status === "sent" ? "Sent" : "Queued (preview mode)"}
                </div>
                <div className="mt-2 text-xs space-y-0.5">
                  <div>To: {lastResult?.recipient}</div>
                  {lastResult?.recipient_name && (
                    <div>Greeted as: {lastResult.recipient_name}</div>
                  )}
                  <div>Subject: {lastResult?.subject}</div>
                  {lastResult?.provider_id && (
                    <div className="font-mono text-[10px] text-emerald-700">
                      id: {lastResult.provider_id}
                    </div>
                  )}
                  {lastResult?.status === "preview" && !lastResult.provider_id && (
                    <div className="text-[11px] text-amber-700 mt-1">
                      RESEND_API_KEY not configured — brief was rendered but not sent. Add the key to go live.
                    </div>
                  )}
                </div>
              </div>
              <div className="flex justify-end">
                <Dialog.Close asChild>
                  <Button size="sm">Close</Button>
                </Dialog.Close>
              </div>
            </div>
          )}

          {step === "error" && (
            <div className="mt-5 space-y-3">
              <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-900">
                <div className="font-semibold">Couldn&apos;t send</div>
                <div className="mt-1 text-xs">{error}</div>
              </div>
              <div className="flex justify-end gap-2">
                <Dialog.Close asChild>
                  <Button variant="ghost" size="sm">Close</Button>
                </Dialog.Close>
                <Button size="sm" onClick={() => setStep("form")}>
                  Try again
                </Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// --- inline SVG icons (no lucide dep) ---

function ShareIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="18" cy="5" r="3" />
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="19" r="3" />
      <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
      <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}
