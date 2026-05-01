/**
 * HomePage — "What matters most right now" dashboard.
 * Shows: FOMO stats + #1 priority card + 3 mini-cards + competitor section + Feed link.
 */

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { news } from "../lib/api";
import { useAuthStore } from "../stores/authStore";
import { COLORS, SHADOWS, RADII } from "../lib/designTokens";
import { PriorityBadge } from "../components/ui/PriorityBadge";
import { MiniArticleCard } from "../components/cards/MiniArticleCard";
import { ArticleDetailSheet } from "../components/panels/ArticleDetailSheet";
import { formatCurrency } from "../lib/utils";
import type { Article } from "../types";

function getGreeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

export default function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const name = useAuthStore((s) => s.name) || "there";
  const companyId = useAuthStore((s) => s.companyId);
  const setCompanyId = useAuthStore((s) => s.setCompanyId);
  const firstName = name.split(" ")[0];
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [scanResult, setScanResult] = useState<string | null>(null);

  // Phase 18 — honour `?company=<slug>` URL param so the "Open dashboard"
  // button on the onboarding success screen actually switches the
  // CompanySwitcher to the freshly-onboarded tenant. Pre-fix the user
  // had to manually find the new company in the dropdown after onboarding.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const fromUrl = searchParams.get("company");
    if (fromUrl && fromUrl !== companyId) {
      setCompanyId(fromUrl);
      // Clean the URL so back-nav / refresh doesn't keep re-applying.
      const next = new URLSearchParams(searchParams);
      next.delete("company");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, companyId, setCompanyId, setSearchParams]);

  const { data: feedData, isLoading } = useQuery({
    queryKey: ["home-articles", companyId],
    queryFn: () =>
      news.list({
        limit: 5,
        offset: 0,
        sort_by: "priority",
        company_id: companyId || undefined,
      }),
  });

  const { data: statsData } = useQuery({
    queryKey: ["news-stats", companyId],
    queryFn: () => news.stats(companyId || undefined),
    // Phase 13 S6: auto-refresh every 30s so the dashboard reflects new
    // articles ingested in the background without forcing the user to
    // click "Scan Now". Avoids the demo-day surprise where stats look
    // stale despite a fresh fetch having completed seconds earlier.
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });

  // Phase 22.1 — Poll the user's own onboarding status so the empty-
  // state copy distinguishes "still ingesting" (state in pending/
  // fetching/analysing) from "ingestion finished but found nothing"
  // (state=ready, total=0). Pre-fix a German prospect whose 2 articles
  // were both relevance-rejected got a permanent "still being analysed"
  // message even after the pipeline had given up.
  const { data: onboarding } = useQuery({
    queryKey: ["onboarding-status", companyId],
    queryFn: () => news.onboardingStatus(companyId || undefined),
    enabled: !!companyId,
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      return s === "pending" || s === "fetching" || s === "analysing" ? 5_000 : false;
    },
  });
  const onboardingState = onboarding?.state ?? "ready";
  const onboardingInProgress =
    onboardingState === "pending" ||
    onboardingState === "fetching" ||
    onboardingState === "analysing";
  const onboardingFailed = onboardingState === "failed";

  // Phase 22.1 — When the polled onboarding state transitions from
  // in-progress → ready (or → failed), invalidate the feed + stats
  // queries so the user sees freshly-indexed articles without having
  // to refresh manually. Without this, the initial empty `home-articles`
  // result is cached forever after the polling interval stops.
  useEffect(() => {
    if (!companyId) return;
    if (onboardingState === "ready" || onboardingState === "failed") {
      queryClient.invalidateQueries({ queryKey: ["home-articles", companyId] });
      queryClient.invalidateQueries({ queryKey: ["news-stats", companyId] });
    }
  }, [onboardingState, companyId, queryClient]);

  const refreshMutation = useMutation({
    mutationFn: () => news.refresh(),
    onSuccess: (data) => {
      setScanResult(`+${data.articles_stored} new articles`);
      queryClient.invalidateQueries({ queryKey: ["home-articles"] });
      queryClient.invalidateQueries({ queryKey: ["news-stats"] });
      setTimeout(() => setScanResult(null), 5000);
    },
    onError: () => {
      setScanResult("Scan failed — try again");
      setTimeout(() => setScanResult(null), 3000);
    },
  });

  // Phase A2 (Track A launch) — on-page-open background fetch.
  //
  // When an analyst opens the news page, fire `news.refresh()` in the
  // background IF the last fetch was > 10 minutes ago. The dashboard renders
  // immediately from the SQLite cache; the refresh runs silently and React
  // Query invalidates the stat tiles + feed when it lands.
  //
  // Cooldown guard via localStorage (not Zustand) so it survives hard
  // refresh + tab switch. Five analysts each opening the page in a
  // 10-minute window only triggers ONE upstream fetch.
  //
  // Failure-mode: if news.refresh() errors (NewsAPI rate-limit, etc), we
  // do NOT surface a toast — this is a silent background optimisation,
  // not a user-initiated action. The "Scan Now" button still does the
  // foreground version with feedback.
  useEffect(() => {
    const COOLDOWN_KEY = "snowkap-last-scan";
    const COOLDOWN_MS = 10 * 60 * 1000;
    const now = Date.now();
    let lastScanAt = 0;
    try {
      const raw = localStorage.getItem(COOLDOWN_KEY);
      if (raw) {
        const parsed = parseInt(raw, 10);
        if (Number.isFinite(parsed)) lastScanAt = parsed;
      }
    } catch {
      /* localStorage disabled (private browsing) — treat as cold */
    }
    if (now - lastScanAt < COOLDOWN_MS) return;
    // Optimistically write the new timestamp BEFORE firing so concurrent
    // mounts (multiple tabs opening within milliseconds) don't double-fetch.
    try {
      localStorage.setItem(COOLDOWN_KEY, String(now));
    } catch {
      /* ignore */
    }
    // Fire-and-forget. We invalidate the queries on success to refresh the
    // visible feed + stats without the user clicking anything.
    news
      .refresh()
      .then(() => {
        queryClient.invalidateQueries({ queryKey: ["home-articles"] });
        queryClient.invalidateQueries({ queryKey: ["news-stats"] });
      })
      .catch(() => {
        /* Silent failure — Scan Now button is the user-facing path */
      });
    // Empty deps: run exactly once per HomePage mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const topArticle = feedData?.[0] as Article | undefined;
  const moreArticles = (feedData?.slice(1, 4) || []) as Article[];
  const financialAmount = topArticle?.financial_signal?.amount;
  const topScore = topArticle?.impact_scores?.[0];
  const totalArticles = statsData?.total || 0;

  // AI insight text
  const insightText = topArticle?.executive_insight
    || (topScore?.explanation
      ? `${topScore.explanation}. ${topScore.frameworks?.length ? `Frameworks: ${topScore.frameworks.slice(0, 4).join(", ")}.` : ""}`
      : null);

  return (
    <div className="max-w-[440px] mx-auto min-h-screen relative pb-20" style={{ backgroundColor: COLORS.bgWhite }}>
      {/* Greeting */}
      <div style={{ paddingTop: "80px", paddingLeft: "47px", paddingRight: "47px" }}>
        <p style={{ fontSize: "16px", color: COLORS.textPrimary, fontWeight: 500 }}>
          {getGreeting()}, {firstName}!
        </p>
        <p style={{ fontSize: "14px", color: COLORS.textSecondary, marginTop: "2px" }}>
          Your ESG intelligence matrix.
        </p>
      </div>

      {/* FOMO Stats + Scan Now */}
      <div style={{ margin: "16px 24px 0" }}>
        {statsData && (
          <div
            className="flex items-center justify-between"
            style={{
              padding: "10px 14px",
              backgroundColor: COLORS.bgLight,
              borderRadius: RADII.card,
            }}
          >
            {[
              { value: statsData.total, label: "Articles", color: COLORS.textPrimary },
              { value: statsData.high_impact_count, label: "High Impact", color: COLORS.riskHigh },
              { value: statsData.new_last_24h, label: "New Today", color: COLORS.brand },
              // Phase 13 B8 — Replaced the always-zero "Predictions" stub
              // with "Active Signals" backed by HOME-tier CRITICAL/HIGH
              // articles in the last 7 days. Back-compat: prefer the new
              // `active_signals_count` field, fall back to `predictions_count`.
              {
                value: statsData.active_signals_count ?? statsData.predictions_count,
                label: "Active Signals",
                color: COLORS.framework,
              },
            ].map((stat, i) => (
              <div key={i} className="text-center">
                <p style={{ fontSize: "18px", fontWeight: 700, color: stat.color }}>{stat.value}</p>
                <p style={{ fontSize: "10px", color: COLORS.textMuted }}>{stat.label}</p>
              </div>
            ))}
          </div>
        )}

        {/* Scan Now button */}
        <div className="flex items-center justify-between" style={{ marginTop: "8px" }}>
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: refreshMutation.isPending ? COLORS.textMuted : COLORS.brand,
              background: "none",
              border: `1px solid ${refreshMutation.isPending ? COLORS.textDisabled : COLORS.brand}`,
              borderRadius: "16px",
              padding: "4px 12px",
              cursor: refreshMutation.isPending ? "not-allowed" : "pointer",
              opacity: refreshMutation.isPending ? 0.6 : 1,
              transition: "opacity 0.2s",
            }}
          >
            {refreshMutation.isPending ? "Scanning..." : "⟳ Scan Now"}
          </button>
          {scanResult && (
            <span style={{ fontSize: "12px", color: scanResult.startsWith("+") ? COLORS.brand : COLORS.riskHigh, fontWeight: 600 }}>
              {scanResult}
            </span>
          )}
        </div>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div style={{ padding: "60px 47px", textAlign: "center" }}>
          <p style={{ fontSize: "14px", color: COLORS.textMuted }}>Loading your ESG intelligence...</p>
        </div>
      )}

      {/* Empty state — FTUX educational content */}
      {!isLoading && !topArticle && (
        <div style={{ padding: "24px" }}>
          <div style={{ textAlign: "center", marginBottom: "24px" }}>
            <p style={{ fontSize: "16px", color: COLORS.textSecondary }}>
              {!companyId
                ? "Setting up your intelligence feed..."
                : onboardingInProgress
                  ? "Setting up your dashboard…"
                  : onboardingFailed
                    ? "We hit a snag onboarding your company."
                    : "No ESG-relevant news for your company in the latest scan."}
            </p>
            <p style={{ fontSize: "13px", color: COLORS.textMuted, marginTop: "8px" }}>
              {!companyId
                ? "Articles are being analyzed. This usually takes 1-2 minutes."
                : onboardingInProgress
                  ? `${onboarding?.analysed ?? 0} of ${onboarding?.fetched ?? 0} articles processed. Hang tight — this usually takes 1-2 minutes.`
                  : onboardingFailed
                    ? "Try Scan Now to retry, or contact your administrator if this keeps happening."
                    : "We searched the web for your company but didn't find ESG-relevant articles in this scan. The platform is optimised for Indian listed companies. Tap Scan Now to refetch."}
            </p>
            <button
              onClick={() => refreshMutation.mutate()}
              disabled={refreshMutation.isPending}
              style={{
                marginTop: "12px",
                fontSize: "13px",
                fontWeight: 600,
                color: "#fff",
                backgroundColor: refreshMutation.isPending ? COLORS.textMuted : COLORS.brand,
                border: "none",
                borderRadius: "20px",
                padding: "8px 20px",
                cursor: refreshMutation.isPending ? "not-allowed" : "pointer",
              }}
            >
              {refreshMutation.isPending ? "Scanning..." : "⟳ Scan for News Now"}
            </button>
            {scanResult && (
              <p style={{ fontSize: "12px", color: COLORS.brand, marginTop: "8px", fontWeight: 600 }}>{scanResult}</p>
            )}
          </div>

          {/* How Snowkap Works */}
          <div style={{ backgroundColor: COLORS.bgLight, borderRadius: "12px", padding: "20px", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: COLORS.brand, marginBottom: "12px" }}>How Snowkap Intelligence Works</h3>
            {[
              { step: "1", title: "News Monitoring", desc: "We scan 100+ sources for ESG-relevant news about your company and sector." },
              { step: "2", title: "5D Relevance Scoring", desc: "Each article scored on ESG Correlation, Financial Impact, Compliance Risk, Supply Chain, and People Impact." },
              { step: "3", title: "Causal Chain Analysis", desc: "Our knowledge graph traces how events propagate through your supply chain and facilities." },
              { step: "4", title: "AI Recommendations", desc: "3-agent validation ensures every recommendation is actionable and grounded in data." },
            ].map((item) => (
              <div key={item.step} className="flex gap-3 mb-3">
                <span style={{ width: "24px", height: "24px", borderRadius: "50%", backgroundColor: COLORS.brand, color: "#fff", fontSize: "12px", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>{item.step}</span>
                <div>
                  <p style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textPrimary }}>{item.title}</p>
                  <p style={{ fontSize: "12px", color: COLORS.textSecondary, lineHeight: "1.4" }}>{item.desc}</p>
                </div>
              </div>
            ))}
          </div>

          {/* Framework cards */}
          <div style={{ marginBottom: "16px" }}>
            <h3 style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", marginBottom: "8px" }}>Frameworks We Track</h3>
            <div className="flex gap-2 flex-wrap">
              {["BRSR", "GRI", "TCFD", "ESRS", "CDP", "SASB", "ISSB", "CSRD"].map((fw) => (
                <span key={fw} style={{ fontSize: "11px", fontWeight: 600, padding: "4px 10px", borderRadius: "12px", backgroundColor: "rgba(14,151,231,0.1)", color: COLORS.framework }}>{fw}</span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* #1 PRIORITY ALERT — 3-layer card */}
      {topArticle && (
        <div className="relative" style={{ marginTop: "20px", marginLeft: "24px", marginRight: "24px" }}>
          {/* Back + middle layers */}
          <div className="absolute" style={{ top: "14px", left: "8px", right: "8px", bottom: "-14px", backgroundColor: COLORS.cardStack2, borderRadius: RADII.card }} />
          <div className="absolute" style={{ top: "7px", left: "4px", right: "4px", bottom: "-7px", backgroundColor: COLORS.cardStack1, borderRadius: RADII.card }} />

          {/* Front card */}
          <div
            className="relative"
            style={{
              backgroundColor: COLORS.cardBg,
              border: `1px solid ${COLORS.cardBorder}`,
              borderRadius: RADII.card,
              boxShadow: SHADOWS.card,
              padding: "20px 24px",
            }}
          >
            <div className="flex items-center justify-between">
              <span style={{ fontSize: "13px", color: COLORS.textSecondary }}>
                {topArticle.frameworks?.[0]?.split(":")[0] || "ESG"} /{" "}
                {topArticle.esg_pillar === "E" ? "Environmental" : topArticle.esg_pillar === "S" ? "Social" : "Governance"}
              </span>
              <div className="flex items-center gap-2">
                {topArticle.relevance_score != null && (
                  <span style={{
                    fontSize: "11px", fontWeight: 700, padding: "2px 6px", borderRadius: "4px",
                    backgroundColor: topArticle.relevance_score >= 7 ? "rgba(223,89,0,0.12)" : "rgba(0,0,0,0.06)",
                    color: topArticle.relevance_score >= 7 ? COLORS.brand : COLORS.textSecondary,
                  }}>
                    {topArticle.relevance_score.toFixed(1)}/10
                  </span>
                )}
                <PriorityBadge level={topArticle.priority_level} />
              </div>
            </div>

            <h2 style={{ fontSize: "20px", color: COLORS.textPrimary, marginTop: "10px", lineHeight: "1.3" }}>
              {topArticle.title}
            </h2>

            {/* AI Insight */}
            <p style={{ fontSize: "13px", color: COLORS.textSecondary, marginTop: "10px", lineHeight: "1.5" }}>
              {insightText || topArticle.summary}
            </p>

            {/* Time horizon + relevance tier badge */}
            {(() => {
              const di = topArticle.deep_insight as Record<string, Record<string, string>> | null;
              const th = di?.time_horizon;
              if (!th) return null;
              return (
                <div className="flex gap-2 mt-2 flex-wrap">
                  {Object.entries(th).map(([k, v]) => (
                    <span key={k} style={{ fontSize: "10px", padding: "2px 6px", borderRadius: "4px", backgroundColor: "rgba(0,0,0,0.04)", color: COLORS.textSecondary }}>
                      {k.replace(/_/g, " ")}: {typeof v === "string" ? v.slice(0, 40) : ""}...
                    </span>
                  ))}
                </div>
              );
            })()}

            {financialAmount && (
              <p style={{ fontSize: "13px", color: COLORS.textMuted, marginTop: "6px" }}>
                Financial Exposure: {formatCurrency(financialAmount)}
              </p>
            )}

            <div style={{ borderTop: `1px solid ${COLORS.textDisabled}`, marginTop: "14px", paddingTop: "10px" }}>
              <button
                onClick={() => setSelectedArticle(topArticle)}
                style={{ fontSize: "13px", fontWeight: 500, color: COLORS.brand, background: "none", border: "none", cursor: "pointer", padding: 0 }}
              >
                View Insights &rarr;
              </button>
            </div>
          </div>
        </div>
      )}

      {/* RECENT HIGH-PRIORITY — Mini cards */}
      {moreArticles.length > 0 && (
        <div style={{ padding: "20px 24px 0" }}>
          <h3 style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
            More Priority Updates
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {moreArticles.map((article) => (
              <MiniArticleCard
                key={article.id}
                article={article}
                onClick={() => setSelectedArticle(article)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Feed link */}
      {totalArticles > 5 && (
        <div style={{ padding: "20px 24px 0" }}>
          <button
            onClick={() => navigate("/feed")}
            style={{ fontSize: "14px", color: COLORS.brand, fontWeight: 500, background: "none", border: "none", cursor: "pointer", padding: 0 }}
          >
            {totalArticles - 5} more stories in Feed &rarr;
          </button>
        </div>
      )}

      {/* Unified detail sheet — no key prop to prevent remount on close */}
      <ArticleDetailSheet
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
    </div>
  );
}
