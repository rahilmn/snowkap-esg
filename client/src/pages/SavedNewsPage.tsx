/** Saved news page — grid of bookmarked articles with multi-select delete */

import { useState, useCallback } from "react";
import { useSavedStore } from "@/stores/savedStore";
import { ArticleDetailSheet } from "@/components/panels/ArticleDetailSheet";
import { esgPillarBg, formatDate } from "@/lib/utils";
import { computeFomoTag } from "@/lib/fomo";
import type { Article } from "@/types";

export function SavedNewsPage() {
  const { savedArticles, unsaveArticle, clearAll } = useSavedStore();
  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [showClearConfirm, setShowClearConfirm] = useState(false);

  // Multi-select mode
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds(new Set(savedArticles.map((a) => a.id)));
  }, [savedArticles]);

  const deselectAll = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const deleteSelected = useCallback(() => {
    selectedIds.forEach((id) => unsaveArticle(id));
    setSelectedIds(new Set());
    setSelectMode(false);
  }, [selectedIds, unsaveArticle]);

  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setSelectedIds(new Set());
  }, []);

  const handleTap = useCallback(
    (article: Article) => {
      if (selectMode) {
        toggleSelect(article.id);
      } else {
        setSelectedArticle(article);
      }
    },
    [selectMode, toggleSelect],
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
      {/* Header — normal mode vs select mode */}
      {!selectMode ? (
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold text-gray-900">
            Saved Stories ({savedArticles.length})
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelectMode(true)}
              className="text-xs text-gray-600 font-medium px-3 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 transition-colors"
            >
              Select
            </button>
            <button
              onClick={() => setShowClearConfirm(true)}
              className="text-xs text-red-500 font-medium px-3 py-1.5 rounded-md border border-red-200 hover:bg-red-50 transition-colors"
            >
              Clear All
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold text-gray-900">
            {selectedIds.size} selected
          </h2>
          <div className="flex items-center gap-2">
            {selectedIds.size < savedArticles.length ? (
              <button
                onClick={selectAll}
                className="text-xs text-gray-600 font-medium px-3 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                Select All
              </button>
            ) : (
              <button
                onClick={deselectAll}
                className="text-xs text-gray-600 font-medium px-3 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                Deselect All
              </button>
            )}
            <button
              onClick={exitSelectMode}
              className="text-xs text-gray-600 font-medium px-3 py-1.5 rounded-md border border-gray-200 hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Clear all confirmation */}
      {showClearConfirm && (
        <div className="mb-4 p-3 rounded-lg border border-red-200 bg-red-50 flex items-center justify-between">
          <p className="text-sm text-red-700">Remove all {savedArticles.length} saved stories?</p>
          <div className="flex gap-2">
            <button
              onClick={() => { clearAll(); setShowClearConfirm(false); setSelectMode(false); }}
              className="px-3 py-1 text-xs bg-red-500 text-white rounded-md font-medium"
            >
              Yes, clear all
            </button>
            <button
              onClick={() => setShowClearConfirm(false)}
              className="px-3 py-1 text-xs bg-white text-gray-700 rounded-md border border-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Article grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {savedArticles.map((article) => {
          const topScore = article.impact_scores?.[0];
          const impactScore = topScore?.impact_score ?? 0;
          const fomo = computeFomoTag(article.published_at, impactScore);
          const isSelected = selectedIds.has(article.id);

          return (
            <div
              key={article.id}
              className={`relative rounded-lg border p-3 cursor-pointer transition-all ${
                isSelected
                  ? "border-red-400 bg-red-50/50 shadow-sm"
                  : "border-gray-200 bg-white hover:shadow-md"
              }`}
              onClick={() => handleTap(article)}
            >
              {/* Selection checkbox (visible in select mode) */}
              {selectMode && (
                <div
                  className={`absolute top-2 right-2 w-5 h-5 rounded-full border-2 flex items-center justify-center z-10 ${
                    isSelected
                      ? "border-red-500 bg-red-500"
                      : "border-gray-300 bg-white"
                  }`}
                >
                  {isSelected && (
                    <svg width="10" height="10" viewBox="0 0 12 12" fill="none">
                      <path d="M2 6l3 3 5-5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
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

              <h3 className="text-sm font-medium text-gray-900 line-clamp-2 leading-tight pr-6">
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

      {/* Sticky delete bar (visible when items selected) */}
      {selectMode && selectedIds.size > 0 && (
        <div className="fixed bottom-16 left-0 right-0 z-40 flex justify-center">
          <button
            onClick={deleteSelected}
            className="px-6 py-3 bg-red-500 text-white text-sm font-semibold rounded-full shadow-lg hover:bg-red-600 transition-colors"
          >
            Delete {selectedIds.size} {selectedIds.size === 1 ? "story" : "stories"}
          </button>
        </div>
      )}

      <ArticleDetailSheet
        key={selectedArticle?.id}
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
    </div>
  );
}
