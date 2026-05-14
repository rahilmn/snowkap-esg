/** Phase 6 §8.3 — "Outside your focus" badge.
 *
 * Renders ONLY when the article has zero overlap with the persona's
 * esg_focus AND the API returned the persona-modulated payload (so
 * `outside_focus === true`).
 *
 * Design intent (per plan §8): a CRITICAL article that doesn't match
 * the persona's stated ESG focus still surfaces (CRITICAL floor) — but
 * the badge tells the reader "we know this isn't normally your area;
 * we're showing it because it's high-impact." Avoids the user thinking
 * the feed is broken when an off-focus item appears.
 */
import { COLORS } from "@/lib/designTokens";

interface Props {
  outsideFocus: boolean | undefined;
  /** Visual weight: "subtle" for inline use in card metadata rows;
   * "prominent" for hero / detail sheets. */
  variant?: "subtle" | "prominent";
  className?: string;
}

export function OutsideFocusBadge({
  outsideFocus,
  variant = "subtle",
  className,
}: Props) {
  if (!outsideFocus) return null;

  const isProminent = variant === "prominent";

  return (
    <span
      title="This article is outside your usual ESG focus, but its CRITICAL impact made it surface anyway."
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: isProminent ? "4px 10px" : "2px 8px",
        fontSize: isProminent ? 11 : 10,
        fontWeight: 600,
        letterSpacing: "0.02em",
        textTransform: "uppercase",
        borderRadius: 999,
        color: COLORS.brand,
        background: COLORS.brandLight,
        border: `1px solid ${COLORS.brand}`,
        whiteSpace: "nowrap",
      }}
      className={className}
    >
      Outside your focus
    </span>
  );
}

export default OutsideFocusBadge;
