/** Saved news page — grid of bookmarked articles (Stage 6.9) */

import { useState, useCallback } from "react";
import { useSavedStore } from "@/stores/savedStore";
import { KnowMoreSheet } from "@/components/panels/KnowMoreSheet";
import { esgPillarBg, formatDate } from "@/lib/utils";
import { computeFomoTag } from "@/lib/fomo";
import type { Article } from "@/types";

export function SavedNewsPage() {
  const { savedArticles, unsaveArticle } = useSavedStore();
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [longPressId, setLongPressId] = useState<string | null>(null);

  const handleTap = useCallback((article: Article) => {
    setSelectedArticle(article);
  }, []);

  const handleLongPress = useCallback(
    (articleId: string) => {
      setLongPressId(articleId);
    },
    [],
  );

  const confirmUnsave = useCallback(
    (articleId: string) => {
      unsaveArticle(articleId);
      setLongPressId(null);
    },
    [unsaveArticle],
  );

  if (savedArticles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-8 pb-14">
        <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-4">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-gray-400">
            <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
          </svg>
        </div>
        <p className="text-lg font-medium text-gray-900">No saved stories</p>
        <p className="text-sm text-muted-foreground mt-1">
          Swipe right on news cards to save them here
        </p>
      </div>
    );
  }

  return (
    <div className="px-4 pt-4 pb-16">
      <h2 className="text-lg font-bold text-gray-900 mb-4">
        Saved Stories ({savedArticles.length})
      </h2>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {savedArticles.map((article) => {
          const topScore = article.impact_scores?.[0];
          const impactScore = topScore?.impact_score ?? 0;
          const fomo = computeFomoTag(article.published_at, impactScore);

          return (
            <div
              key={article.id}
              className="relative bg-white rounded-lg border border-gray-200 p-3 cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => handleTap(article)}
              onContextMenu={(e) => {
                e.preventDefault();
                handleLongPress(article.id);
              }}
            >
              {/* Unsave confirmation overlay */}
              {longPressId === article.id && (
                <div className="absolute inset-0 bg-white/95 rounded-lg flex items-center justify-center z-10">
                  <div className="text-center">
                    <p className="text-sm text-gray-700 mb-2">Remove from saved?</p>
                    <div className="flex gap-2">
                      <button
                        onClick={(e) => { e.stopPropagation(); confirmUnsave(article.id); }}
                        className="px-3 py-1 text-xs bg-red-500 text-white rounded-md"
                      >
                        Remove
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); setLongPressId(null); }}
                        className="px-3 py-1 text-xs bg-gray-200 text-gray-700 rounded-md"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex items-center gap-2 mb-1">
                {article.esg_pillar && (
                  <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full border ${esgPillarBg(article.esg_pillar)}`}>
                    {article.esg_pillar}
                  </span>
                )}
                {fomo.tag && (
                  <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${fomo.bgColor} ${fomo.color}`}>
                    {fomo.tag}
                  </span>
                )}
              </div>

              <h3 className="text-sm font-medium text-gray-900 line-clamp-2 leading-tight">
                {article.title}
              </h3>

              <div className="flex items-center gap-2 mt-1.5 text-[10px] text-muted-foreground">
                {article.source && <span>{article.source}</span>}
                {article.published_at && (
                  <>
                    <span>·</span>
                    <span>{formatDate(article.published_at)}</span>
                  </>
                )}
              </div>

              {impactScore > 0 && (
                <span className="mt-1.5 inline-block text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-red-50 text-red-700">
                  Impact: {impactScore.toFixed(0)}
                </span>
              )}
            </div>
          );
        })}
      </div>

      <KnowMoreSheet
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
    </div>
  );
}
