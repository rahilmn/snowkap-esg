/** News card — individual article card for swipe stack.
 * Fix 1: Added PriorityBadge, sentiment indicator, content type chip.
 * Fix 2: Updated CTA to "Swipe up to save".
 */

import { useState } from "react";
import type { Article } from "@/types";
import { esgPillarBg, formatDate } from "@/lib/utils";
import { computeFomoTag } from "@/lib/fomo";
import { PriorityBadge } from "@/components/ui/PriorityBadge";

interface NewsCardProps {
  article: Article;
}

const PILLAR_GRADIENTS: Record<string, string> = {
  environmental: "from-emerald-500 to-teal-600",
  e: "from-emerald-500 to-teal-600",
  social: "from-blue-500 to-indigo-600",
  s: "from-blue-500 to-indigo-600",
  governance: "from-violet-500 to-purple-600",
  g: "from-violet-500 to-purple-600",
};

function SentimentDot({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const color = score > 0.2 ? "#18a87d" : score < -0.2 ? "#ff4044" : "#888888";
  const arrow = score > 0.2 ? "\u25B2" : score < -0.2 ? "\u25BC" : "\u25CF";
  return (
    <span style={{ color, fontSize: "10px", fontWeight: 700 }}>
      {arrow} {score > 0 ? "+" : ""}{score.toFixed(1)}
    </span>
  );
}

export function NewsCard({ article }: NewsCardProps) {
  const [imgFailed, setImgFailed] = useState(false);
  const topScore = article.impact_scores?.[0];
  const impactScore = topScore?.impact_score ?? 0;
  const fomo = computeFomoTag(article.published_at, impactScore);
  const pillar = article.esg_pillar?.toLowerCase() || "";
  const gradient = PILLAR_GRADIENTS[pillar] || "from-gray-500 to-gray-600";

  return (
    <div className="rounded-xl shadow-lg bg-white border border-gray-100 overflow-hidden select-none aspect-[375/425]">
      {/* Top row: pillar + priority + sentiment + FOMO */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <div className="flex items-center gap-1.5">
          {article.esg_pillar && (
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${esgPillarBg(article.esg_pillar)}`}>
              {article.esg_pillar}
            </span>
          )}
          <PriorityBadge level={article.priority_level} />
          <SentimentDot score={article.sentiment_score} />
        </div>
        {fomo.tag && (
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${fomo.bgColor} ${fomo.color}`}>
            {fomo.tag}
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className="px-4 pt-1 text-base font-semibold leading-tight line-clamp-2 text-gray-900">
        {article.title}
      </h3>

      {/* Source + time */}
      <div className="px-4 pt-1 flex items-center gap-2 text-xs text-muted-foreground">
        {article.source && <span>{article.source}</span>}
        {article.published_at && (
          <>
            <span>&middot;</span>
            <span>{formatDate(article.published_at)}</span>
          </>
        )}
      </div>

      {/* Hero image or gradient fallback */}
      <div className="mx-4 mt-2 rounded-lg overflow-hidden aspect-[16/9]">
        {article.image_url && !imgFailed ? (
          <img
            src={article.image_url}
            alt={article.title}
            className="w-full h-full object-cover"
            onError={() => setImgFailed(true)}
          />
        ) : (
          <div className={`w-full h-full bg-gradient-to-br ${gradient} flex items-center justify-center`}>
            <span className="text-white/80 text-4xl font-bold">
              {(article.esg_pillar || "ESG").charAt(0).toUpperCase()}
            </span>
          </div>
        )}
      </div>

      {/* Summary */}
      <p className="px-4 pt-2 text-sm text-gray-600 line-clamp-2 leading-relaxed">
        {article.summary || "Tap to read more about this story and its ESG impact."}
      </p>

      {/* Tag row: content type + frameworks */}
      <div className="px-4 pt-2 pb-1 flex items-center gap-1.5 flex-wrap">
        {article.content_type && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-800 text-white">
            {article.content_type.charAt(0).toUpperCase() + article.content_type.slice(1)}
          </span>
        )}
        {article.frameworks?.slice(0, 3).map((fw, i) => (
          <span key={`fw-${i}`} className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">
            {fw.split(":")[0]}
          </span>
        ))}
        {impactScore > 0 && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">
            Impact: {impactScore.toFixed(0)}
          </span>
        )}
      </div>

      {/* CTA — updated for new swipe gestures */}
      <div className="px-4 pb-3 pt-1">
        <p className="text-xs text-center text-muted-foreground">
          Tap to know more &middot; Swipe up to save
        </p>
      </div>
    </div>
  );
}
