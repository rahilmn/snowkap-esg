/**
 * Phase C — Inline card rendering an advisor coach hint inside the chat
 * stream OR on the sidebar.
 *
 * Hints carry a `coach`, `severity` (low/moderate/high), headline,
 * optional CTA. Dismissal posts to `/api/advisor/hints/dismiss` (not yet
 * shipped in this session — TODO follow-up).
 */
interface AdvisorHintCardProps {
  hint: {
    hint_id: string;
    coach: string;
    kind: string;
    severity: "low" | "moderate" | "high";
    headline: string;
    body: string;
    cta_label?: string | null;
    cta_target?: string | null;
  };
  onDismiss?: (hintId: string) => void;
}

export function AdvisorHintCard({ hint, onDismiss }: AdvisorHintCardProps) {
  const palette =
    hint.severity === "high" ? "border-red-300 bg-red-50"
      : hint.severity === "moderate" ? "border-amber-300 bg-amber-50"
      : "border-blue-300 bg-blue-50";

  return (
    <div className={`mt-2 rounded border ${palette} p-3 text-sm`}>
      <div className="flex items-center justify-between">
        <span className="font-semibold">{hint.coach}: {hint.headline}</span>
        {onDismiss && (
          <button
            onClick={() => onDismiss(hint.hint_id)}
            className="text-xs text-gray-500 hover:text-gray-800"
            aria-label="Dismiss hint"
          >
            ✕
          </button>
        )}
      </div>
      <div className="mt-1 text-gray-700">{hint.body}</div>
      {hint.cta_target && (
        <a
          href={hint.cta_target}
          className="mt-2 inline-block text-xs font-semibold text-blue-700 hover:underline"
        >
          {hint.cta_label ?? "Open →"}
        </a>
      )}
    </div>
  );
}
