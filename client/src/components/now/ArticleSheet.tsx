/**
 * Phase 34.4 — Mobile-first article sheet.
 *
 * Replaces the legacy Phase-33 ArticleDetailSheet for the `/now` swipe
 * deck (the desktop `/home` route keeps the full Phase-33 sheet).
 *
 * Per the user's spec:
 *   "Once the news has been read, below the news is a simple dialogue
 *    box that summarizes all intelligence the app gathers to explain in
 *    simple narrative why the news matters to the person and their
 *    company, and how, and what they should do about it. Further below
 *    is a button to request a detailed technical report on their email.
 *    And below that the comment space starts."
 *
 * Layout (top → bottom):
 *   1. Sticky header (back arrow + bookmark + share)
 *   2. Hero gradient band (category tint) + headline + source pill
 *   3. NarrativeBlock — 3-paragraph dialogue box
 *      (Why this matters to YOU · How it impacts YOUR business · What to do)
 *   4. TechReportStripe — "Email me the detailed report" button
 *   5. CommentsPlaceholder — to be replaced in Phase 34.5
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Article } from "@/types";
import { TOKENS, categoryTint, pillTokens } from "@/lib/designTokensV2";
import { articleEmail, news } from "@/lib/api";
import { CommentThread } from "@/components/now/CommentThread";

interface UnifiedAnalysis {
  what_changed?: { headline?: string; polarity?: string; source?: string };
  why_it_matters?: {
    materiality_band?: string;
    criticality_summary?: string;
    stakes_for_company?: string;
    financial_exposure?: { amount_cr?: number; label?: string };
  };
  what_it_triggers?: {
    recommended_actions?: Array<{
      title?: string;
      deadline?: string;
      owner?: string;
    }>;
  };
  what_to_watch?: {
    top_risk_categories?: string[];
  };
  headline_only?: boolean;
  body_char_count?: number;
}

interface Props {
  article: Article;
  open: boolean;
  bookmarked: boolean;
  onClose: () => void;
  onBookmarkToggle: () => void;
}

function _category(article: Article): string {
  const pillarMap: Record<string, string> = { E: "Environment", S: "Social", G: "Governance" };
  const framework = (article.frameworks?.[0] || "").split(":")[0] || "ESG";
  const pillar = pillarMap[(article.esg_pillar || "").toUpperCase()] || "";
  if (pillar && framework) return `${framework} / ${pillar}`;
  return framework;
}

export function ArticleSheet({ article, open, bookmarked, onClose, onBookmarkToggle }: Props) {
  const [emailState, setEmailState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [emailError, setEmailError] = useState<string>("");
  const [recipientsInput, setRecipientsInput] = useState<string>("");
  const [extrasSent, setExtrasSent] = useState<number>(0);
  const [extrasFailed, setExtrasFailed] = useState<number>(0);
  const [showRecipients, setShowRecipients] = useState<boolean>(false);

  // Phase 34.4 — when the SwipeDeck supplies a lean Article (live-feed
  // items have `deep_insight: null`), fetch the full insight on open.
  // React Query caches per article_id so re-opening is free.
  const qc = useQueryClient();
  const triggeredRef = useRef<Set<string>>(new Set());
  const analysisQuery = useQuery({
    queryKey: ["now-article-analysis", article.id],
    queryFn: async () => {
      const res = await news.getAnalysisStatus(article.id);
      return res;
    },
    enabled: open,
    staleTime: 60_000 * 5,
    retry: false,
    // Poll while the article sheet is open AND we don't yet have a
    // populated analysis. Stops polling automatically once the deep
    // insight headline lands, or when the user closes the sheet.
    refetchInterval: (q) => {
      const data = q.state.data as { status?: string; analysis?: { deep_insight?: { analysis?: { what_changed?: { headline?: string } } } } } | undefined;
      const hasContent = !!data?.analysis?.deep_insight?.analysis?.what_changed?.headline;
      if (hasContent) return false;
      // No content yet → keep polling. 6s cadence balances responsiveness
      // (median enrichment ~45s) against API load.
      return 6000;
    },
  });

  // When the article hasn't been indexed yet OR the API returned an idle
  // status, kick off a fresh on-demand enrichment ONCE per article id and
  // poll for the result. Without this, live-feed articles render the
  // empty state and never recover. The error path covers 404 (article
  // not in `article_index` yet) — `request<T>` throws the API's `detail`
  // string, so we detect "not in index" / "no analysis" / "not found"
  // / 404-status-code generically.
  // Auto-trigger condition — the key heuristic is "do we have a real
  // analysis headline to render?". If not, fire on-demand enrichment
  // regardless of what status flag the server claimed. Pre-fix the
  // code only fired on 404 / "idle" / "pending"; for stub articles
  // surfaced by the strict-10 deck the server returns 200 with empty
  // analysis, so none of those flags tripped and the trigger silently
  // skipped — leaving the user stuck on the empty-state message.
  const fetchedAnalysis = (analysisQuery.data?.analysis?.deep_insight as { analysis?: UnifiedAnalysis } | undefined)?.analysis;
  const passedAnalysis = (article.deep_insight as { analysis?: UnifiedAnalysis } | undefined)?.analysis;
  const effectiveAnalysis = fetchedAnalysis || passedAnalysis;
  const hasRealAnalysis = !!(effectiveAnalysis?.what_changed?.headline);
  useEffect(() => {
    if (!open) return;
    if (analysisQuery.isLoading) return;
    if (triggeredRef.current.has(article.id)) return;
    if (hasRealAnalysis) return;  // already have a populated analysis block
    triggeredRef.current.add(article.id);
    news.triggerAnalysis(article.id).then(() => {
      // Allow the server ~30s to enrich before polling. The Phase 33
      // on-demand path typically completes in 45-60s; the refetch
      // interval below handles the long tail.
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["now-article-analysis", article.id] });
      }, 30_000);
    }).catch(() => { /* surfacing handled by the empty-state copy below */ });
  }, [open, article.id, hasRealAnalysis, analysisQuery.isLoading, qc]);

  if (!open) return null;

  // Prefer the fetched deep_insight (carries unified analysis) over the
  // lean Article object the SwipeDeck handed us.
  const fetchedDi = analysisQuery.data?.analysis?.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  const passedDi = article.deep_insight as { analysis?: UnifiedAnalysis } | undefined;
  const di = fetchedDi || passedDi;
  const analysis = di?.analysis;
  // `fetchPending` strictly tracks the in-flight network call. The
  // empty-state copy uses this to differentiate "loading" vs
  // "analysis is still being generated server-side".
  const fetchPending = analysisQuery.isLoading && !analysis;
  const category = _category(article);
  const wim = analysis?.why_it_matters;
  const band = (wim?.materiality_band || article.criticality_band || "MEDIUM").toString().toUpperCase() as "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
  const pill = pillTokens(band);
  const exposureLabel = wim?.financial_exposure?.label
    || (wim?.financial_exposure?.amount_cr ? `~₹${wim.financial_exposure.amount_cr.toLocaleString("en-IN")} Cr` : "");

  // Narrative paragraphs — deterministic from Phase 32's analysis fields.
  const stakes = (wim?.stakes_for_company || "").trim();
  const critSummary = (wim?.criticality_summary || "").trim();
  const topAction = analysis?.what_it_triggers?.recommended_actions?.[0];
  const topRisks = (analysis?.what_to_watch?.top_risk_categories || []).slice(0, 3);
  const headlineOnly = !!analysis?.headline_only;

  const parsedExtras = recipientsInput
    .split(/[,;\s]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s))
    .slice(0, 10);

  const handleEmailReport = async () => {
    if (emailState === "sending" || emailState === "sent") return;
    setEmailState("sending");
    setEmailError("");
    setExtrasSent(0);
    setExtrasFailed(0);
    try {
      const res = await articleEmail.emailSelf(article.id, parsedExtras);
      setEmailState("sent");
      const additional = res.additional ?? [];
      setExtrasSent(additional.filter((r) => r.status === "sent").length);
      setExtrasFailed(additional.filter((r) => r.status === "failed").length);
    } catch (err) {
      setEmailState("failed");
      setEmailError(err instanceof Error ? err.message : "Couldn't send the report.");
    }
  };

  return (
    <div className="screen-fade" style={{
      position: "absolute", inset: 0,
      background: TOKENS.bg,
      display: "flex", flexDirection: "column",
      zIndex: 50,
      overflow: "hidden",
    }}>
      {/* Sticky header */}
      <div style={{
        position: "sticky", top: 0,
        height: 50, flex: "0 0 auto",
        background: "rgba(255,255,255,0.92)",
        backdropFilter: "blur(16px)",
        borderBottom: `1px solid ${TOKENS.line}`,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "0 12px",
        zIndex: 5,
      }}>
        <button onClick={onClose} className="tap" style={{
          width: 36, height: 36, borderRadius: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "transparent", border: "none", cursor: "pointer",
        }} aria-label="Back">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M12 4 6 10l6 6" stroke={TOKENS.ink} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
        <button onClick={onBookmarkToggle} className="tap" style={{
          width: 36, height: 36, borderRadius: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: "transparent", border: "none", cursor: "pointer",
          color: bookmarked ? TOKENS.brand : TOKENS.ink4,
        }} aria-label={bookmarked ? "Remove bookmark" : "Bookmark"}>
          <svg width="18" height="18" viewBox="0 0 18 18" fill={bookmarked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8">
            <path d="M3 2h12v14L9 12l-6 4V2z"/>
          </svg>
        </button>
      </div>

      {/* Scrollable body */}
      <div className="app-scroll" style={{ flex: 1, overflowY: "auto" }}>
        {/* Hero band */}
        <div style={{
          position: "relative",
          padding: "20px 20px 18px",
          background: categoryTint(category),
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
            <span style={{
              background: "rgba(255,255,255,0.92)",
              padding: "5px 10px", borderRadius: 999,
              fontSize: 10.5, fontWeight: 600, color: TOKENS.ink2,
              letterSpacing: "0.02em",
            }}>{category}</span>
            <span className="pon-pill" style={{ background: pill.bg, color: pill.fg }}>
              {band}
            </span>
          </div>
          {/* Phase 40.C — headline is clickable to the source article
              when article.url is present (most articles ingested via
              Google News + NewsAPI.ai carry the canonical publisher URL).
              Opens in a new tab so the reader doesn't lose the Snowkap
              context. Falls back to non-clickable when URL is missing. */}
          {article.url ? (
            <a
              href={article.url}
              target="_blank"
              rel="noopener noreferrer"
              className="serif"
              style={{
                display: "block",
                margin: 0,
                fontSize: 22,
                lineHeight: 1.25,
                fontWeight: 600,
                color: TOKENS.ink,
                letterSpacing: "-0.015em",
                textDecoration: "none",
              }}
              title="Open the original article in a new tab"
            >
              {article.title}
            </a>
          ) : (
            <h1 className="serif" style={{
              margin: 0, fontSize: 22, lineHeight: 1.25, fontWeight: 600,
              color: TOKENS.ink, letterSpacing: "-0.015em",
            }}>
              {article.title}
            </h1>
          )}
          <div style={{
            marginTop: 10, display: "flex", gap: 10, alignItems: "center",
            fontSize: 11, color: TOKENS.ink3,
          }}>
            {article.source && <span>{article.source}</span>}
            {article.published_at && <span>· {new Date(article.published_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</span>}
            {/* Phase 40.C — explicit "Read original →" pill so the source-
                link affordance is discoverable even when the title isn't
                obviously a link. */}
            {article.url && (
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  padding: "2px 8px",
                  borderRadius: 999,
                  background: "rgba(255,255,255,0.85)",
                  border: "1px solid rgba(15,23,42,0.12)",
                  fontSize: 10.5,
                  fontWeight: 600,
                  color: TOKENS.brand,
                  textDecoration: "none",
                  letterSpacing: "0.02em",
                }}
                title="Open the original article in a new tab"
              >
                Read original →
              </a>
            )}
            {exposureLabel && (
              <span style={{
                marginLeft: "auto",
                padding: "3px 9px", borderRadius: 999,
                background: "rgba(255,255,255,0.85)",
                fontSize: 11, fontWeight: 600, color: TOKENS.ink,
              }}>{exposureLabel}</span>
            )}
          </div>
          {/* Headline-only transparency banner. Surfaces the engine's
              own honesty: when the article body couldn't be scraped
              (paywall / 403 / JS-rendered SPA), the four-bullet
              analysis below is built from the title + ontology priors,
              not from article text. Every ₹ figure carries
              (engine estimate) already; this banner is the human-
              readable summary of that fact. */}
          {headlineOnly && (
            <div style={{
              marginTop: 12, padding: "8px 12px",
              background: "#fef3c7",
              border: "1px solid #fcd34d",
              borderRadius: 10,
              fontSize: 12, lineHeight: 1.45,
              color: "#7c2d12",
              display: "flex", gap: 8, alignItems: "flex-start",
            }}>
              <span style={{ fontSize: 14, flex: "0 0 auto" }}>ⓘ</span>
              <span>
                <strong>Headline-only.</strong>{" "}
                Full article body unavailable (publisher paywall or scraper
                blocked). Specifics below — ₹ figures, frameworks, recommended
                actions — are engine projections from the title + ontology, not
                article facts.
              </span>
            </div>
          )}
        </div>

        {/* ── Narrative dialogue box ───────────────────────────────── */}
        <section style={{
          margin: "20px 20px 0",
          padding: "18px 20px",
          background: TOKENS.bgSoft,
          border: `1px solid ${TOKENS.line}`,
          borderRadius: 16,
        }}>
          <p style={{
            margin: 0, fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
            textTransform: "uppercase", color: TOKENS.ink3,
          }}>
            Why this matters
          </p>
          {/* Para 1 — Why this matters to YOU */}
          {stakes && (
            <p style={{
              margin: "10px 0 0", fontSize: 14.5, lineHeight: 1.6, color: TOKENS.ink,
            }}>
              <strong style={{ color: TOKENS.brand }}>For you · </strong>
              {stakes}
            </p>
          )}
          {/* Para 2 — How it impacts YOUR business */}
          {critSummary && (
            <p style={{
              margin: "12px 0 0", fontSize: 14, lineHeight: 1.6, color: TOKENS.ink2,
            }}>
              <strong style={{ color: TOKENS.brand }}>How it impacts · </strong>
              {critSummary}
              {topRisks.length > 0 && (
                <> Top risks to watch: {topRisks.join(" · ")}.</>
              )}
            </p>
          )}
          {/* Para 3 — What you should do */}
          {topAction?.title && (
            <p style={{
              margin: "12px 0 0", fontSize: 14, lineHeight: 1.6, color: TOKENS.ink2,
            }}>
              <strong style={{ color: TOKENS.brand }}>What to do · </strong>
              {topAction.title}
              {topAction.deadline && (
                <span style={{ color: TOKENS.ink3 }}> (by {topAction.deadline})</span>
              )}
              {topAction.owner && (
                <span style={{ color: TOKENS.ink3 }}> · owner: {topAction.owner}</span>
              )}
            </p>
          )}
          {!stakes && !critSummary && !topAction && (
            <p style={{
              margin: "10px 0 0", fontSize: 13, color: TOKENS.ink3, fontStyle: "italic",
            }}>
              {fetchPending
                ? "Loading the intelligence for this article…"
                : "Snowkap is analysing this article — body extraction + cascade math typically takes 60–120 seconds. This sheet auto-refreshes; you can keep it open, swipe back to the deck, or tap the tech-report button to have it emailed when ready."
              }
            </p>
          )}
        </section>

        {/* ── Tech-report email stripe ─────────────────────────────── */}
        <section style={{
          margin: "16px 20px 0",
          padding: "14px 18px",
          background: "#fff",
          border: `1px solid ${TOKENS.line}`,
          borderRadius: 16,
        }}>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 12,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{
                margin: 0, fontSize: 13.5, fontWeight: 600, color: TOKENS.ink,
              }}>
                Get the full technical report
              </p>
              <p style={{
                margin: "3px 0 0", fontSize: 11, color: TOKENS.ink3, lineHeight: 1.45,
              }}>
                Cascade math, framework citations, sentiment trajectory — emailed
                to your inbox{parsedExtras.length > 0 ? ` and ${parsedExtras.length} other${parsedExtras.length > 1 ? "s" : ""}` : ""}.
              </p>
            </div>
            <button
              onClick={handleEmailReport}
              disabled={emailState === "sending" || emailState === "sent"}
              className="tap"
              style={{
                flex: "0 0 auto",
                padding: "10px 16px",
                borderRadius: 999,
                border: "none",
                cursor: emailState === "sending" ? "wait" : "pointer",
                background: emailState === "sent" ? TOKENS.positive : TOKENS.brand,
                color: "#fff",
                fontSize: 12.5, fontWeight: 700, letterSpacing: "0.02em",
                opacity: emailState === "sending" ? 0.7 : 1,
                transition: "background 160ms ease, opacity 160ms ease",
              }}
            >
              {emailState === "idle"    && (parsedExtras.length > 0 ? `📧 Email ${parsedExtras.length + 1}` : "📧 Email me")}
              {emailState === "sending" && "Sending…"}
              {emailState === "sent"    && "✓ Sent"}
              {emailState === "failed"  && "Retry"}
            </button>
          </div>

          {/* "Also send to" toggle + chip-input */}
          {emailState !== "sent" && (
            <div style={{ marginTop: 12 }}>
              {!showRecipients ? (
                <button
                  type="button"
                  onClick={() => setShowRecipients(true)}
                  className="tap"
                  style={{
                    background: "transparent", border: "none", padding: 0,
                    color: TOKENS.brand, fontSize: 12, fontWeight: 600,
                    cursor: "pointer",
                  }}
                >
                  + Also send to colleagues
                </button>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <label style={{
                    fontSize: 11, fontWeight: 600, color: TOKENS.ink3,
                    letterSpacing: "0.02em",
                  }}>
                    Additional recipients (comma-separated, up to 10)
                  </label>
                  <input
                    type="text"
                    value={recipientsInput}
                    onChange={(e) => setRecipientsInput(e.target.value)}
                    placeholder="cfo@yourco.com, esg@yourco.com"
                    autoComplete="off"
                    spellCheck={false}
                    style={{
                      padding: "9px 12px",
                      borderRadius: 10,
                      border: `1px solid ${TOKENS.line}`,
                      fontSize: 13,
                      background: "#fff",
                      color: TOKENS.ink,
                      outline: "none",
                    }}
                  />
                  <div style={{
                    fontSize: 10.5, color: TOKENS.ink3,
                    display: "flex", justifyContent: "space-between",
                  }}>
                    <span>
                      {parsedExtras.length > 0
                        ? `${parsedExtras.length} valid recipient${parsedExtras.length > 1 ? "s" : ""}`
                        : recipientsInput.trim()
                          ? "No valid emails parsed yet"
                          : "Each address gets the same Morning-Brew report"}
                    </span>
                    <button
                      type="button"
                      onClick={() => { setShowRecipients(false); setRecipientsInput(""); }}
                      className="tap"
                      style={{
                        background: "transparent", border: "none", padding: 0,
                        color: TOKENS.ink3, fontSize: 10.5, fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
        {emailState === "failed" && (
          <p style={{
            margin: "6px 20px 0", fontSize: 11, color: TOKENS.critical,
          }}>
            {emailError}
          </p>
        )}
        {emailState === "sent" && (extrasSent > 0 || extrasFailed > 0) && (
          <p style={{
            margin: "6px 20px 0", fontSize: 11, color: TOKENS.ink3,
          }}>
            {extrasSent > 0 && `Also sent to ${extrasSent} colleague${extrasSent > 1 ? "s" : ""}.`}
            {extrasFailed > 0 && (
              <span style={{ color: TOKENS.critical, marginLeft: 6 }}>
                {extrasFailed} address{extrasFailed > 1 ? "es" : ""} couldn't be delivered.
              </span>
            )}
          </p>
        )}

        {/* ── Ask AI about this article (POW-5c) ───────────────────── */}
        <section style={{
          margin: "10px 20px 0",
          padding: "14px 16px",
          background: "#fafaf8",
          border: `1px solid ${TOKENS.line}`,
          borderRadius: 14,
          display: "flex", flexDirection: "column", gap: 10,
        }}>
          <p style={{
            margin: 0, fontSize: 10, fontWeight: 700,
            letterSpacing: "0.06em", textTransform: "uppercase",
            color: TOKENS.ink3,
          }}>
            Ask AI
          </p>
          <a
            href={`/ask?article=${encodeURIComponent(article.id)}`}
            className="tap"
            style={{
              background: TOKENS.brand, color: "#fff",
              padding: "10px 14px", borderRadius: 10,
              fontSize: 13.5, fontWeight: 600,
              textAlign: "center", textDecoration: "none",
              cursor: "pointer",
            }}
          >
            ✨ Ask AI about this article
          </a>
          <a
            href={`/ask?article=${encodeURIComponent(article.id)}&include_comments=true`}
            className="tap"
            style={{
              background: "#fff", color: TOKENS.brand,
              border: `1px solid ${TOKENS.brand}`,
              padding: "9px 14px", borderRadius: 10,
              fontSize: 13, fontWeight: 600,
              textAlign: "center", textDecoration: "none",
              cursor: "pointer",
            }}
          >
            💬 Ask about the discussion
          </a>
        </section>

        {/* ── Comments (Phase 34.5) ──────────────────────────────────
            Reddit-style, non-anonymous threads. 1-level reply nesting.
            Author identity comes from the JWT subject — the server
            stamps it; the client never sets it from form input.
            POW-5c: each row carries a "Help me reply" link that
            deep-links into /ask with focus_comment set. */}
        <section style={{ padding: "18px 20px 24px" }}>
          <CommentThread articleId={article.id}/>
        </section>
      </div>
    </div>
  );
}
