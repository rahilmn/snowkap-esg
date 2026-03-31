/**
 * MiniArticleCard — compact card for Home dashboard.
 * Shows: priority + sentiment + title (1 line) + content type + source.
 */

import { COLORS } from "../../lib/designTokens";
import { PriorityBadge } from "../ui/PriorityBadge";
import { formatDate } from "../../lib/utils";
import type { Article } from "../../types";

interface MiniArticleCardProps {
  article: Article;
  onClick?: () => void;
}

export function MiniArticleCard({ article, onClick }: MiniArticleCardProps) {
  const sent = article.sentiment_score;
  const sentColor = sent != null ? (sent > 0.2 ? "#18a87d" : sent < -0.2 ? "#ff4044" : "#888") : "#888";
  const sentArrow = sent != null ? (sent > 0.2 ? "\u25B2" : sent < -0.2 ? "\u25BC" : "\u25CF") : "";

  return (
    <button
      onClick={onClick}
      className="w-full text-left"
      style={{
        padding: "12px 16px",
        backgroundColor: COLORS.bgWhite,
        border: `1px solid ${COLORS.cardBorder}`,
        borderRadius: "8px",
        cursor: "pointer",
        display: "flex",
        alignItems: "flex-start",
        gap: "12px",
      }}
    >
      {/* Left: image thumbnail or pillar dot */}
      {article.image_url ? (
        <img
          src={article.image_url}
          alt=""
          style={{ width: "48px", height: "48px", borderRadius: "6px", objectFit: "cover", flexShrink: 0 }}
        />
      ) : (
        <div
          style={{
            width: "48px",
            height: "48px",
            borderRadius: "6px",
            flexShrink: 0,
            background:
              article.esg_pillar === "E" ? "linear-gradient(135deg, #10b981, #059669)"
              : article.esg_pillar === "S" ? "linear-gradient(135deg, #3b82f6, #2563eb)"
              : "linear-gradient(135deg, #8b5cf6, #7c3aed)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span style={{ color: "#fff", fontSize: "16px", fontWeight: 700, opacity: 0.7 }}>
            {(article.esg_pillar || "E").charAt(0)}
          </span>
        </div>
      )}

      {/* Right: content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Title */}
        <p
          style={{
            fontSize: "14px",
            fontWeight: 500,
            color: COLORS.textPrimary,
            lineHeight: "1.3",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {article.title}
        </p>

        {/* Meta row: priority + sentiment + content type + source */}
        <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "4px", flexWrap: "wrap" }}>
          <PriorityBadge level={article.priority_level} />
          {sentArrow && (
            <span style={{ color: sentColor, fontSize: "10px", fontWeight: 700 }}>
              {sentArrow}{sent != null ? ` ${sent > 0 ? "+" : ""}${sent.toFixed(1)}` : ""}
            </span>
          )}
          {article.content_type && (
            <span style={{ fontSize: "10px", color: COLORS.textMuted, backgroundColor: COLORS.bgLight, padding: "1px 6px", borderRadius: "4px" }}>
              {article.content_type.charAt(0).toUpperCase() + article.content_type.slice(1)}
            </span>
          )}
          {article.source && (
            <span style={{ fontSize: "10px", color: COLORS.textMuted }}>
              {article.source}
            </span>
          )}
          {article.published_at && (
            <span style={{ fontSize: "10px", color: COLORS.textDisabled }}>
              {formatDate(article.published_at)}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}
