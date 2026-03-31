/**
 * Phase 3F: CRITICAL/HIGH/MEDIUM/LOW priority badge.
 * Colors matched to UX/Home/Home.html risk badge specs.
 */

import { PRIORITY_COLORS } from "../../lib/designTokens";

interface PriorityBadgeProps {
  level: string | null | undefined;
  className?: string;
}

export function PriorityBadge({ level, className = "" }: PriorityBadgeProps) {
  if (!level) return null;

  const upperLevel = level.toUpperCase();
  const colors = PRIORITY_COLORS[upperLevel] ?? PRIORITY_COLORS["LOW"]!;

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs font-bold rounded ${className}`}
      style={{
        color: colors.text,
        backgroundColor: colors.bg,
        border: `1px solid ${colors.border}`,
        borderRadius: "5px",
        fontSize: "12px",
        fontWeight: 700,
        letterSpacing: "-0.01em",
      }}
    >
      {upperLevel}
    </span>
  );
}
