/** News card — individual article card for swipe stack (Stage 6.3) */

import type { Article } from "@/types";
import { esgPillarBg } from "@/lib/utils";
import { computeFomoTag } from "@/lib/fomo";
import { formatDate } from "@/lib/utils";

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

export function NewsCard({ article }: NewsCardProps) {
  const topScore = article.impact_scores?.[0];
  const impactScore = topScore?.impact_score ?? 0;
  const fomo = computeFomoTag(article.published_at, impactScore);
  const pillar = article.esg_pillar?.toLowerCase() || "";
  const gradient = PILLAR_GRADIENTS[pillar] || "from-gray-500 to-gray-600";

  return (
    <div className="rounded-xl shadow-lg bg-white border border-gray-100 overflow-hidden select-none aspect-[375/425]">
      {/* Top row: pillar badge + FOMO tag */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        {article.esg_pillar && (
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${esgPillarBg(article.esg_pillar)}`}>
            {article.esg_pillar}
          </span>
        )}
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
            <span>·</span>
            <span>{formatDate(article.published_at)}</span>
          </>
        )}
      </div>

      {/* Hero image or gradient fallback */}
      <div className="mx-4 mt-2 rounded-lg overflow-hidden aspect-[16/9]">
        {article.image_url ? (
          <img
            src={article.image_url}
            alt={article.title}
            className="w-full h-full object-cover"
            onError={(e) => {
              // Fall back to gradient on image load failure
              const target = e.currentTarget;
              target.style.display = "none";
              target.parentElement!.innerHTML = `<div class="w-full h-full bg-gradient-to-br ${gradient} flex items-center justify-center"><span class="text-white/80 text-4xl font-bold">${(article.esg_pillar || "ESG").charAt(0).toUpperCase()}</span></div>`;
            }}
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

      {/* Tag row */}
      <div className="px-4 pt-2 pb-1 flex items-center gap-2 flex-wrap">
        {impactScore > 0 && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">
            Impact: {impactScore.toFixed(0)}
          </span>
        )}
        {article.entities?.slice(0, 2).map((e, i) => (
          <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
            {typeof e === "string" ? e : (e as { text?: string }).text || ""}
          </span>
        ))}
        {article.frameworks?.slice(0, 2).map((fw, i) => (
          <span key={`fw-${i}`} className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">
            {fw}
          </span>
        ))}
      </div>

      {/* CTA */}
      <div className="px-4 pb-3 pt-1">
        <p className="text-xs text-center text-muted-foreground">
          Tap to know more · Swipe right to save
        </p>
      </div>
    </div>
  );
}
