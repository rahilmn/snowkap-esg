/** Swipe Feed Page — main news experience (Stage 6.9) */

import React, { useState, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { news } from "@/lib/api";
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

  // Clear stale data on mount
  React.useEffect(() => {
    useFeedStore.getState().reset();
  }, []);
  const { saveArticle } = useSavedStore();
  const { hasSeenIntro, markIntroSeen, dismissedIds, setRefreshTime } = useFeedStore();
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [offset, setOffset] = useState(0);

  const { isLoading, refetch } = useQuery({
    queryKey: ["news-feed", offset],
    queryFn: async () => {
      const result = await news.list({ limit: PAGE_SIZE, offset, sort_by: "priority" });
      if (offset === 0) {
        setArticles(result);
        // Clear dismissed cards on fresh load so new articles show
        useFeedStore.getState().reset();
      } else {
        setArticles([...useNewsStore.getState().articles, ...result]);
      }
      return result;
    },
    staleTime: 0, // Always refetch fresh data
    refetchOnMount: "always",
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
    <div className="flex flex-col items-center justify-center h-full pb-14">
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
          <div style={{ fontSize: "48px", marginBottom: "16px" }}>&#128640;</div>
          <p className="text-lg font-medium" style={{ color: "#111" }}>
            Fetching ESG intelligence...
          </p>
          <p className="text-sm mt-2" style={{ lineHeight: "1.6" }}>
            We&apos;re gathering and analyzing news for your company.
            Articles will appear here as they&apos;re processed.
          </p>
          <p className="text-xs mt-4" style={{ color: "#999" }}>
            This usually takes 1-2 minutes for new accounts.
            <br />Pull down to refresh.
          </p>
        </div>
      )}

      {/* Unified detail sheet */}
      <ArticleDetailSheet
        key={selectedArticle?.id}
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
    </div>
  );
}
