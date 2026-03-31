/**
 * Phase 3D: Home-Popup — Dark card + vertical causal chain.
 * Layout verified from UX/Home-popup/HomePopup.html DOM order.
 *
 * Layout (top to bottom):
 * 1. Dark card (#080707) — alert content only
 * 2. Executive Insight (white bg, outside dark card)
 * 3. Causal Chain Analysis header
 * 4. VerticalCausalChain (1st→2nd→3rd→Opportunity)
 * 5. "Ask AI" + "View Article" black buttons
 */

import { COLORS, SHADOWS, RADII } from "../../lib/designTokens";
import { PriorityBadge } from "../ui/PriorityBadge";
import { VerticalCausalChain } from "./VerticalCausalChain";
import type { Article } from "../../types";
import { formatCurrency } from "../../lib/utils";

interface HomePopupProps {
  article: Article;
  onClose: () => void;
  onAskAI: () => void;
  onViewArticle: () => void;
  highRiskCount?: number;
  frameworkUpdateCount?: number;
}

export function HomePopup({
  article,
  onClose,
  onAskAI,
  onViewArticle,
  highRiskCount = 3,
  frameworkUpdateCount = 2,
}: HomePopupProps) {
  const topScore = article.impact_scores?.[0];
  const financialAmount = article.financial_signal?.amount;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto" style={{ backgroundColor: COLORS.bgWhite }}>
      <div className="max-w-[440px] mx-auto min-h-screen relative">
        {/* Back button */}
        <button
          onClick={onClose}
          className="absolute z-10"
          style={{
            top: "28px",
            left: "31px",
            width: "45px",
            height: "45px",
            borderRadius: "50%",
            backgroundColor: COLORS.bgWhite,
            boxShadow: SHADOWS.button,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span style={{ fontSize: "20px" }}>&larr;</span>
        </button>

        {/* Badge row */}
        <div className="flex gap-2" style={{ padding: "217px 47px 0" }}>
          <span
            className="text-xs font-bold px-2 py-1 rounded"
            style={{
              color: "#d80004",
              backgroundColor: COLORS.riskHighBg,
              fontSize: "12px",
              fontWeight: 700,
            }}
          >
            HIGH RISK: {String(highRiskCount).padStart(2, "0")}
          </span>
          <span
            className="text-xs font-bold px-2 py-1 rounded"
            style={{
              color: COLORS.framework,
              backgroundColor: COLORS.frameworkBg,
              fontSize: "12px",
              fontWeight: 700,
            }}
          >
            FRAMEWORK UPDATES: {String(frameworkUpdateCount).padStart(2, "0")}
          </span>
        </div>

        {/* DARK CARD — alert content only */}
        <div
          className="mx-auto mt-4"
          style={{
            marginLeft: "46px",
            marginRight: "46px",
            backgroundColor: COLORS.darkCard,
            borderRadius: RADII.card,
            boxShadow: SHADOWS.darkCard,
            padding: "32px",
          }}
        >
          {/* Framework + pillar label */}
          <p style={{ fontSize: "14px", color: COLORS.bgWhite }}>
            {article.frameworks?.[0]?.split(":")[0] || "GRI"} / {article.esg_pillar === "E" ? "Environmental" : article.esg_pillar === "S" ? "Social" : "Governance"}
          </p>

          {/* Title */}
          <h2
            className="mt-2 font-normal"
            style={{ fontSize: "24px", color: COLORS.bgWhite, letterSpacing: "-0.01em" }}
          >
            {article.title}
          </h2>

          {/* Description */}
          <p className="mt-4" style={{ fontSize: "16px", color: COLORS.bgWhite, lineHeight: "1.5" }}>
            {article.summary}
          </p>

          {/* Metric */}
          {financialAmount && (
            <p className="mt-4" style={{ fontSize: "14px", color: COLORS.textMuted }}>
              Metric: Financial Exposure | Value: {formatCurrency(financialAmount)}
            </p>
          )}

          {/* Separator */}
          <div className="mt-4" style={{ borderTop: "1px solid rgba(255,255,255,0.15)" }} />

          {/* Risk badge + Know More */}
          <div className="mt-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <PriorityBadge level={article.priority_level} />
              <span style={{ fontSize: "12px", color: COLORS.textMuted }}>
                {article.content_type ? article.content_type.charAt(0).toUpperCase() + article.content_type.slice(1) + " Risk" : "Compliance Risk"}
              </span>
            </div>
            <span style={{ fontSize: "14px", color: COLORS.bgWhite }}>Know More</span>
          </div>
        </div>

        {/* OUTSIDE DARK CARD — Executive Insight */}
        <div style={{ padding: "24px 33px 0" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textSecondary }}>
            Executive Insight
          </h3>
          <p className="mt-2" style={{ fontSize: "16px", color: COLORS.textPrimary, lineHeight: "1.5" }}>
            {topScore?.explanation
              ? `${topScore.explanation}. Impact score: ${topScore.impact_score?.toFixed(0)}% with ${topScore.causal_hops} causal hops via ${topScore.relationship_type?.replace(/([A-Z])/g, ' $1').trim()}.${topScore.frameworks?.length ? ` Frameworks affected: ${topScore.frameworks.slice(0, 4).join(', ')}.` : ''}`
              : article.summary || "Analysis pending — tap Ask AI for a detailed executive briefing."}
          </p>

          {/* Separator */}
          <div className="mt-4" style={{ borderTop: `1px solid ${COLORS.textDisabled}` }} />

          {/* Causal Chain Analysis */}
          <h3 className="mt-4" style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textSecondary }}>
            Causal Chain Analysis
          </h3>
        </div>

        {/* Vertical Causal Chain — passes REAL data from API */}
        <div className="mt-4">
          <VerticalCausalChain
            chainPath={topScore?.chain_path ?? undefined}
            explanation={topScore?.explanation ?? undefined}
            relationshipType={topScore?.relationship_type}
            hops={topScore?.causal_hops}
            frameworks={topScore?.frameworks}
            articleTitle={article.title}
          />
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 mt-6" style={{ padding: "0 47px 16px" }}>
          <button
            onClick={onAskAI}
            className="flex-1 text-white font-medium"
            style={{
              backgroundColor: COLORS.darkCard,
              borderRadius: RADII.button,
              padding: "14px 0",
              fontSize: "20px",
              fontWeight: 500,
            }}
          >
            Ask AI
          </button>
          <button
            onClick={onViewArticle}
            className="flex-1 text-white font-medium"
            style={{
              backgroundColor: COLORS.darkCard,
              borderRadius: RADII.button,
              padding: "14px 0",
              fontSize: "20px",
              fontWeight: 500,
            }}
          >
            View Article
          </button>
        </div>
      </div>
    </div>
  );
}
