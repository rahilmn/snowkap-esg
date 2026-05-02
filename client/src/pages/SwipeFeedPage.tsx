/** Swipe Feed Page — main news experience (Stage 6.9) */

import React, { useState, useCallback, useMemo, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { news } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useNewsStore } from "@/stores/newsStore";
import { useSavedStore } from "@/stores/savedStore";
import { useFeedStore } from "@/stores/feedStore";
import { SwipeCardStack } from "@/components/cards/SwipeCardStack";
import { NewsCard } from "@/components/cards/NewsCard";
import { IntroCard } from "@/components/cards/IntroCard";
import { ArticleDetailSheet } from "@/components/panels/ArticleDetailSheet";
import { Spinner } from "@/components/ui/Spinner";
import type { Article } from "@/types";

const MIN_NEWS = 5;
const PAGE_SIZE = 20;

export function SwipeFeedPage() {
  const { articles, setArticles } = useNewsStore();
  const companyId = useAuthStore((s) => s.companyId);
  const queryClient = useQueryClient();

  // Clear stale data on mount
  React.useEffect(() => {
    useFeedStore.getState().reset();
  }, []);
  const { saveArticle } = useSavedStore();
  const { hasSeenIntro, markIntroSeen, dismissedIds, setRefreshTime } = useFeedStore();
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [offset, setOffset] = useState(0);

  // Phase 22.1 — Poll the user's own onboarding status so we can
  // differentiate "still ingesting" from "ingestion finished but
  // produced nothing" in the empty state. Without this, prospects
  // whose articles all got relevance-rejected see a permanent
  // "Fetching ESG intelligence..." spinner.
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

  // Phase 22.1 — refetch the feed when the polled onboarding state
  // transitions from in-progress → ready/failed, so freshly-indexed
  // articles appear without a manual refresh after the pipeline lands.
  useEffect(() => {
    if (!companyId) return;
    if (onboardingState === "ready" || onboardingState === "failed") {
      queryClient.invalidateQueries({ queryKey: ["news-feed", companyId] });
    }
  }, [onboardingState, companyId, queryClient]);

  const { isLoading, refetch } = useQuery({
    queryKey: ["news-feed", companyId, offset],
    queryFn: async () => {
      const result = await news.list({
        limit: PAGE_SIZE,
        offset,
        sort_by: "priority",
        company_id: companyId || undefined,
      });
      if (offset === 0) {
        setArticles(result);
        // Clear dismissed cards on fresh load so new articles show
        useFeedStore.getState().reset();
      } else {
        setArticles([...useNewsStore.getState().articles, ...result]);
      }
      return result;
    },
    staleTime: 30_000, // Cache for 30s to prevent refetch on detail sheet close
    refetchOnMount: true,
  });

  // Filter out dismissed articles
  const visibleArticles = useMemo(
    () => articles.filter((a) => !dismissedIds.has(a.id)),
    [articles, dismissedIds],
  );

  // FOMO metrics for intro card
  const highImpactCount = useMemo(
    () => articles.filter((a) => (a.impact_scores?.[0]?.impact_score ?? 0) >= 70).length,
    [articles],
  );
  const predictionCount = useMemo(
    () => articles.filter((a) => a.predictions?.length > 0).length,
    [articles],
  );

  // Auto-fetch more when running low
  const handleCardChange = useCallback(() => {
    if (visibleArticles.length - (articles.length - visibleArticles.length) < MIN_NEWS) {
      setOffset((prev) => prev + PAGE_SIZE);
    }
  }, [visibleArticles.length, articles.length]);

  const handleSwipeRight = useCallback(
    (card: Article) => {
      saveArticle(card);
      handleCardChange();
    },
    [saveArticle, handleCardChange],
  );

  const handleSwipeLeft = useCallback(
    (_card: Article) => {
      handleCardChange();
    },
    [handleCardChange],
  );

  const handleTap = useCallback((card: Article) => {
    setSelectedArticle(card);
  }, []);

  const handleRefresh = useCallback(() => {
    setOffset(0);
    setRefreshTime();
    refetch();
  }, [refetch, setRefreshTime]);

  // Loading state
  if (isLoading && articles.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-full pb-14 bg-white">
      {/* Intro card — shown once per session */}
      {!hasSeenIntro && articles.length > 0 && (
        <div className="flex items-center justify-center h-full">
          <IntroCard
            articleCount={articles.length}
            highImpactCount={highImpactCount}
            predictionCount={predictionCount}
            newSinceLastVisit={articles.length}
            onStart={markIntroSeen}
          />
        </div>
      )}

      {/* Swipe card stack */}
      {hasSeenIntro && visibleArticles.length > 0 && (
        <SwipeCardStack
          cards={visibleArticles}
          onSwipeRight={handleSwipeRight}
          onSwipeLeft={handleSwipeLeft}
          onTap={handleTap}
          onRefresh={handleRefresh}
          renderCard={(card) => <NewsCard article={card} />}
        />
      )}

      {hasSeenIntro && visibleArticles.length === 0 && !isLoading && (
        <div className="text-center px-8" style={{ color: "#888" }}>
          <div style={{ fontSize: "48px", marginBottom: "16px" }}>
            {onboardingInProgress ? "\u{1F680}" : onboardingState === "failed" ? "\u26A0\uFE0F" : "\u{1F50D}"}
          </div>
          <p className="text-lg font-medium" style={{ color: "#111" }}>
            {onboardingInProgress
              ? "Fetching ESG intelligence..."
              : onboardingState === "failed"
                ? "We hit a snag onboarding your company."
                : "No ESG-relevant news yet."}
          </p>
          <p className="text-sm mt-2" style={{ lineHeight: "1.6" }}>
            {onboardingInProgress
              ? `${onboarding?.analysed ?? 0} of ${onboarding?.fetched ?? 0} articles processed. We're gathering and analysing news for your company; articles will appear here as they're scored.`
              : onboardingState === "failed"
                ? "Pull down to retry the scan, or contact your administrator if this keeps happening."
                : "We searched the web for your company but didn't find ESG-relevant articles in the latest scan. The platform is optimised for listed companies across major exchanges. Pull down to refetch."}
          </p>
          {onboardingInProgress && (
            <p className="text-xs mt-4" style={{ color: "#999" }}>
              This usually takes 1-2 minutes for new accounts.
              <br />Pull down to refresh.
            </p>
          )}
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
