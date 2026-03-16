/** FOMO tag logic for news cards — Stage 6.4 */

export type FomoTag = "BREAKING" | "URGENT" | "TRENDING" | "NEW" | null;

export interface FomoInfo {
  tag: FomoTag;
  color: string;
  bgColor: string;
}

/**
 * Compute the FOMO tag for a news article.
 * Priority: BREAKING > URGENT > TRENDING > NEW > null
 */
export function computeFomoTag(
  publishedAt: string | null,
  impactScore: number,
): FomoInfo {
  const now = Date.now();
  const published = publishedAt ? new Date(publishedAt).getTime() : 0;
  const ageHours = published ? (now - published) / (1000 * 60 * 60) : Infinity;

  // BREAKING: < 1 hour old + score >= 70
  if (ageHours < 1 && impactScore >= 70) {
    return { tag: "BREAKING", color: "text-red-700", bgColor: "bg-red-100" };
  }

  // URGENT: score >= 80 (any age)
  if (impactScore >= 80) {
    return { tag: "URGENT", color: "text-orange-700", bgColor: "bg-orange-100" };
  }

  // TRENDING: score >= 60
  if (impactScore >= 60) {
    return { tag: "TRENDING", color: "text-amber-700", bgColor: "bg-amber-100" };
  }

  // NEW: < 4 hours old
  if (ageHours < 4) {
    return { tag: "NEW", color: "text-blue-700", bgColor: "bg-blue-100" };
  }

  return { tag: null, color: "", bgColor: "" };
}
