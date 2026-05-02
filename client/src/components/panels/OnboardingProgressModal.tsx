/**
 * OnboardingProgressModal — Task #2
 *
 * Non-blocking floating card shown at the bottom of HomePage while a
 * brand-new prospect's tenant is still being onboarded in the background.
 *
 * Backend context: the /auth/login + /auth/returning-user endpoints kick
 * off `_background_onboard` for any first-time corporate email. That task
 * walks 3 stages — fetch ESG articles → run 12-stage pipeline per article
 * → mark ready — and writes progress to the `onboarding_status` table.
 * Frontend polls /api/news/onboarding-status every 5s (in HomePage) and
 * feeds the row into this modal via props.
 *
 * Design choices:
 *   - Bottom-anchored card (not a full-screen modal) so the user can
 *     still browse the empty-state FTUX content above. The task spec
 *     explicitly calls this "non-blocking".
 *   - State-driven copy: pending / fetching / analysing / failed each
 *     get their own headline + subline. Counts come straight from the
 *     status row so the progress feels real.
 *   - On `ready`, parent stops rendering us — auto-dismiss is implicit.
 *   - On `failed`, we expose a Retry CTA that re-calls /api/admin/onboard
 *     with the user's domain (the same endpoint that originally seeded
 *     the row). Non-admin prospects will get a 403 from the backend; we
 *     surface that clearly rather than failing silently.
 */

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { admin as adminApi } from "@/lib/api";
import { COLORS, RADII, SHADOWS } from "@/lib/designTokens";

type OnboardState = "pending" | "fetching" | "analysing" | "ready" | "failed";

export interface OnboardingProgressModalProps {
  /** Live status row from /api/news/onboarding-status. */
  state: OnboardState;
  fetched: number;
  analysed: number;
  /** Display name to show in the headline. Falls back to "your company". */
  companyName: string;
  /** Backend error message (only set when state==="failed"). */
  error: string | null;
  /** Domain used to seed the original onboard call — passed to the retry. */
  domain: string | null;
  /** Optional: called after a successful retry kick-off so the parent can
   *  invalidate its onboarding-status query and resume polling immediately. */
  onRetryQueued?: () => void;
}

// Stage 1 fetches ~10 articles (`limit=10` in admin_onboard.OnboardRequest).
const TARGET_ARTICLES = 10;

interface CopyBundle {
  headline: string;
  sub: string;
  /** 0..1, drives the progress bar fill. */
  progress: number;
  accent: string;
}

function buildCopy(
  state: OnboardState,
  fetched: number,
  analysed: number,
  companyName: string,
): CopyBundle {
  const safeFetched = Math.max(0, fetched);
  const safeAnalysed = Math.max(0, Math.min(analysed, safeFetched || analysed));

  switch (state) {
    case "pending":
      return {
        headline: `Preparing your dashboard for ${companyName}`,
        sub: "Queued — Snowkap is warming up the analysis pipeline.",
        // Tiny visible sliver so the bar never looks empty/stuck.
        progress: 0.05,
        accent: COLORS.brand,
      };
    case "fetching": {
      const denom = TARGET_ARTICLES;
      // Stage 1 weight: 0% → 50% of the bar.
      const pct = Math.min(safeFetched / denom, 1) * 0.5;
      return {
        headline: `Fetching news for ${companyName}…`,
        sub:
          safeFetched > 0
            ? `Pulled ${safeFetched} of ~${denom} ESG-relevant articles so far.`
            : "Searching 100+ sources for ESG-relevant coverage.",
        progress: Math.max(pct, 0.1),
        accent: COLORS.brand,
      };
    }
    case "analysing": {
      // Stage 2 weight: 50% → 95% of the bar (we keep the last 5% for the
      // "ready" snap so the jump from analysing → dismissed feels earned).
      const denom = safeFetched > 0 ? safeFetched : TARGET_ARTICLES;
      const pct = 0.5 + Math.min(safeAnalysed / denom, 1) * 0.45;
      return {
        headline: `Analysing news for ${companyName}…`,
        sub: `${safeAnalysed} of ${denom} articles processed through the 12-stage pipeline.`,
        progress: Math.max(pct, 0.55),
        accent: COLORS.framework,
      };
    }
    case "failed":
      return {
        headline: `We hit a snag setting up ${companyName}.`,
        sub: "The onboarding pipeline didn't complete. You can retry, or your administrator can re-run it from Settings → Onboard Company.",
        progress: 1,
        accent: COLORS.riskHigh,
      };
    case "ready":
    default:
      return {
        headline: `${companyName} is ready.`,
        sub: "Loading your latest insights…",
        progress: 1,
        accent: COLORS.opportunity,
      };
  }
}

export function OnboardingProgressModal({
  state,
  fetched,
  analysed,
  companyName,
  error,
  domain,
  onRetryQueued,
}: OnboardingProgressModalProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [retryNotice, setRetryNotice] = useState<string | null>(null);

  const copy = useMemo(
    () => buildCopy(state, fetched, analysed, companyName),
    [state, fetched, analysed, companyName],
  );

  const retryMutation = useMutation({
    mutationFn: () => {
      // The original onboarder accepts `name`, `ticker_hint`, or `domain`.
      // Login uses domain-only (see `_ensure_tenant_for_login`), and the
      // backend's `_slugify(seed)` prioritises `name` over `domain` when
      // both are set — passing the resolved/humanised name here would
      // generate a *different* slug than the user's JWT session is bound
      // to, leaving the UI polling the original failed slug while the
      // retry progresses elsewhere. Domain-only keeps the slug aligned.
      if (!domain) {
        throw new Error("No domain on session to retry with.");
      }
      return adminApi.onboard({ domain });
    },
    onSuccess: () => {
      setRetryNotice("Restarted — fetching fresh articles…");
      onRetryQueued?.();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "Retry failed.";
      // Non-admins will hit a 403 here; surface it explicitly so the user
      // knows to escalate rather than mashing the button.
      const isForbidden = /403|forbidden|permission/i.test(msg);
      setRetryNotice(
        isForbidden
          ? "Your account can't restart onboarding. Please contact your administrator."
          : `Retry failed: ${msg.slice(0, 140)}`,
      );
    },
  });

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label="Show onboarding progress"
        style={{
          position: "fixed",
          bottom: "20px",
          left: "50%",
          transform: "translateX(-50%)",
          backgroundColor: copy.accent,
          color: "#fff",
          border: "none",
          borderRadius: RADII.pill,
          padding: "8px 16px",
          fontSize: "12px",
          fontWeight: 600,
          boxShadow: SHADOWS.button,
          cursor: "pointer",
          zIndex: 60,
        }}
      >
        {state === "failed" ? "⚠ Onboarding failed" : "Preparing dashboard…"}
      </button>
    );
  }

  return (
    <div
      role={state === "failed" ? "alert" : "status"}
      aria-live={state === "failed" ? "assertive" : "polite"}
      style={{
        position: "fixed",
        bottom: "16px",
        left: "50%",
        transform: "translateX(-50%)",
        width: "min(420px, calc(100vw - 24px))",
        backgroundColor: COLORS.cardBg,
        border: `1px solid ${COLORS.cardBorder}`,
        borderRadius: RADII.card,
        boxShadow: SHADOWS.card,
        padding: "16px 18px 14px",
        zIndex: 60,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <span
          aria-hidden
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: RADII.circle,
            backgroundColor: `${copy.accent}1A`,
            color: copy.accent,
            fontSize: 14,
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          {state === "failed" ? "!" : (
            <span
              style={{
                width: 12,
                height: 12,
                border: `2px solid ${copy.accent}`,
                borderTopColor: "transparent",
                borderRadius: "50%",
                animation: "snowkap-spin 0.9s linear infinite",
              }}
            />
          )}
        </span>

        <div style={{ flex: 1, minWidth: 0 }}>
          <p
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: COLORS.textPrimary,
              margin: 0,
              lineHeight: 1.3,
            }}
          >
            {copy.headline}
          </p>
          <p
            style={{
              fontSize: 12,
              color: COLORS.textSecondary,
              margin: "4px 0 0",
              lineHeight: 1.4,
            }}
          >
            {copy.sub}
          </p>
        </div>

        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="Hide onboarding progress"
          style={{
            background: "none",
            border: "none",
            color: COLORS.textMuted,
            fontSize: 16,
            lineHeight: 1,
            cursor: "pointer",
            padding: "0 0 0 8px",
            flexShrink: 0,
          }}
        >
          ×
        </button>
      </div>

      {/* Progress bar — hidden in failed state so the red doesn't fight the alert tone. */}
      {state !== "failed" && (
        <div
          style={{
            marginTop: 12,
            height: 4,
            backgroundColor: COLORS.cardStack1,
            borderRadius: 2,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${Math.round(copy.progress * 100)}%`,
              height: "100%",
              backgroundColor: copy.accent,
              transition: "width 600ms ease",
            }}
          />
        </div>
      )}

      {/* Failure error line + retry CTA */}
      {state === "failed" && (
        <>
          {error && (
            <pre
              style={{
                margin: "10px 0 0",
                padding: "8px 10px",
                fontSize: 11,
                color: COLORS.riskHigh,
                backgroundColor: "rgba(255,64,68,0.06)",
                borderRadius: 6,
                whiteSpace: "pre-wrap",
                maxHeight: 80,
                overflow: "auto",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              }}
            >
              {error.slice(0, 240)}
            </pre>
          )}
          <div
            style={{
              marginTop: 12,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 10,
            }}
          >
            <button
              type="button"
              onClick={() => {
                setRetryNotice(null);
                retryMutation.mutate();
              }}
              disabled={retryMutation.isPending || !domain}
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "#fff",
                backgroundColor:
                  retryMutation.isPending || !domain
                    ? COLORS.textMuted
                    : COLORS.brand,
                border: "none",
                borderRadius: RADII.pill,
                padding: "6px 14px",
                cursor:
                  retryMutation.isPending || !domain ? "not-allowed" : "pointer",
              }}
            >
              {retryMutation.isPending ? "Retrying…" : "Retry onboarding"}
            </button>
            {retryNotice && (
              <span
                style={{
                  fontSize: 11,
                  color: retryMutation.isError
                    ? COLORS.riskHigh
                    : COLORS.textSecondary,
                  textAlign: "right",
                  flex: 1,
                }}
              >
                {retryNotice}
              </span>
            )}
          </div>
        </>
      )}

      {/* Local keyframes — kept inline so the modal is self-contained and
          doesn't leak a global animation into the design system. */}
      <style>{`
        @keyframes snowkap-spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
