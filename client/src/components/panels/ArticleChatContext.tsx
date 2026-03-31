/**
 * ArticleChatContext — Context card shown at top of agent chat when arriving from an article.
 * Displays article metadata and quick-action prompt buttons adapted to riskMode.
 */

import { COLORS, RADII } from "../../lib/designTokens";

interface ArticleChatContextProps {
  articleTitle: string;
  priorityLevel?: string;
  relevanceScore?: number;
  primaryTheme?: string;
  topRiskName?: string;
  topRiskClass?: string;
  riskMode?: string; // "full" or "spotlight"
  frameworkCount?: number;
  onSendPrompt: (prompt: string) => void;
}

const PRIORITY_BADGE_COLORS: Record<
  string,
  { bg: string; text: string }
> = {
  CRITICAL: { bg: "rgba(216, 0, 4, 0.15)", text: "#ff4044" },
  HIGH: { bg: "rgba(216, 0, 4, 0.15)", text: "#ff4044" },
  MEDIUM: { bg: "rgba(223, 89, 0, 0.15)", text: "#df5900" },
  LOW: { bg: "rgba(136, 136, 136, 0.15)", text: "#888888" },
};

const RISK_BADGE_COLORS: Record<string, { bg: string; text: string }> = {
  HIGH: { bg: "rgba(223, 89, 0, 0.15)", text: "#df5900" },
  CRITICAL: { bg: "rgba(216, 0, 4, 0.15)", text: "#ff4044" },
  MODERATE: { bg: "rgba(245, 158, 11, 0.12)", text: "#d97706" },
  LOW: { bg: "rgba(136, 136, 136, 0.12)", text: "#888888" },
};

const HOME_PROMPTS = [
  "What should I prioritize?",
  "Break down the risk matrix",
  "Framework compliance gaps",
  "Compare to recent news",
];

const FEED_PROMPTS = [
  "Run full risk analysis",
  "Which frameworks apply?",
  "Should this be on my radar?",
  "How does this affect us?",
];

function MetadataPill({
  label,
  bg,
  color,
}: {
  label: string;
  bg: string;
  color: string;
}) {
  return (
    <span
      style={{
        fontSize: "11px",
        fontWeight: 600,
        padding: "3px 8px",
        borderRadius: RADII.pill,
        backgroundColor: bg,
        color,
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

export function ArticleChatContext({
  articleTitle,
  priorityLevel,
  relevanceScore,
  primaryTheme,
  topRiskName,
  topRiskClass,
  riskMode,
  frameworkCount,
  onSendPrompt,
}: ArticleChatContextProps) {
  const prompts = riskMode === "full" ? HOME_PROMPTS : FEED_PROMPTS;
  const priorityColors =
    priorityLevel && PRIORITY_BADGE_COLORS[priorityLevel.toUpperCase()]
      ? PRIORITY_BADGE_COLORS[priorityLevel.toUpperCase()]
      : null;
  const riskColors =
    topRiskClass && RISK_BADGE_COLORS[topRiskClass.toUpperCase()]
      ? RISK_BADGE_COLORS[topRiskClass.toUpperCase()]
      : null;

  return (
    <div
      style={{
        backgroundColor: COLORS.darkCard,
        borderRadius: RADII.card,
        padding: "16px",
      }}
    >
      {/* Top section */}
      <div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 500,
            color: COLORS.textMuted,
            textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}
        >
          Analyzing:
        </span>
        <p
          style={{
            fontSize: "14px",
            fontWeight: 600,
            color: "#ffffff",
            margin: "4px 0 10px 0",
            lineHeight: "1.4",
            overflow: "hidden",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
          }}
        >
          {articleTitle}
        </p>

        {/* Metadata pills */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            flexWrap: "wrap",
          }}
        >
          {priorityLevel && priorityColors && (
            <MetadataPill
              label={priorityLevel}
              bg={priorityColors.bg}
              color={priorityColors.text}
            />
          )}
          {relevanceScore != null && (
            <MetadataPill
              label={`${relevanceScore}/10`}
              bg="rgba(223, 89, 0, 0.12)"
              color={COLORS.brand}
            />
          )}
          {primaryTheme && (
            <MetadataPill
              label={primaryTheme}
              bg="rgba(255, 255, 255, 0.08)"
              color="#ffffff"
            />
          )}
          {topRiskName && riskColors && (
            <MetadataPill
              label={topRiskName}
              bg={riskColors.bg}
              color={riskColors.text}
            />
          )}
          {frameworkCount != null && frameworkCount > 0 && (
            <MetadataPill
              label={`${frameworkCount} frameworks`}
              bg="rgba(14, 151, 231, 0.12)"
              color={COLORS.framework}
            />
          )}
        </div>
      </div>

      {/* Divider */}
      <div
        style={{
          height: "1px",
          backgroundColor: "rgba(255, 255, 255, 0.1)",
          margin: "14px 0",
        }}
      />

      {/* Bottom section: Quick Actions */}
      <div>
        <span
          style={{
            fontSize: "11px",
            fontWeight: 500,
            color: COLORS.textMuted,
            textTransform: "uppercase",
            letterSpacing: "0.5px",
            marginBottom: "10px",
            display: "block",
          }}
        >
          Quick Actions
        </span>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "8px",
          }}
        >
          {prompts.map((prompt) => (
            <button
              key={prompt}
              onClick={() => onSendPrompt(prompt)}
              style={{
                fontSize: "12px",
                fontWeight: 500,
                color: "#ffffff",
                backgroundColor: "transparent",
                border: "1px solid rgba(255, 255, 255, 0.18)",
                borderRadius: RADII.button,
                padding: "9px 8px",
                cursor: "pointer",
                textAlign: "center",
                lineHeight: "1.35",
                transition: "background-color 0.15s, border-color 0.15s",
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget as HTMLButtonElement;
                el.style.backgroundColor = "rgba(255, 255, 255, 0.08)";
                el.style.borderColor = "rgba(255, 255, 255, 0.3)";
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget as HTMLButtonElement;
                el.style.backgroundColor = "transparent";
                el.style.borderColor = "rgba(255, 255, 255, 0.18)";
              }}
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
