/**
 * Phase 34.3 — One news card in the SwipeDeck.
 *
 * Pulls visual primitives from `Power of Now UI/swipe-deck.jsx::CardBody`
 * (hero gradient + framework chip + serif headline + deck + footer
 * metric stripe) but reads from the Snowkap `Article` type instead of
 * the mock data shape.
 *
 * Field mapping (Snowkap → Power of Now visual):
 *   article.title                    → headline (serif)
 *   article.summary or insight.analysis.what_changed.headline  → deck
 *   article.esg_pillar + framework[0] → category chip
 *   article.source                   → source chip
 *   criticality_band                 → CRITICAL / HIGH / MEDIUM pill
 *   insight.analysis.why_it_matters.financial_exposure → footer metric
 */
import { useState } from "react";
import type { Article } from "@/types";
import { categoryTint, pillTokens } from "@/lib/designTokensV2";

interface UnifiedAnalysis {
  what_changed?: { headline?: string; polarity?: string };
  why_it_matters?: {
    materiality_band?: string;
    criticality_summary?: string;
    // Phase 56.M — a short FOMO hook shown on the deck card (the card summary
    // is intentionally empty on the feed; this fills it). Curated; falls back
    // to criticality_summary.
    card_teaser?: string;
    financial_exposure?: { amount_cr?: number; label?: string; kind?: string; source?: string };
  };
  headline_only?: boolean;
  body_char_count?: number;
}

// Phase 56.M — plain-English meaning of each criticality band, shown in a
// tap-to-reveal legend on the band pill.
const _BAND_LEGEND: Record<string, string> = {
  CRITICAL: "Act now — high materiality to your company.",
  HIGH: "Review soon — likely material.",
  MEDIUM: "Moderate materiality — review before your next reporting cycle.",
  LOW: "Monitor — nothing to act on yet.",
};

function _cleanTitle(raw: string): string {
  if (!raw) return "";
  // Strip noisy publisher suffix patterns common to Google News scrapes:
  // "Headline - Publisher.com", "Headline | Publisher", "Headline – Source"
  return raw
    .replace(/\s*[\-–|]\s*[A-Za-z0-9 .,'&]{2,40}(?:\.com|\.in|\.co|\.org)?\s*$/i, "")
    .trim();
}

function _headline(article: Article): string {
  const di = article.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  // Prefer the LLM-generated clean headline so the deck never shows
  // raw publisher-suffixed garbage like "Infosys : Financial document
  // - (csr impact assessment reports2025 26) - marketscreener.com".
  const llmHeadline = di?.analysis?.what_changed?.headline?.trim();
  if (llmHeadline) return llmHeadline;
  return _cleanTitle(article.title || "");
}

function _freshness(article: Article): { label: string; dot: string } {
  const raw = article.published_at;
  if (!raw) return { label: "Just in", dot: "#cbd5e1" };
  const t = new Date(raw).getTime();
  if (!isFinite(t)) return { label: "Just in", dot: "#cbd5e1" };
  const ageMs = Date.now() - t;
  const hours = ageMs / 3_600_000;
  let label: string;
  if (hours < 1) label = "Just in";
  else if (hours < 24) label = `${Math.round(hours)}h ago`;
  else if (hours < 24 * 14) label = `${Math.round(hours / 24)}d ago`;
  else label = `${Math.round(hours / (24 * 7))}w ago`;
  // Green dot for <48h (live), grey otherwise.
  const dot = hours < 48 ? "#1b8a3b" : "#94a3b8";
  return { label, dot };
}

interface Props {
  article: Article;
  bookmarked?: boolean;
}

function _category(article: Article): string {
  // Best-effort: framework prefix + pillar (e.g. "GRI / Social", "TCFD / Climate").
  // Fall back to a neutral category when nothing's set.
  const pillarMap: Record<string, string> = {
    E: "Environment",
    S: "Social",
    G: "Governance",
  };
  const framework = (article.frameworks?.[0] || "").split(":")[0] || "ESG";
  const pillar = pillarMap[(article.esg_pillar || "").toUpperCase()] || "";
  if (pillar && framework) return `${framework} / ${pillar}`;
  return framework || "ESG";
}

function _band(article: Article): "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" {
  const di = article.deep_insight as { analysis?: UnifiedAnalysis; criticality?: { band?: string } } | undefined;
  const fromAnalysis = di?.analysis?.why_it_matters?.materiality_band;
  const fromCrit = di?.criticality?.band;
  const band = (fromAnalysis || fromCrit || article.criticality_band || "MEDIUM").toString().toUpperCase();
  if (band === "CRITICAL" || band === "HIGH" || band === "MEDIUM" || band === "LOW") return band;
  return "MEDIUM";
}

function _teaser(article: Article): string {
  // Phase 56.M — the FOMO hook on the deck card. The feed hardcodes
  // article.summary="" so the card body is otherwise blank; show a short,
  // curated teaser (falls back to the "how it impacts" line).
  const di = article.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  const wim = di?.analysis?.why_it_matters;
  const curated = (wim?.card_teaser || "").trim();
  if (curated) return curated;
  return (wim?.criticality_summary || "").trim().slice(0, 180);
}

function _metric(article: Article): { label: string; value: string } | null {
  const di = article.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  const exposure = di?.analysis?.why_it_matters?.financial_exposure;
  if (!exposure) return null;
  // Phase 47.R — non-financial event: hide the exposure chip entirely
  // on the deck card. Earlier we showed "₹16.9 Cr (engine estimate)"
  // on articles whose body had no monetary content — misleading at
  // the deck-card glance density. Better to show no chip than a
  // fabricated figure.
  if (exposure.kind === "non_financial_event") {
    return null;
  }
  const label = "Exposure";
  let value = "";
  if (exposure.amount_cr != null) {
    value = `~₹${Number(exposure.amount_cr).toLocaleString("en-IN")} Cr`;
  } else if (exposure.label) {
    value = exposure.label
      .replace(/\s*\([^)]*\)\s*/g, "")
      .split(/[,.]/)[0]
      ?.trim() || exposure.label;
  }
  if (!value) return null;
  return { label, value };
}

function _isHeadlineOnly(article: Article): boolean {
  const di = article.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  return !!di?.analysis?.headline_only;
}

export function SwipeCard({ article, bookmarked }: Props) {
  const category = _category(article);
  const band = _band(article);
  const pill = pillTokens(band);
  const metric = _metric(article);
  const teaser = _teaser(article);
  const headline = _headline(article);
  const freshness = _freshness(article);
  const headlineOnly = _isHeadlineOnly(article);
  const [legendOpen, setLegendOpen] = useState(false);
  // Stop the swipe-deck pointer handlers from capturing taps on the legend.
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

  return (
    <div style={{ position: "relative", height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Hero gradient band */}
      <div style={{
        position: "relative",
        height: 130,
        background: categoryTint(category),
        flex: "0 0 auto",
      }}>
        {/* Hero image overlay — only rendered when a real image URL exists */}
        {article.image_url && (
          <div
            style={{
              position: "absolute", inset: 0,
              mixBlendMode: "multiply", opacity: 0.55,
              backgroundImage: `url(${article.image_url})`,
              backgroundSize: "cover", backgroundPosition: "center",
            }}
          />
        )}
        {/* top chip row: category + source */}
        <div style={{
          position: "absolute", top: 12, left: 14, right: 14,
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div style={{
            background: "rgba(255,255,255,0.92)", backdropFilter: "blur(6px)",
            padding: "5px 10px", borderRadius: 999,
            fontSize: 10.5, fontWeight: 600, color: "#2a2d33",
            letterSpacing: "0.02em",
          }}>{category}</div>
          {article.source && (
            <div style={{
              background: "rgba(255,255,255,0.92)", backdropFilter: "blur(6px)",
              padding: "5px 10px", borderRadius: 999,
              fontSize: 10.5, fontWeight: 500, color: "#5a5f68",
              letterSpacing: "0.02em",
              maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>{article.source}</div>
          )}
        </div>
        {/* bookmark ribbon */}
        {bookmarked && (
          <div style={{
            position: "absolute", top: 0, right: 24,
            width: 28, height: 38,
            background: "#df5900",
            clipPath: "polygon(0 0, 100% 0, 100% 100%, 50% 78%, 0 100%)",
            boxShadow: "0 4px 10px rgba(223,89,0,0.3)",
            display: "flex", alignItems: "flex-start", justifyContent: "center",
            paddingTop: 6, color: "white",
          }}>
            <svg width="12" height="14" viewBox="0 0 12 14" fill="currentColor">
              <path d="M1 1h10v12L6 10l-5 3V1z"/>
            </svg>
          </div>
        )}
      </div>

      {/* meta row */}
      <div style={{
        padding: "14px 18px 0",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 8,
      }}>
        <div style={{
          fontSize: 10.5, color: "#8a8f98", fontWeight: 600, letterSpacing: "0.04em",
          display: "flex", alignItems: "center", gap: 6, minWidth: 0,
        }}>
          {/* Phase 56.M — the framework/pillar tag was removed here; it
              duplicated the top-left "ESG / Pillar" category chip. */}
          {/* Transparency cue: when the article body couldn't be scraped
              (paywall / JS SPA / 403), every specific figure in the
              analysis is an engine projection rather than an article
              fact. Showing this on the card itself keeps the engine's
              honesty visible at scan-time. */}
          {headlineOnly && (
            <span
              title="Full article body unavailable — specifics in the analysis are engine projections"
              style={{
                padding: "2px 8px", borderRadius: 999,
                background: "#fef3c7", color: "#92400e",
                fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em",
                textTransform: "uppercase",
                cursor: "help",
                whiteSpace: "nowrap",
              }}>
              Headline-only
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {/* Phase 51 — light-tier chip so the 7 watchlist cards read distinctly
              from the 3 priority criticals at a glance. */}
          {(article as { now_tier?: string }).now_tier === "light" && (
            <span
              title="A Stage 1-9 watchlist read; full ₹ analysis runs on the 3 priority briefs"
              style={{
                padding: "2px 8px", borderRadius: 999,
                background: "rgba(14,58,95,0.08)", color: "#5b6b7b",
                fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em",
                textTransform: "uppercase", whiteSpace: "nowrap",
              }}>
              Quick read
            </span>
          )}
          {/* Phase 56.M — tap the band to reveal a plain-English legend. */}
          <span
            className={`pon-pill ${band.toLowerCase()}`}
            onClick={(e) => { stop(e); setLegendOpen((v) => !v); }}
            onPointerDown={stop}
            title="What does this mean?"
            style={{
              background: pill.bg, color: pill.fg, cursor: "pointer",
              display: "inline-flex", alignItems: "center", gap: 4,
            }}>
            {band}
            <span style={{ fontSize: 9, fontWeight: 800, opacity: 0.65 }}>ⓘ</span>
          </span>
        </div>
      </div>

      {/* Phase 56.M — band legend popover (tap the pill to open). */}
      {legendOpen && (
        <>
          <div
            onPointerDown={(e) => { stop(e); setLegendOpen(false); }}
            onClick={stop}
            style={{ position: "absolute", inset: 0, zIndex: 18, background: "transparent" }}
          />
          <div
            onPointerDown={stop}
            style={{
              position: "absolute", top: 168, right: 14, zIndex: 19,
              width: 232, padding: "10px 12px",
              background: "#ffffff", borderRadius: 12,
              border: "1px solid #ececef",
              boxShadow: "0 8px 24px rgba(15,17,21,0.16)",
            }}>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "#8a8f98", marginBottom: 6 }}>
              Priority levels
            </div>
            {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((b) => {
              const p = pillTokens(b);
              return (
                <div key={b} style={{ display: "flex", gap: 8, alignItems: "flex-start", margin: "5px 0" }}>
                  <span className={`pon-pill ${b.toLowerCase()}`} style={{
                    background: p.bg, color: p.fg, flex: "0 0 auto",
                    fontWeight: b === band ? 800 : 600,
                    outline: b === band ? "1.5px solid rgba(15,17,21,0.25)" : "none",
                  }}>{b}</span>
                  <span style={{ fontSize: 11.5, lineHeight: 1.4, color: "#5a5f68" }}>
                    {_BAND_LEGEND[b]}
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Headline — prefers the LLM-generated clean headline; falls back to a
          publisher-suffix-stripped raw title. Clamped to 4 lines with a hard
          maxHeight so a long headline can't leak a sliver of the next line
          ("…Bank / branch") or push the footer past the card's bound. */}
      <div className="serif" style={{
        padding: "8px 18px 0",
        fontSize: 20, lineHeight: 1.22,
        fontWeight: 600, color: "#0f1115",
        display: "-webkit-box",
        WebkitBoxOrient: "vertical",
        WebkitLineClamp: 4,
        overflow: "hidden",
        maxHeight: "98px",
      }}>
        {headline}
      </div>

      {/* Phase 56.M — FOMO teaser: a short hook telling the reader what this
          story is + why it matters, since the card body is otherwise blank.
          Clamped to 3 lines. */}
      {teaser && (
        <div style={{
          padding: "6px 18px 0",
          fontSize: 14, lineHeight: 1.45,
          color: "#5a5f68",
          display: "-webkit-box",
          WebkitBoxOrient: "vertical",
          WebkitLineClamp: 3,
          overflow: "hidden",
        }}>
          {teaser}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 8 }}/>

      {/* Footer — metric + received-by stripe */}
      <div style={{
        padding: "14px 18px 18px",
        borderTop: "1px solid #f2f2f4",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 10,
      }}>
        {metric ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, maxWidth: "70%" }}>
            <span style={{ fontSize: 9.5, fontWeight: 600, color: "#8a8f98", letterSpacing: "0.08em", textTransform: "uppercase" }}>
              {metric.label}
            </span>
            <span className="serif" style={{
              fontSize: 18, fontWeight: 600, color: "#0f1115",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {metric.value}
            </span>
          </div>
        ) : (
          /* Phase 56.M — when there's no ₹ exposure, the footer was empty.
             Show the swipe affordance so the reader knows to go deeper. */
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 12, fontWeight: 700, color: "#df5900", letterSpacing: "0.01em",
          }}>
            <span style={{ fontSize: 14, lineHeight: 1 }}>↑</span>
            Swipe up to read the full brief
          </span>
        )}
        {/* Freshness: replaces the meaningless "Live signal" label with
            an actual time-since-publish. Green dot when <48h (recent),
            grey otherwise. Hover shows the full timestamp. */}
        <div
          title={article.published_at ? `Published ${new Date(article.published_at).toLocaleString()}` : ""}
          style={{
            fontSize: 11, color: "#8a8f98", letterSpacing: "0.02em",
            display: "flex", alignItems: "center", gap: 6,
            cursor: "default",
          }}>
          <span style={{ width: 6, height: 6, borderRadius: 3, background: freshness.dot }}/>
          {freshness.label}
        </div>
      </div>
    </div>
  );
}
