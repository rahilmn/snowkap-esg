/** Phase 39 — EditorialLede
 *
 * Renders the 2-3 sentence story-style opener that sits above the
 * UnifiedAnalysisCard's 4-bullet structure (in the article-detail sheet)
 * and at the top of the morning_brew newsletter (in the email).
 *
 * Visual treatment mirrors the email — serif italic, generous padding,
 * no decorative label. Same prose, same voice across both surfaces.
 *
 * Returns null when no lede is present, so callers can mount this
 * unconditionally without checking. Pre-Phase-39 articles still on
 * schema 3.2 or earlier render zero markup.
 */
import type { UnifiedAnalysisLede } from "@/types";

interface Props {
  lede: UnifiedAnalysisLede | undefined | null;
}

export function EditorialLede({ lede }: Props) {
  const text = (lede?.text || "").trim();
  if (!text) return null;
  return (
    <div
      aria-label="Editorial lede"
      style={{
        margin: "8px 24px 4px",
        padding: "10px 16px 12px",
      }}
    >
      <p
        style={{
          margin: 0,
          fontFamily: "Georgia, 'Times New Roman', serif",
          fontSize: 17,
          lineHeight: 1.55,
          color: "#0F172A",
          fontStyle: "italic",
          letterSpacing: 0.1,
        }}
      >
        {text}
      </p>
    </div>
  );
}
