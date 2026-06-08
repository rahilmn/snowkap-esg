/**
 * Phase 34.3 — `/now` route. Mobile-first SwipeDeck experience.
 *
 * Wires the live feed (`news.live(companyId)`) into the SwipeDeck +
 * SwipeCard + TutorialOverlay components. Wraps everything in the
 * IPhoneFrame so desktop browsers see the iOS chrome while mobile
 * devices get full-bleed.
 *
 * Bookmark + open handlers route to:
 *   - swipe ↓: server-side bookmark (Phase 34.7 endpoint — when the
 *              migration ships). For now, local Zustand `savedStore`.
 *   - swipe ↑: opens the article-detail sheet via the existing
 *              ArticleDetailSheet (Phase 34.4 will replace this with
 *              a Power-of-Now-styled `ArticleSheet`).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { news, now } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useSavedStore } from "@/stores/savedStore";
import { IPhoneFrame } from "@/components/ui/IPhoneFrame";
import { SwipeDeck } from "@/components/now/SwipeDeck";
import { ArticleSheet } from "@/components/now/ArticleSheet";
import { TOKENS } from "@/lib/designTokensV2";
import type { Article } from "@/types";

/** Time-aware salutation for the /now header.
 *
 * Returns:
 *   - eyebrow: `Wednesday morning` (day name + time-of-day)
 *   - line:    `Today's signal, Naik` — short, evergreen, keeps the
 *              brand word "signal" that the deck already uses
 *              ("Live signal" sub-label on every card).
 *
 * Time buckets (local clock):
 *   05:00–11:59 → "morning"
 *   12:00–16:59 → "afternoon"
 *   17:00–21:59 → "evening"
 *   22:00–04:59 → "night"   (eyebrow flips to "Working late")
 */
function _useGreeting(firstName: string): { eyebrow: string; line: string } {
  const now = new Date();
  const hour = now.getHours();
  const dayName = now.toLocaleDateString(undefined, { weekday: "long" });

  let timeWord: "morning" | "afternoon" | "evening" | "night";
  if (hour >= 5 && hour < 12) timeWord = "morning";
  else if (hour >= 12 && hour < 17) timeWord = "afternoon";
  else if (hour >= 17 && hour < 22) timeWord = "evening";
  else timeWord = "night";

  // Eyebrow — day + time, except late night where we acknowledge it.
  const eyebrow = timeWord === "night"
    ? "Working late"
    : `${dayName} ${timeWord}`;

  // Main line — time-of-day adjective on the brief itself, keeps brand
  // word "signal".
  const briefWord: Record<typeof timeWord, string> = {
    morning: "Today's signal",
    afternoon: "The midday read",
    evening: "Evening dispatch",
    night: "Late-night dispatch",
  };
  const line = `${briefWord[timeWord]}, ${firstName}`;

  return { eyebrow, line };
}

export function NowPage() {
  const name = useAuthStore((s) => s.name) || "there";
  const companyId = useAuthStore((s) => s.companyId);
  const logout = useAuthStore((s) => s.logout);
  const firstName = name.split(" ")[0] || "there";
  const greeting = _useGreeting(firstName);
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = async () => {
    if (isRefreshing) return;
    setIsRefreshing(true);
    try {
      // Invalidate both the feed query AND any per-article analysis caches
      // so a refresh re-fetches everything from the server.
      await queryClient.invalidateQueries({ queryKey: ["now-feed", companyId] });
      await queryClient.invalidateQueries({ queryKey: ["now-article-analysis"] });
      // Force-await one refetch so the spinner only stops when fresh data lands.
      await queryClient.refetchQueries({ queryKey: ["now-feed", companyId] });
    } finally {
      // Tiny delay so the spinner is visible on near-instant cache hits.
      window.setTimeout(() => setIsRefreshing(false), 400);
    }
  };

  // Close the profile dropdown on outside-click / escape.
  useEffect(() => {
    if (!profileOpen) return;
    const onClick = (e: MouseEvent) => {
      if (!profileRef.current?.contains(e.target as Node)) setProfileOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setProfileOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [profileOpen]);

  const handleLogout = () => {
    logout();
    window.location.href = "/login";
  };

  // POW-4 — read the deck from the new /api/now/feed endpoint backed by
  // `article_pool ⋈ company_article_view`. Top-3 are CRITICAL pre-personalised
  // rows; slots 4-10 fill from HIGH → MEDIUM → LOW within 30 days.
  // See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.4, §5.1.
  const feedQuery = useQuery({
    queryKey: ["now-feed", companyId],
    queryFn: () => companyId
      // Phase 50 — max_age_days MUST be <= 30; the endpoint (Phase 47.Q) caps
      // it at 30 and 422s on anything larger. This hardcoded 90 made EVERY
      // feed request fail with "Couldn't load the feed" regardless of tenant.
      ? now.feed(companyId, 10, 30)
      : Promise.resolve({ company_slug: "", industry: "", count: 0, limit: 0, max_age_days: 0, articles: [] }),
    enabled: !!companyId,
    refetchInterval: 90_000,
    refetchOnWindowFocus: true,
  });

  // Server-side bookmarks will land in Phase 34.7 — for now read the
  // existing Zustand `savedStore` (localStorage-backed). On Phase 34.7
  // ship the migration replaces these with a server call.
  const savedIds = useSavedStore((s) => s.savedIds);
  const saveArticle = useSavedStore((s) => s.saveArticle);
  const unsaveArticle = useSavedStore((s) => s.unsaveArticle);

  const articles: Article[] = useMemo(() => {
    // POW-4 — adapt the new `articles` shape (article_pool ⋈ company_article_view
    // join) to the legacy `Article` interface the SwipeDeck/ArticleSheet expect.
    // The shared_analysis + personalised_analysis flow into Article.deep_insight
    // so the existing rendering paths in ArticleSheet pick them up without
    // additional fetches (no swipe-up cold-warm penalty for top-10 articles).
    return (feedQuery.data?.articles || []).map((a) => {
      const sharedAnalysis = (a.shared_analysis as Record<string, unknown>) || {};
      const personalisedAnalysis = (a.personalised_analysis as Record<string, unknown>) || {};
      // Merge: shared first (what_changed + its methodology), then per-company
      // overlay (why_it_matters / what_it_triggers / what_to_watch + their
      // methodology). The legacy `insight.analysis` shape combines both.
      const mergedAnalysis = {
        ...sharedAnalysis,
        ...personalisedAnalysis,
        methodology: {
          ...((sharedAnalysis.methodology as Record<string, unknown>) || {}),
          ...((personalisedAnalysis.methodology as Record<string, unknown>) || {}),
        },
      };
      return {
        id: a.article_id,
        title: a.title,
        summary: "",
        source: a.source || "",
        url: a.url,
        // Phase 48.E — hero image. NewsAPI.ai supplies metadata.image_url,
        // which the writer stamps into shared_analysis/personalised_analysis
        // (article_pool has no dedicated image column). Read it through here
        // so SwipeCard's `article.image_url` overlay actually renders —
        // previously this was hardcoded "" and hero images never showed.
        image_url:
          (sharedAnalysis.image_url as string) ||
          (personalisedAnalysis.image_url as string) ||
          "",
        published_at: a.published_at,
        company_id: feedQuery.data?.company_slug || "",
        company_slug: feedQuery.data?.company_slug || "",
        esg_pillar: a.primary_pillar,
        sentiment: null,
        entities: [],
        impact_scores: [],
        predictions: [],
        frameworks: [],
        framework_hits: [],
        sentiment_score: null,
        sentiment_confidence: null,
        aspect_sentiments: null,
        content_type: null,
        urgency: null,
        time_horizon: null,
        reversibility: null,
        priority_score: a.criticality_score,
        priority_level: a.criticality_band,
        financial_signal: null,
        executive_insight: null,
        relevance_score: null,
        relevance_breakdown: null,
        criticality_band: a.criticality_band,
        // Phase 51 — explicit tier from the backend, with a lede-presence fallback.
        now_tier: a.tier ?? (((personalisedAnalysis.lede as { text?: string } | undefined)?.text) ? "critical" : "light"),
        // Stamp the merged unified-analysis block on deep_insight so the
        // existing ArticleSheet WHY THIS MATTERS / Tech Report / Comments
        // rendering reads it without a second fetch.
        deep_insight: { analysis: mergedAnalysis } as unknown,
        scoring_metadata: null,
        rereact_recommendations: null,
        nlp_extraction: null,
        esg_themes: null,
        framework_matches: null,
        risk_matrix: null,
        geographic_signal: null,
      } as unknown as Article;
    });
  }, [feedQuery.data]);

  const bookmarkedSet: Set<string> = useMemo(() => {
    return new Set(Array.from(savedIds));
  }, [savedIds]);

  // Phase 41 — deck pre-warm. When /now loads, fire on-demand analysis
  // for the first 3 STUB cards (articles whose deep_insight has no real
  // `what_changed.headline`) in parallel. By the time the user swipes-up
  // any of these, the background pipeline is already running OR done,
  // cutting the visible wait from ~60-120s to <20s on average.
  //
  // We only pre-warm 3 articles per deck load (not all 10) to balance
  // LLM cost against perceived snappiness — the user is most likely to
  // click one of the top 3 deck cards, and pre-warming everything would
  // multiply the Stage-10/12 cost per onboard by 10×.
  const prewarmedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!articles.length) return;
    const stubs = articles.filter((a) => {
      // Phase 51 — never pre-warm a light card: it is a deliberate Stage 1-9
      // watchlist entry and must not kick a full Stage 10-12 (Opus) run.
      if ((a as { now_tier?: string }).now_tier === "light") return false;
      const di = a.deep_insight as { analysis?: { what_changed?: { headline?: string } } } | undefined;
      const hasHeadline = !!di?.analysis?.what_changed?.headline;
      return !hasHeadline && !prewarmedRef.current.has(a.id);
    }).slice(0, 3);
    if (!stubs.length) return;
    Promise.all(stubs.map((a) => {
      prewarmedRef.current.add(a.id);
      return news.triggerAnalysis(a.id).catch(() => undefined);
    })).then(() => {
      // Once the background pipelines start landing results, the deck
      // query auto-refreshes every 90s. No further frontend work needed.
    });
  }, [articles]);

  const [openArticle, setOpenArticle] = useState<Article | null>(null);

  const toggleBookmark = (articleId: string) => {
    const match = articles.find((a) => a.id === articleId);
    if (!match) return;
    if (bookmarkedSet.has(articleId)) {
      unsaveArticle(articleId);
    } else {
      saveArticle(match);
    }
  };

  return (
    <IPhoneFrame>
      <div style={{
        position: "absolute", inset: 0, paddingTop: 6, paddingBottom: 0,
        display: "flex", flexDirection: "column",
      }}>
        {/* Top bar — greeting + sparkle */}
        <div style={{
          padding: "8px 20px 4px",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          zIndex: 7,
        }}>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span style={{
              fontSize: 11, color: TOKENS.ink4, letterSpacing: "0.04em",
              fontWeight: 600, textTransform: "uppercase",
            }}>
              {greeting.eyebrow}
            </span>
            <span className="serif" style={{
              fontSize: 17, fontWeight: 500, color: TOKENS.ink, letterSpacing: "-0.015em",
            }}>
              {greeting.line}
            </span>
          </div>
          {/* Right-side controls: refresh + profile menu. */}
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {/* Refresh button — invalidates feed + per-article query caches
                so the user can force a re-fetch when something looks stale.
                The auto-poll on /api/now/feed already refreshes every 90s
                in the background; this is the explicit "I want it now"
                lever. Spinner is visible for at least 400ms even on
                instant cache hits so the click feels acknowledged. */}
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              aria-label="Refresh feed"
              className="tap"
              style={{
                width: 34, height: 34, borderRadius: 999,
                background: isRefreshing ? "#f1f5f9" : "transparent",
                border: `1px solid ${TOKENS.line}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                cursor: isRefreshing ? "wait" : "pointer",
                color: TOKENS.ink2,
                transition: "background 160ms ease",
              }}
            >
              <svg
                width="16" height="16" viewBox="0 0 16 16" fill="none"
                style={{
                  animation: isRefreshing ? "snowkap-spin 0.9s linear infinite" : undefined,
                }}
              >
                <path
                  d="M2 8a6 6 0 0 1 10.5-4M14 8a6 6 0 0 1-10.5 4M12.5 4V2m0 2h-2M3.5 12v2m0-2h2"
                  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
                />
              </svg>
              <style>{`@keyframes snowkap-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
            </button>

            {/* Profile + logout dropdown trigger. */}
            <div ref={profileRef} style={{ position: "relative" }}>
            <button
              className="tap"
              onClick={() => setProfileOpen((v) => !v)}
              aria-label="Open profile menu"
              style={{
                width: 34, height: 34, borderRadius: 999,
                background: "linear-gradient(135deg, #cfe7ee, #e9f3f6)",
                display: "flex", alignItems: "center", justifyContent: "center",
                border: profileOpen ? `1px solid ${TOKENS.brand}` : "none",
                cursor: "pointer",
                color: TOKENS.ink,
                fontSize: 13, fontWeight: 600,
              }}
            >
              {firstName.charAt(0).toUpperCase() || "•"}
            </button>
            {profileOpen && (
              <div style={{
                position: "absolute",
                top: 42, right: 0,
                width: 160,
                background: "#fff",
                border: `1px solid ${TOKENS.line}`,
                borderRadius: 12,
                boxShadow: "0 18px 40px rgba(15,17,21,0.18)",
                padding: 6,
                zIndex: 60,
              }}>
                <div style={{
                  display: "flex", flexDirection: "column", gap: 2,
                }}>
                  <button
                    onClick={handleLogout}
                    className="tap"
                    style={{
                      textAlign: "left",
                      padding: "8px 10px",
                      fontSize: 12.5, fontWeight: 600,
                      color: TOKENS.critical,
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      borderRadius: 6,
                    }}
                  >
                    Log out
                  </button>
                </div>
              </div>
            )}
            </div>
          </div>
        </div>

        {/* Deck */}
        <div style={{ flex: 1, position: "relative" }}>
          {feedQuery.isLoading ? (
            <div style={{
              position: "absolute", inset: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              color: TOKENS.ink4, fontSize: 13,
            }}>
              Loading your Now…
            </div>
          ) : feedQuery.isError ? (
            // Phase 36 fix — distinguish "company still onboarding" (404)
            // from a real backend error. A freshly-signed-up tenant whose
            // company row hasn't persisted yet hits 404; rather than the
            // alarming "Couldn't load the feed" the user sees an explicit
            // "setting up your tenant" message + a deep-link to /settings/
            // onboard so they can re-trigger onboarding.
            (() => {
              const err = feedQuery.error as { status?: number; message?: string } | undefined;
              const isNotFound = err?.status === 404 || /not yet onboarded/i.test(err?.message || "");
              if (isNotFound) {
                // The auth-login flow auto-queues onboarding for the
                // signed-in tenant, so when the feed 404s the user is
                // almost certainly mid-onboarding. Point them at the
                // live SSE progress page rather than the manual /settings
                // form so they see actual per-stage progress.
                return (
                  <div style={{
                    position: "absolute", inset: 0,
                    display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                    color: TOKENS.ink3, fontSize: 13, padding: 24, textAlign: "center", gap: 12,
                  }}>
                    <div style={{ fontSize: 32 }}>🌱</div>
                    <div style={{ fontSize: 15, color: TOKENS.ink, fontWeight: 600 }}>
                      Setting up your Now feed
                    </div>
                    <div style={{ fontSize: 13, color: TOKENS.ink3, maxWidth: 280 }}>
                      Our pipeline is fetching ESG news for {companyId || "your company"}.
                      First articles typically land in 60–120 seconds.
                    </div>
                    {companyId && (
                      <a href={`/onboarding/${encodeURIComponent(companyId)}`} style={{
                        marginTop: 8, padding: "10px 20px",
                        background: TOKENS.brand, color: "#fff",
                        borderRadius: 999, fontSize: 12, fontWeight: 700,
                        textDecoration: "none",
                      }}>
                        See live progress →
                      </a>
                    )}
                  </div>
                );
              }
              return (
                <div style={{
                  position: "absolute", inset: 0,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: TOKENS.critical, fontSize: 13, padding: 24, textAlign: "center",
                }}>
                  Couldn't load the feed. Pull to retry.
                </div>
              );
            })()
          ) : (
            <>
              {/* Phase 51 — completeness ribbon: surface a short deck instead of
                  silently showing <10 cards or <3 criticals. pointer-events:none
                  so it never intercepts a swipe. */}
              {(() => {
                const d = feedQuery.data;
                const crit = d?.critical_count ?? 0;
                const total = d?.total_count ?? d?.count ?? articles.length;
                if (articles.length > 0 && (crit < 3 || total < 10)) {
                  return (
                    <div style={{
                      position: "absolute", top: 6, left: 0, right: 0, zIndex: 5,
                      margin: "0 auto", width: "fit-content", maxWidth: "90%",
                      padding: "4px 12px", pointerEvents: "none",
                      background: "rgba(14,58,95,0.07)", color: TOKENS.ink3,
                      borderRadius: 999, fontSize: 11, fontWeight: 600, textAlign: "center",
                    }}>
                      {crit} of 3 priority briefs · {total} of 10 cards ready
                    </div>
                  );
                }
                return null;
              })()}
              <SwipeDeck
                articles={articles}
                bookmarked={bookmarkedSet}
                onBookmarkToggle={toggleBookmark}
                onOpen={setOpenArticle}
              />
            </>
          )}
        </div>

        {/* Bottom nav placeholder — real one lands in Phase 34.2 */}
        <div style={{
          position: "relative",
          height: 70,
          borderTop: `1px solid ${TOKENS.line}`,
          background: "#fff",
          display: "flex", alignItems: "center", justifyContent: "space-around",
        }}>
          {(["Now", "Forum", "Wiki", "Ask"] as const).map((label) => {
            const href = label === "Now" ? "/now" : label === "Wiki" ? "/wiki" : label === "Forum" ? "/forum" : "/ask";
            return (
              <a
                key={label}
                href={href}
                className="tap"
                style={{
                  fontSize: 11, fontWeight: 600,
                  color: label === "Now" ? TOKENS.brand : TOKENS.ink4,
                  background: "transparent", border: "none", cursor: "pointer",
                  padding: "8px 14px", borderRadius: 10,
                  textDecoration: "none",
                }}
              >
                {label}
              </a>
            );
          })}
        </div>
      </div>

      {/* Phase 34.4 — mobile-first article sheet (narrative + email + comments). */}
      {openArticle && (
        <ArticleSheet
          article={openArticle}
          open={true}
          bookmarked={bookmarkedSet.has(openArticle.id)}
          onClose={() => setOpenArticle(null)}
          onBookmarkToggle={() => toggleBookmark(openArticle.id)}
        />
      )}
    </IPhoneFrame>
  );
}

