/** Phase 28 — Onboarding progress page (SSE-driven).
 *
 * Route: /onboarding/:slug. Reached from ProfilePage after the user
 * submits their domain. Listens on the SSE stream
 * `GET /api/me/onboard/{slug}/stream` so the UI feels live:
 *
 *   • <2s   — "Looking up your company..."
 *   • ~3s   — Profile card appears with company name + industry
 *   • ~30s  — "Fetching ESG news" (skeleton placeholder)
 *   • ~60s  — Three skeleton article cards lock in (critical_3_selected)
 *   • each ~45s — Cards fill in as `analysis_done` arrives
 *   • <3min — "Ready" + redirect to /home?company={slug}
 *
 * Polling fallback (existing `/api/admin/onboard/{slug}/status`) covers
 * SSE-unsupported clients / transient network failures.
 *
 * Pre-Phase-28 behavior (polling-only) is preserved as the fallback so
 * disabling SSE doesn't regress the original W2 experience.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { me, streamOnboarding, type OnboardStatus } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { COLORS } from "@/lib/designTokens";

type OnboardState = OnboardStatus["state"];

interface CompanyProfile {
  slug: string;
  name: string;
  industry: string;
  region: string;
}

interface ArticleCard {
  article_id: string;
  position: number;
  total: number;
  headline?: string;
  criticality_band?: string;
  status: "pending" | "analysing" | "done" | "failed";
}

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
    sub: "Building your 3 critical insights for the day.",
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

  // SSE-driven live state (Phase 28). Falls back to polling below.
  const [sseAlive, setSseAlive] = useState<boolean>(false);
  const [sseTerminal, setSseTerminal] = useState<"complete" | "failed" | null>(null);
  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  const [stageMessage, setStageMessage] = useState<string>("");
  const [cards, setCards] = useState<ArticleCard[]>([]);
  const cancelRef = useRef<(() => void) | null>(null);

  // Open SSE stream once we have the slug.
  useEffect(() => {
    if (!slug) return;
    setSseAlive(true);
    cancelRef.current = streamOnboarding(
      slug,
      (event, data) => {
        switch (event) {
          case "stream_start":
            setStageMessage("Looking up your company…");
            break;
          case "company_profile_ready":
            setProfile({
              slug: String(data.slug ?? slug),
              name: String(data.name ?? ""),
              industry: String(data.industry ?? "Unknown"),
              region: String(data.region ?? "GLOBAL"),
            });
            setStageMessage("Profile resolved. Fetching ESG news…");
            break;
          case "news_fetch_started":
            setStageMessage("Searching ESG headlines across global publishers…");
            break;
          case "news_fetch_done":
            setStageMessage(
              `Pulled ${data.n_articles ?? 0} candidate articles. Pulling full article text…`,
            );
            break;
          case "full_text_capture_done": {
            // Phase 36 — body-grounded analysis guarantee.
            // Shown for ~2s before "critical_3_selected" advances the UI.
            const bodied = Number(data.bodies_added ?? 0) + Number(data.already_grounded ?? 0);
            const checked = Number(data.candidates_checked ?? 0);
            const paywalled = Number(data.paywalled_skipped ?? 0);
            const suffix = paywalled > 0 ? ` (${paywalled} paywalled)` : "";
            setStageMessage(
              `${bodied}/${checked} top articles body-grounded${suffix}. Picking the 3 most critical for you.`,
            );
            break;
          }
          case "critical_3_selected": {
            const ids = Array.isArray(data.article_ids) ? (data.article_ids as string[]) : [];
            setCards(ids.slice(0, 3).map((id, idx) => ({
              article_id: id,
              position: idx + 1,
              total: 3,
              status: "pending",
            })));
            setStageMessage("Analysing your 3 critical articles…");
            break;
          }
          case "analysis_started": {
            const articleId = String(data.article_id ?? "");
            setCards((prev) => prev.map((c) =>
              c.article_id === articleId ? { ...c, status: "analysing" } : c,
            ));
            break;
          }
          case "analysis_done": {
            const articleId = String(data.article_id ?? "");
            const headline = data.headline ? String(data.headline) : undefined;
            const band = data.criticality_band ? String(data.criticality_band) : undefined;
            setCards((prev) => prev.map((c) =>
              c.article_id === articleId
                ? {
                    ...c,
                    status: band === "FAILED" ? "failed" : "done",
                    headline,
                    criticality_band: band,
                  }
                : c,
            ));
            break;
          }
          case "onboard_complete":
            setSseTerminal("complete");
            setStageMessage("Done. Opening your dashboard…");
            break;
          case "onboard_failed":
            setSseTerminal("failed");
            setStageMessage(String(data.error ?? "Onboarding failed."));
            break;
          default:
            // Unknown event — ignore (forwards-compat).
            break;
        }
      },
      () => {
        // SSE network error → fall back to polling (already running).
        setSseAlive(false);
      },
    );
    return () => {
      cancelRef.current?.();
      cancelRef.current = null;
    };
  }, [slug]);

  // Polling fallback — also keeps `status.error` text visible if SSE drops.
  const statusQuery = useQuery({
    queryKey: ["me-onboard-status", slug],
    queryFn: () => (slug ? me.onboardStatus(slug) : Promise.resolve(null)),
    enabled: !!slug,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return 5_000;
      if (data.state === "ready" || data.state === "failed") return false;
      // SSE is live → poll slower to conserve battery / network.
      return sseAlive ? 15_000 : 5_000;
    },
  });

  // When status (or SSE) flips to terminal, refresh caches + redirect.
  useEffect(() => {
    const terminal = sseTerminal === "complete" || statusQuery.data?.state === "ready";
    if (terminal && slug) {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "tenants"] });
      const t = setTimeout(() => navigate(`/home?company=${slug}`), 1200);
      return () => clearTimeout(t);
    }
  }, [sseTerminal, statusQuery.data?.state, slug, queryClient, navigate]);

  if (!slug) {
    return (
      <div style={{ padding: 32, textAlign: "center", color: COLORS.textMuted }}>
        Missing slug.
      </div>
    );
  }

  const status = statusQuery.data;
  // Prefer SSE terminal state; fall back to polled status.
  const effectiveState: OnboardState = sseTerminal === "complete"
    ? "ready"
    : sseTerminal === "failed"
      ? "failed"
      : (status?.state ?? "pending");
  const stateCopy = STATE_COPY[effectiveState];

  const summary = useMemo(() => ({
    fetched: status?.fetched ?? 0,
    analysed: status?.analysed ?? cards.filter((c) => c.status === "done").length,
    home_count: status?.home_count ?? 0,
  }), [status, cards]);

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px 64px" }}>
      <header style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: COLORS.textPrimary }}>
          Personalising Power of Now
        </h1>
        <p style={{ fontSize: 13, color: COLORS.textMuted, margin: "8px 0 0", lineHeight: 1.55 }}>
          Setting up <strong>{profile?.name || slug}</strong>
          {profile?.industry ? <> — <em>{profile.industry}</em></> : null}.
          Typically takes ~3 minutes. You can close this tab and come back later.
        </p>
      </header>

      <div style={{
        background: "#fff", border: "1px solid #E2E8F0",
        borderRadius: 12, padding: 24,
      }}>
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
              {stageMessage || stateCopy.sub}
            </p>
          </div>
          {effectiveState !== "ready" && effectiveState !== "failed" && <Spinner />}
        </div>

        {/* Today's 3 critical — skeleton-fill UX */}
        {cards.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <div style={{
              fontSize: 11, fontWeight: 700, letterSpacing: 0.4,
              textTransform: "uppercase", color: COLORS.textMuted, marginBottom: 12,
            }}>
              Today’s 3 critical
            </div>
            <div style={{ display: "grid", gap: 10 }}>
              {cards.map((card) => (
                <ArticleSkeletonCard key={card.article_id} card={card} />
              ))}
            </div>
          </div>
        )}

        {(summary.fetched > 0 || summary.analysed > 0) && (
          <div style={{
            marginTop: 20, padding: "12px 16px",
            background: "#F8FAFC", borderRadius: 8,
            display: "flex", gap: 24,
          }}>
            <Stat label="Fetched" value={summary.fetched} />
            <Stat label="Analysed" value={summary.analysed} />
            <Stat label="High-impact" value={summary.home_count} />
          </div>
        )}

        {effectiveState === "failed" && status?.error && (
          <div style={{
            marginTop: 20, padding: "12px 14px", borderRadius: 8,
            background: "#FEF2F2", border: "1px solid #FECACA",
            color: "#7F1D1D", fontSize: 12, lineHeight: 1.5,
            whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto",
          }}>
            {status.error}
          </div>
        )}

        {effectiveState === "failed" && (
          <div style={{ marginTop: 16 }}>
            <Button onClick={() => navigate("/profile")}>Try again</Button>
          </div>
        )}

        {effectiveState === "ready" && (
          <div style={{ marginTop: 16 }}>
            <Button onClick={() => navigate(`/home?company=${slug}`)}>
              Open dashboard →
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function ArticleSkeletonCard({ card }: { card: ArticleCard }) {
  const isDone = card.status === "done";
  const isFailed = card.status === "failed";
  const accent = isFailed
    ? "#DC2626"
    : card.criticality_band === "HOME"
      ? "#DF5900"
      : "#94A3B8";
  return (
    <div style={{
      border: "1px solid #E2E8F0", borderRadius: 8,
      padding: "12px 14px", display: "flex", alignItems: "center", gap: 12,
      background: isDone ? "#FFFFFF" : "#F8FAFC",
      transition: "background-color 200ms ease",
    }}>
      <div style={{
        width: 28, height: 28, borderRadius: 14,
        background: isDone || isFailed ? `${accent}1A` : "#E2E8F0",
        color: accent,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 12, fontWeight: 700,
      }}>
        {card.position}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {isDone && card.headline ? (
          <>
            <div style={{
              fontSize: 13, fontWeight: 600, color: COLORS.textPrimary,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {card.headline}
            </div>
            {card.criticality_band && (
              <div style={{
                fontSize: 10, fontWeight: 700, letterSpacing: 0.4,
                textTransform: "uppercase", color: accent, marginTop: 2,
              }}>
                {card.criticality_band}
              </div>
            )}
          </>
        ) : isFailed ? (
          <div style={{ fontSize: 12, color: "#7F1D1D" }}>
            Article {card.position} failed to analyse — will retry.
          </div>
        ) : (
          <>
            <div style={{
              height: 12, background: "#E2E8F0", borderRadius: 4,
              width: "80%", marginBottom: 6,
            }} />
            <div style={{
              height: 8, background: "#E2E8F0", borderRadius: 4,
              width: "40%",
            }} />
          </>
        )}
      </div>
      {card.status === "analysing" && <Spinner />}
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
