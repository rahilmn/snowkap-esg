/** Swipe Feed Page — main news experience (Stage 6.9) */

import { useState, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { news } from "@/lib/api";
import { useNewsStore } from "@/stores/newsStore";
import { useSavedStore } from "@/stores/savedStore";
import { useFeedStore } from "@/stores/feedStore";
import { SwipeCardStack } from "@/components/cards/SwipeCardStack";
import { NewsCard } from "@/components/cards/NewsCard";
import { IntroCard } from "@/components/cards/IntroCard";
import { KnowMoreSheet } from "@/components/panels/KnowMoreSheet";
import { Spinner } from "@/components/ui/Spinner";
import type { Article } from "@/types";

const MIN_NEWS = 5;
const PAGE_SIZE = 20;

export function SwipeFeedPage() {
  const { articles, setArticles } = useNewsStore();
  const { saveArticle } = useSavedStore();
  const { hasSeenIntro, markIntroSeen, dismissedIds, setRefreshTime } = useFeedStore();
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [offset, setOffset] = useState(0);

  const { isLoading, refetch } = useQuery({
    queryKey: ["news-feed", offset],
    queryFn: async () => {
      const result = await news.list({ limit: PAGE_SIZE, offset });
      if (offset === 0) {
        setArticles(result);
      } else {
        setArticles([...articles, ...result]);
      }
      return result;
    },
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
        <div className="text-center text-muted-foreground">
          <p className="text-lg font-medium">No news stories yet</p>
          <p className="text-sm mt-1">Check back later for ESG updates</p>
        </div>
      )}

      {/* Know More bottom sheet */}
      <KnowMoreSheet
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
    </div>
  );
}
