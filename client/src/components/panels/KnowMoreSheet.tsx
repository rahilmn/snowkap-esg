/** Know More bottom sheet — article detail panel (Stage 6.5) */

import { useCallback, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import type { Article } from "@/types";
import { esgPillarBg, formatDate } from "@/lib/utils";
import { ImpactMetrics } from "./ImpactMetrics";
import { CausalChainViz } from "./CausalChainViz";
import { FrameworkComplianceMap } from "./FrameworkComplianceMap";
import { PredictionSummary } from "./PredictionSummary";

interface KnowMoreSheetProps {
  article: Article | null;
  onClose: () => void;
}

export function KnowMoreSheet({ article, onClose }: KnowMoreSheetProps) {
  const navigate = useNavigate();
  const sheetRef = useRef<HTMLDivElement>(null);

  // Close on backdrop click
  const handleBackdrop = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  // Close on Escape
  useEffect(() => {
    if (!article) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [article, onClose]);

  if (!article) return null;

  const topScore = article.impact_scores?.[0];
  const prediction = article.predictions?.[0];
  const consensus = prediction
    ? { analysis: prediction.summary, risk_level: prediction.risk_level }
    : null;

  const handleAskAI = () => {
    navigate("/agent", { state: { articleId: article.id, articleTitle: article.title } });
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm"
      onClick={handleBackdrop}
    >
      <div
        ref={sheetRef}
        className="absolute bottom-0 left-0 right-0 bg-white rounded-t-2xl shadow-2xl overflow-y-auto transition-transform"
        style={{ maxHeight: "75vh" }}
      >
        {/* Drag handle */}
        <div className="sticky top-0 bg-white pt-3 pb-2 flex justify-center z-10">
          <div className="w-10 h-1 rounded-full bg-gray-300" />
        </div>

        <div className="px-5 pb-8 space-y-5">
          {/* 1. Article header */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              {article.esg_pillar && (
                <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${esgPillarBg(article.esg_pillar)}`}>
                  {article.esg_pillar}
                </span>
              )}
              {article.source && (
                <span className="text-xs text-muted-foreground">{article.source}</span>
              )}
              {article.published_at && (
                <span className="text-xs text-muted-foreground">{formatDate(article.published_at)}</span>
              )}
            </div>
            <h2 className="text-lg font-bold text-gray-900 leading-tight">{article.title}</h2>
            {article.summary && (
              <p className="mt-2 text-sm text-gray-600 leading-relaxed">{article.summary}</p>
            )}
          </div>

          {/* 2. Impact Assessment */}
          {topScore && (
            <ImpactMetrics
              impactScore={topScore.impact_score}
              causalHops={topScore.causal_hops}
              financialExposure={topScore.financial_exposure}
              companyName={topScore.company_name}
            />
          )}

          {/* 3. Causal Chain */}
          {topScore?.explanation && (
            <CausalChainViz
              hops={topScore.causal_hops}
              relationshipType={topScore.relationship_type}
              explanation={topScore.explanation}
              impactScore={topScore.impact_score}
            />
          )}

          {/* 4. Framework Compliance Map */}
          {(article.framework_hits?.length > 0 || article.frameworks?.length > 0) && (
            <FrameworkComplianceMap
              frameworkHits={article.framework_hits || []}
              frameworks={article.frameworks || []}
              esgPillar={article.esg_pillar}
            />
          )}

          {/* 5. Prediction */}
          {prediction && (
            <PredictionSummary prediction={prediction} />
          )}

          {/* 6. Agent Consensus */}
          {consensus?.analysis && (
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
              <h4 className="text-sm font-semibold text-gray-700 mb-2">Agent Consensus</h4>
              <p className="text-sm text-gray-600">{consensus.analysis}</p>
              {consensus.risk_level && (
                <span className={`mt-2 inline-block text-xs font-medium px-2 py-0.5 rounded-full ${
                  consensus.risk_level === "high" || consensus.risk_level === "critical"
                    ? "bg-red-100 text-red-700"
                    : consensus.risk_level === "medium"
                    ? "bg-amber-100 text-amber-700"
                    : "bg-green-100 text-green-700"
                }`}>
                  Risk: {consensus.risk_level}
                </span>
              )}
            </div>
          )}

          {/* 7. Actions */}
          <div className="flex gap-3">
            <button
              onClick={handleAskAI}
              className="flex-1 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors"
            >
              Ask AI for deeper analysis
            </button>
            {article.url && (
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="px-4 py-2.5 border border-gray-300 text-sm font-medium rounded-lg hover:bg-gray-50 transition-colors text-gray-700"
              >
                Read full article
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
