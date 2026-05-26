/**
 * Phase 34.1 — Power-of-Now design tokens.
 *
 * Ported from `Power of Now UI/app.css` + `article-detail.jsx::HERO_TINTS`.
 * Coexists with the original `designTokens.ts` (which the Phase 27 desktop
 * stack still consumes) — new mobile-first surfaces (`/now`, `/welcome/*`,
 * `/wiki`, `/forum`) read from this module.
 *
 * Tokens kept tight + extracted from the Power of Now files literally so a
 * future designer can diff `app.css` vs this module and see exact parity.
 */

// ----- Brand + ink palette (verbatim from `Power of Now UI/app.css:1-24`) -----

export const TOKENS = {
  brand: "#df5900",
  brandSoft: "#ffece1",
  brandDeep: "#b94400",

  ink: "#0f1115",
  ink2: "#2a2d33",
  ink3: "#5a5f68",
  ink4: "#8a8f98",

  line: "#ececef",
  line2: "#f2f2f4",

  bg: "#ffffff",
  bgSoft: "#f7f7f8",
  bgCard: "#fafafa",

  tintBlue: "#cfe7ee",
  tintMint: "#d4ecdc",

  critical: "#c6361b",
  criticalBg: "#fdebe6",
  high: "#d97706",
  highBg: "#fdf2dc",
  medium: "#5b6470",
  mediumBg: "#eef0f3",
  positive: "#1b8a3b",
  positiveBg: "#e3f4e7",
} as const;

// ----- Hero tint gradients per category (verbatim from `article-detail.jsx:6-13`) -----

const HERO_TINTS: Record<string, [string, string]> = {
  "GRI / Social":         ["#fceae1", "#f5d4be"],
  "TCFD / Climate":       ["#dbe8ef", "#bcd2de"],
  "Strategy / Markets":   ["#e8e4dd", "#d2cab8"],
  "Capital / Disclosure": ["#e4e1ec", "#cdc7da"],
  "CSRD / Reporting":     ["#deebe1", "#bedac3"],
  "GRI / Environment":    ["#e6dfd1", "#d0c39e"],
};

/**
 * Return a 135° linear-gradient string for a category label. Falls back
 * to a neutral grey gradient for unrecognised categories (rather than a
 * solid color so the visual rhythm holds).
 *
 * @example
 *   <div style={{ background: categoryTint("TCFD / Climate") }} />
 *   // ➜ "linear-gradient(135deg, #dbe8ef 0%, #bcd2de 100%)"
 */
export function categoryTint(category: string | null | undefined): string {
  const [from, to] = HERO_TINTS[category || ""] ?? ["#eef0f3", "#dde1e6"];
  return `linear-gradient(135deg, ${from} 0%, ${to} 100%)`;
}

/**
 * The same map, exposed so a designer / harness can iterate without
 * re-importing the constant.
 */
export const HERO_TINT_KEYS = Object.keys(HERO_TINTS);

// ----- Compliance pill levels (verbatim from `app.css:79-89`) -----

export type ComplianceLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "POSITIVE";

export function pillTokens(level: ComplianceLevel): { bg: string; fg: string } {
  switch (level) {
    case "CRITICAL": return { bg: TOKENS.criticalBg, fg: TOKENS.critical };
    case "HIGH":     return { bg: TOKENS.highBg,     fg: TOKENS.high };
    case "MEDIUM":   return { bg: TOKENS.mediumBg,   fg: TOKENS.medium };
    case "LOW":      return { bg: TOKENS.mediumBg,   fg: TOKENS.medium };
    case "POSITIVE": return { bg: TOKENS.positiveBg, fg: TOKENS.positive };
  }
}

// ----- Font stacks -----

export const FONT_SERIF =
  '"Fraunces", "Source Serif Pro", Georgia, serif';
export const FONT_SANS =
  '"Inter", -apple-system, system-ui, sans-serif';
export const FONT_MONO =
  '"JetBrains Mono", ui-monospace, monospace';

// ----- Tap interaction (mirror of `app.css:110-111`) -----

export const TAP_TRANSITION = "transform 0.12s ease, opacity 0.12s ease";
export const TAP_ACTIVE_STYLE = "scale(0.97)";
