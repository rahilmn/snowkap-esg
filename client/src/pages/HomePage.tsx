/**
 * HomePage — "What matters most right now" dashboard.
 * Shows: FOMO stats + #1 priority card + 3 mini-cards + competitor section + Feed link.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
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
  const firstName = name.split(" ")[0];
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [scanResult, setScanResult] = useState<string | null>(null);

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
  });

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
              { value: statsData.predictions_count, label: "Predictions", color: COLORS.framework },
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
            <p style={{ fontSize: "16px", color: COLORS.textSecondary }}>Setting up your intelligence feed...</p>
            <p style={{ fontSize: "13px", color: COLORS.textMuted, marginTop: "8px" }}>
              Articles are being analyzed. This usually takes 1-2 minutes.
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
