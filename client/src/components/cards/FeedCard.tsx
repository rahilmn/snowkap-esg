/**
 * Phase 3E: List-style feed card matching UX/Feed/Feed.html.
 * Thumbnail (140x107) left + title/badge/source right.
 */

import { COLORS, RADII } from "../../lib/designTokens";
import { PriorityBadge } from "../ui/PriorityBadge";

interface FeedCardProps {
  title: string;
  source?: string | null;
  imageUrl?: string | null;
  esgPillar?: string | null;
  priorityLevel?: string | null;
  contentType?: string | null;
  onClick?: () => void;
}

const PILLAR_LABELS: Record<string, string> = {
  E: "Environmental",
  S: "Social",
  G: "Governance",
};

export function FeedCard({
  title,
  source: _source,
  imageUrl,
  esgPillar,
  priorityLevel,
  contentType,
  onClick,
}: FeedCardProps) {
  void _source;
  return (
    <div
      className="flex gap-3 cursor-pointer"
      onClick={onClick}
      style={{ padding: "0 47px" }}
    >
      {/* Thumbnail */}
      <div
        className="flex-shrink-0 overflow-hidden"
        style={{
          width: "140px",
          height: "107px",
          borderRadius: RADII.card,
          backgroundColor: COLORS.textDisabled,
        }}
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt=""
            className="w-full h-full object-cover"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{
              background:
                esgPillar === "E"
                  ? "linear-gradient(135deg, #10b981, #059669)"
                  : esgPillar === "S"
                    ? "linear-gradient(135deg, #3b82f6, #2563eb)"
                    : "linear-gradient(135deg, #8b5cf6, #7c3aed)",
            }}
          >
            <span className="text-white text-xs font-bold opacity-60">
              {PILLAR_LABELS[esgPillar || ""] || "ESG"}
            </span>
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p
          style={{
            fontSize: "13px",
            color: COLORS.textMuted,
            letterSpacing: "-0.01em",
          }}
        >
          {esgPillar ? PILLAR_LABELS[esgPillar] || esgPillar : "ESG"}
        </p>
        <p
          className="mt-1 line-clamp-2 font-medium"
          style={{
            fontSize: "14px",
            color: COLORS.textPrimary,
            letterSpacing: "-0.01em",
            fontWeight: 500,
          }}
        >
          {title}
        </p>
        <div className="mt-2 flex items-center gap-2">
          <PriorityBadge level={priorityLevel} />
          {contentType && (
            <span
              style={{
                fontSize: "12px",
                color: COLORS.textSecondary,
              }}
            >
              {contentType.charAt(0).toUpperCase() + contentType.slice(1)} Risk
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
