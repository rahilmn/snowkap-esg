/**
 * UX Design System Tokens — Phase 3A
 * Central source of truth derived from D:\ClaudePowerofnow\UX\ Figma exports.
 * Pixel-perfect at 440px mobile viewport.
 */

export const COLORS = {
  // Brand
  brand: "#df5900",
  brandLight: "rgba(223, 89, 0, 0.15)",

  // Risk levels
  riskHigh: "#ff4044",
  riskHighBg: "rgba(216, 0, 4, 0.15)",
  elevated: "#18a87d",
  elevatedBg: "rgba(24, 168, 125, 0.15)",
  framework: "#0e97e7",
  frameworkBg: "rgba(14, 151, 231, 0.15)",

  // Dark card mode (Home-Popup)
  darkCard: "#080707",
  darkCardShadow1: "#6e6e6e",
  darkCardShadow2: "#b0b0b0",

  // Text
  textPrimary: "#111111",
  textSecondary: "#888888",
  textMuted: "#999999",
  textDisabled: "#d9d9d9",

  // Card layers (3-depth effect)
  cardBg: "#ffffff",
  cardBorder: "#efefef",
  cardStack1: "#f4f4f4",
  cardStack2: "#e4e4e4",

  // Backgrounds
  bgWhite: "#ffffff",
  bgLight: "#f7f9fb",
  bgGradientEnd: "#e1f6ff",

  // Opportunity (green node in causal chain)
  opportunity: "#18a87d",
  opportunityBg: "rgba(24, 168, 125, 0.15)",
} as const;

export const SHADOWS = {
  card: "0px 10px 30px 0px rgba(102, 123, 136, 0.15)",
  darkCard: "0px 10px 30px 0px rgba(0, 0, 0, 0.12)",
  button: "0px 4px 4px 0px rgba(0, 0, 0, 0.12)",
} as const;

export const RADII = {
  card: "8px",
  pill: "40px",
  button: "5px",
  circle: "50%",
} as const;

export const SPACING = {
  contentMargin: "47px",
  topPadding: "62px",
  logoSize: "40px",
} as const;

// Priority level → color mapping (from UX risk badges)
export const PRIORITY_COLORS: Record<
  string,
  { text: string; bg: string; border: string }
> = {
  CRITICAL: {
    text: "#ff4044",
    bg: "rgba(216, 0, 4, 0.15)",
    border: "#ff4044",
  },
  HIGH: { text: "#ff4044", bg: "rgba(216, 0, 4, 0.15)", border: "#ff4044" },
  MEDIUM: {
    text: "#df5900",
    bg: "rgba(223, 89, 0, 0.15)",
    border: "#df5900",
  },
  LOW: {
    text: "#888888",
    bg: "rgba(136, 136, 136, 0.15)",
    border: "#888888",
  },
};

// Content type labels for UI display
export const CONTENT_TYPE_LABELS: Record<string, string> = {
  regulatory: "Regulatory",
  financial: "Financial",
  operational: "Operational",
  reputational: "Reputational",
  technical: "Technical",
  narrative: "Narrative",
  data_release: "Data Release",
};
