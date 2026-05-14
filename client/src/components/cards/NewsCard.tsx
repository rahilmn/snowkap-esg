/** News card — individual article card for swipe stack.
 * Fix 1: Added PriorityBadge, sentiment indicator, content type chip.
 * Fix 2: Updated CTA to "Swipe up to save".
 */

import { useState } from "react";
import type { Article } from "@/types";
import { esgPillarBg, formatDate } from "@/lib/utils";
import { computeFomoTag } from "@/lib/fomo";
import { PriorityBadge } from "@/components/ui/PriorityBadge";
import { OutsideFocusBadge } from "@/components/persona/OutsideFocusBadge";

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

  // Phase 25 W10 — CRITICAL/HIGH visual hierarchy. Per the user's
  // feedback ("what matters most appears first"), articles flagged
  // CRITICAL get a red 4px left border + larger headline so they
  // visually pop relative to MEDIUM/LOW cards in the same feed.
  // MEDIUM/LOW keep the original card style — purely additive.
  const priorityUpper = (article.priority_level || "").toUpperCase();
  const isCritical = priorityUpper === "CRITICAL";
  const isHigh = priorityUpper === "HIGH";
  const cardClasses = isCritical
    ? "rounded-xl shadow-lg bg-white overflow-hidden select-none aspect-[375/425] border-l-4 border-l-red-600 border-y border-r border-y-gray-100 border-r-gray-100"
    : isHigh
    ? "rounded-xl shadow-lg bg-white overflow-hidden select-none aspect-[375/425] border-l-4 border-l-orange-500 border-y border-r border-y-gray-100 border-r-gray-100"
    : "rounded-xl shadow-lg bg-white border border-gray-100 overflow-hidden select-none aspect-[375/425]";
  const headlineClasses = isCritical
    ? "px-4 pt-1 text-lg font-bold leading-tight line-clamp-2 text-gray-900"
    : "px-4 pt-1 text-base font-semibold leading-tight line-clamp-2 text-gray-900";

  return (
    <div className={cardClasses}>
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
          {/* W10 — CRITICAL ribbon, only on CRITICAL cards */}
          {isCritical && (
            <span className="ml-1 rounded bg-red-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
              CRITICAL
            </span>
          )}
          {/* Phase 6 §8.3 — surfaces only when API returned outside_focus.
              Discoverability invariant in action: a CRITICAL article that
              isn't on the user's persona-saved esg_focus surfaces anyway,
              with a chip explaining why the feed picked it. */}
          <OutsideFocusBadge outsideFocus={article.outside_focus} />
        </div>
        {fomo.tag && (
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${fomo.bgColor} ${fomo.color}`}>
            {fomo.tag}
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className={headlineClasses}>
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
