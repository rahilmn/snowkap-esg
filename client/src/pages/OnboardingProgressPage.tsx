/** W2 — Onboarding progress page.
 *
 * Route: /onboarding/:slug. The user lands here from ProfilePage after
 * submitting their domain. We poll GET /api/admin/onboard/{slug}/status
 * every 5s and surface progress (Pending → Fetching → Analysing → Ready).
 *
 * Reuses the status-poll endpoint that admin onboarding already uses
 * (no new backend route required).
 *
 * On `ready`: redirect to /home?company={slug}.
 * On `failed`: show the error string + a "Try again" button that goes
 * back to /profile.
 */

import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, type OnboardStatus } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { COLORS } from "@/lib/designTokens";

type OnboardState = OnboardStatus["state"];

const STATE_COPY: Record<OnboardState, { label: string; sub: string; color: string }> = {
  pending: {
    label: "Queued",
    sub: "Waiting for the analysis pipeline to pick this up.",
    color: "#94A3B8",
  },
  fetching: {
    label: "Fetching ESG news",
    sub: "Pulling articles from your industry across major publishers.",
    color: "#3B82F6",
  },
  analysing: {
    label: "Running analysis pipeline",
    sub: "12 stages per article — NLP, themes, frameworks, primitives, recommendations.",
    color: "#DF5900",
  },
  ready: {
    label: "Ready",
    sub: "Your dashboard is live. Redirecting in a moment…",
    color: "#16A34A",
  },
  failed: {
    label: "Failed",
    sub: "Something went wrong. See details below.",
    color: "#DC2626",
  },
};

export default function OnboardingProgressPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const statusQuery = useQuery({
    queryKey: ["me-onboard-status", slug],
    queryFn: () => (slug ? me.onboardStatus(slug) : Promise.resolve(null)),
    enabled: !!slug,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 5_000;
      if (data.state === "ready" || data.state === "failed") return false;
      return 5_000;
    },
  });

  // When status flips to ready, refresh the global tenants/companies cache
  // so the new tenant appears in the company switcher, then redirect.
  useEffect(() => {
    if (statusQuery.data?.state === "ready" && slug) {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "tenants"] });
      const t = setTimeout(() => navigate(`/home?company=${slug}`), 1200);
      return () => clearTimeout(t);
    }
  }, [statusQuery.data?.state, slug, queryClient, navigate]);

  if (!slug) {
    return (
      <div style={{ padding: 32, textAlign: "center", color: COLORS.textMuted }}>
        Missing slug.
      </div>
    );
  }

  const status = statusQuery.data;
  const stateCopy = status ? STATE_COPY[status.state] : null;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px 64px" }}>
      <header style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: COLORS.textPrimary }}>
          Personalising Snowkap
        </h1>
        <p style={{ fontSize: 13, color: COLORS.textMuted, margin: "8px 0 0", lineHeight: 1.55 }}>
          Setting up <strong>{slug}</strong> — typically takes ~4 minutes.
          You can close this tab; the pipeline runs in the background and
          your dashboard will be ready when you sign back in.
        </p>
      </header>

      <div style={{
        background: "#fff", border: "1px solid #E2E8F0",
        borderRadius: 12, padding: 24,
      }}>
        {!status && (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Spinner />
            <span style={{ fontSize: 13, color: COLORS.textMuted }}>
              Loading status…
            </span>
          </div>
        )}

        {status && stateCopy && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{
                  display: "inline-block", padding: "4px 10px", borderRadius: 12,
                  background: `${stateCopy.color}1A`, color: stateCopy.color,
                  fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase",
                }}>
                  {stateCopy.label}
                </div>
                <p style={{ fontSize: 14, color: COLORS.textPrimary, margin: "12px 0 0", lineHeight: 1.5 }}>
                  {stateCopy.sub}
                </p>
              </div>
              {(status.state === "fetching" || status.state === "analysing" || status.state === "pending") && (
                <Spinner />
              )}
            </div>

            {(status.fetched > 0 || status.analysed > 0) && (
              <div style={{
                marginTop: 20, padding: "12px 16px",
                background: "#F8FAFC", borderRadius: 8,
                display: "flex", gap: 24,
              }}>
                <Stat label="Fetched" value={status.fetched} />
                <Stat label="Analysed" value={status.analysed} />
                <Stat label="High-impact" value={status.home_count} />
              </div>
            )}

            {status.state === "failed" && status.error && (
              <div style={{
                marginTop: 20, padding: "12px 14px", borderRadius: 8,
                background: "#FEF2F2", border: "1px solid #FECACA",
                color: "#7F1D1D", fontSize: 12, lineHeight: 1.5,
                whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto",
              }}>
                {status.error}
              </div>
            )}

            {status.state === "failed" && (
              <div style={{ marginTop: 16 }}>
                <Button onClick={() => navigate("/profile")}>Try again</Button>
              </div>
            )}

            {status.state === "ready" && (
              <div style={{ marginTop: 16 }}>
                <Button onClick={() => navigate(`/home?company=${slug}`)}>
                  Open dashboard →
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div style={{ fontSize: 22, fontWeight: 700, color: COLORS.textPrimary }}>{value}</div>
      <div style={{ fontSize: 10, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {label}
      </div>
    </div>
  );
}
