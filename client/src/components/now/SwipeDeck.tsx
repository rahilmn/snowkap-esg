/**
 * Phase 34.3 — SwipeDeck.
 *
 * 4-direction news interaction:
 *   ← swipe LEFT  → next news (advance)
 *   → swipe RIGHT → previous news
 *   ↓ swipe DOWN  → bookmark / unbookmark
 *   ↑ swipe UP    → open article detail
 *
 * Ports `Power of Now UI/swipe-deck.jsx` line-by-line but adapts to:
 *   - Snowkap's `Article[]` type (live-fetched from `/api/news/live`)
 *   - TypeScript + React-Router-aware bookmark + open handlers
 *   - Server-side bookmarks (Phase 34.7) via callback prop
 *
 * Visual stack: bottom (2nd-next) at scale 0.92 + opacity 0.5, middle
 * (next) at 0.96 + 0.85, top is the live drag-target.
 */
import { useEffect, useState } from "react";
import { useSwipeGestures, type SwipeDir } from "@/hooks/useSwipeGestures";
import { SwipeCard } from "@/components/now/SwipeCard";
import { TutorialOverlay } from "@/components/now/TutorialOverlay";
import type { Article } from "@/types";

interface Props {
  articles: Article[];
  bookmarked: Set<string>;
  onBookmarkToggle: (articleId: string) => void;
  onOpen: (article: Article) => void;
}

const EXIT_ANIMATION_MS = 260;

export function SwipeDeck({ articles, bookmarked, onBookmarkToggle, onOpen }: Props) {
  const [index, setIndex] = useState(0);
  const [exiting, setExiting] = useState<"left" | "right" | null>(null);
  const [toast, setToast] = useState<{ msg: string; kind: "mark" | "info" } | null>(null);
  const [tutorialDismissed, setTutorialDismissed] = useState<boolean>(() => {
    try { return localStorage.getItem("pon-tut-1") === "1"; } catch { return false; }
  });

  const article = articles[index];
  const nextArticle = articles[(index + 1) % Math.max(articles.length, 1)];
  const next2Article = articles[(index + 2) % Math.max(articles.length, 1)];

  const dismissTutorial = () => {
    setTutorialDismissed(true);
    try { localStorage.setItem("pon-tut-1", "1"); } catch { /* private browsing */ }
  };

  const flashToast = (msg: string, kind: "mark" | "info" = "info") => {
    setToast({ msg, kind });
    window.setTimeout(() => setToast(null), 1500);
  };

  // Wiki v1.1 — surface server-side bookmark failures from savedStore.
  // The store emits `snowkap:bookmark-error` whenever its server-first
  // sync reverts an optimistic update; we render a toast so the user
  // sees why the bookmark badge just disappeared.
  useEffect(() => {
    const onError = (e: Event) => {
      const detail = (e as CustomEvent).detail as {
        action?: "save" | "unsave";
        message?: string;
      } | undefined;
      const verb = detail?.action === "unsave" ? "remove" : "save";
      setToast({ msg: `Couldn't ${verb} bookmark — try again`, kind: "info" });
      window.setTimeout(() => setToast(null), 2500);
    };
    window.addEventListener("snowkap:bookmark-error", onError);
    return () => window.removeEventListener("snowkap:bookmark-error", onError);
  }, []);

  const handleSwipe = (dir: Exclude<SwipeDir, null>) => {
    if (!article) return;
    if (!tutorialDismissed) dismissTutorial();

    if (dir === "left") {
      // NEXT — slide top card off to the left, advance index
      setExiting("left");
      window.setTimeout(() => {
        setIndex((i) => (i + 1) % articles.length);
        setExiting(null);
      }, EXIT_ANIMATION_MS);
    } else if (dir === "right") {
      // PREVIOUS — bounce on first card, else slide off right
      if (index === 0) {
        flashToast("You're at the latest", "info");
        return;
      }
      setExiting("right");
      window.setTimeout(() => {
        setIndex((i) => (i - 1 + articles.length) % articles.length);
        setExiting(null);
      }, EXIT_ANIMATION_MS);
    } else if (dir === "down") {
      const wasMarked = bookmarked.has(article.id);
      onBookmarkToggle(article.id);
      flashToast(wasMarked ? "Removed from Wiki" : "Saved to your Wiki", "mark");
    } else if (dir === "up") {
      onOpen(article);
    }
  };

  const { drag, intensity, handlers } = useSwipeGestures({
    onSwipe: handleSwipe,
    enabled: !exiting && articles.length > 0,
  });

  if (articles.length === 0 || !article) {
    // Phase 36 fix — friendly onboarding-in-progress empty state.
    // Previously said "Tap 'Scan Now' to refresh" but there's no Scan
    // Now button — a misleading copy left over from earlier UX. For
    // a freshly-signed-up tenant where the pipeline is still fetching,
    // explain what's happening and set an honest expectation.
    return (
      <div style={{
        position: "absolute", inset: 0,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 12,
        padding: 32, textAlign: "center",
        color: "#475569",
      }}>
        <div style={{ fontSize: 32 }}>🌱</div>
        <div style={{ fontSize: 15, color: "#0F172A", fontWeight: 600 }}>
          Setting up your Now feed
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.5, maxWidth: 280 }}>
          Our pipeline is fetching ESG news for your company across 47
          themes. Your top 3 critical articles will land here in
          ~5&ndash;7 minutes.
        </div>
        <div style={{ fontSize: 11, color: "#94A3B8", marginTop: 4 }}>
          This page auto-refreshes every 90 seconds.
        </div>
      </div>
    );
  }
  // Narrow `article` to non-undefined for the rest of the render.
  const currentArticle: Article = article;

  // Top-card transform: live drag, exit animation, or rest.
  let topTransform: string;
  let topTransition: string = "none";
  if (exiting === "left") {
    topTransform = "translate(-120%, -40px) rotate(-18deg)";
    topTransition = "transform 260ms cubic-bezier(0.55, 0, 0.5, 1), opacity 200ms";
  } else if (exiting === "right") {
    topTransform = "translate(120%, -40px) rotate(18deg)";
    topTransition = "transform 260ms cubic-bezier(0.55, 0, 0.5, 1), opacity 200ms";
  } else if (drag.active) {
    const rot = drag.x / 18;
    topTransform = `translate(${drag.x}px, ${drag.y * 0.55}px) rotate(${rot}deg)`;
  } else {
    topTransform = "translate(0,0) rotate(0)";
    topTransition = "transform 280ms cubic-bezier(0.2, 0.9, 0.3, 1.2)";
  }

  // Directional tint overlay
  let tintColor: string | null = null;
  if (drag.dir === "down")  tintColor = `rgba(27, 138, 59, ${0.16 * intensity})`;
  if (drag.dir === "up")    tintColor = `rgba(223, 89, 0, ${0.16 * intensity})`;
  if (drag.dir === "left" || drag.dir === "right") tintColor = `rgba(15, 17, 21, ${0.06 * intensity})`;

  return (
    <div style={{
      position: "relative", width: "100%", height: "100%",
      overflow: "hidden",
      userSelect: "none", touchAction: "none",
    }}>
      {/* Directional edge hints */}
      {/* Edge hints — only render while the tutorial is still up, OR
          while the user is actively dragging. After the first-launch
          tutorial is dismissed the deck shows just the cards. */}
      <EdgeHint side="left"   label="Next"     active={drag.dir === "left"}  intensity={intensity} visible={!tutorialDismissed || drag.active}/>
      <EdgeHint side="right"  label="Previous" active={drag.dir === "right"} intensity={intensity} visible={!tutorialDismissed || drag.active}/>
      <EdgeHint side="top"    label="Read"     active={drag.dir === "up"}    intensity={intensity} visible={!tutorialDismissed || drag.active}/>
      <EdgeHint side="bottom" label={bookmarked.has(currentArticle.id) ? "Unbookmark" : "Bookmark"} active={drag.dir === "down"} intensity={intensity} positive visible={!tutorialDismissed || drag.active}/>

      {/* 2nd-next card (deepest in stack) */}
      {next2Article && next2Article.id !== currentArticle.id && (
        <CardShell zIndex={1} scale={0.92} opacity={0.5} y={20} static>
          <SwipeCard article={next2Article} bookmarked={bookmarked.has(next2Article.id)} />
        </CardShell>
      )}

      {/* Next card */}
      {!exiting && nextArticle && nextArticle.id !== currentArticle.id && (
        <CardShell zIndex={2} scale={0.96} opacity={0.85} y={10} static>
          <SwipeCard article={nextArticle} bookmarked={bookmarked.has(nextArticle.id)} />
        </CardShell>
      )}

      {/* Top (interactive) card */}
      <CardShell
        zIndex={5}
        transform={topTransform}
        transition={topTransition}
        opacity={exiting ? 0 : 1}
        interactive
        handlers={handlers}
        tint={tintColor}
      >
        <SwipeCard article={currentArticle} bookmarked={bookmarked.has(currentArticle.id)} />
      </CardShell>

      {/* Toast (bookmark / bounce) */}
      {toast && (
        <div className="screen-fade" style={{
          position: "absolute", left: "50%", bottom: 110,
          transform: "translateX(-50%)",
          background: "#101418", color: "white",
          padding: "11px 18px", borderRadius: 999,
          fontSize: 13, fontWeight: 500,
          boxShadow: "0 18px 40px rgba(0,0,0,0.18)",
          display: "inline-flex", alignItems: "center", gap: 8,
          zIndex: 30,
        }}>
          {toast.kind === "mark" && (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
              <path d="M2 1h10v12L7 9l-5 4V1z"/>
            </svg>
          )}
          {toast.msg}
        </div>
      )}

      {/* First-launch tutorial */}
      {!tutorialDismissed && !drag.active && !exiting && (
        <TutorialOverlay onDismiss={dismissTutorial}/>
      )}

      {/* Deck counter */}
      <div style={{
        position: "absolute", bottom: 8, left: "50%", transform: "translateX(-50%)",
        fontSize: 10, color: "#a0a4ab", fontVariantNumeric: "tabular-nums",
        letterSpacing: "0.08em", fontWeight: 600, zIndex: 6,
      }}>
        {String(index + 1).padStart(2, "0")} · {String(articles.length).padStart(2, "0")}
      </div>
    </div>
  );
}

// ─── Card shell (animated wrapper around SwipeCard) ─────────────────────────

interface CardShellProps {
  children: React.ReactNode;
  zIndex: number;
  scale?: number;
  opacity?: number;
  y?: number;
  transform?: string;
  transition?: string;
  interactive?: boolean;
  static?: boolean;
  handlers?: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp:   (e: React.PointerEvent) => void;
    onPointerCancel: (e: React.PointerEvent) => void;
  };
  tint?: string | null;
}

function CardShell({
  children, zIndex, scale, opacity = 1, y = 0,
  transform, transition, interactive, static: isStatic, handlers, tint,
}: CardShellProps) {
  const t = transform || `translate(0, ${y}px) scale(${scale ?? 1})`;
  return (
    <div
      style={{
        position: "absolute",
        left: 16, right: 16, top: 96, bottom: 96,
        background: "white",
        borderRadius: 22,
        border: "1px solid #ececef",
        boxShadow: interactive
          ? "0 24px 50px rgba(15,17,21,0.10), 0 4px 14px rgba(15,17,21,0.05)"
          : "0 12px 30px rgba(15,17,21,0.05)",
        transform: t,
        transition: transition || (isStatic ? "transform 320ms ease" : undefined),
        opacity,
        zIndex,
        overflow: "hidden",
        cursor: interactive ? "grab" : "default",
        touchAction: "none",
        willChange: "transform",
      }}
      onPointerDown={interactive ? handlers?.onPointerDown : undefined}
      onPointerMove={interactive ? handlers?.onPointerMove : undefined}
      onPointerUp={interactive ? handlers?.onPointerUp : undefined}
      onPointerCancel={interactive ? handlers?.onPointerCancel : undefined}
    >
      {children}
      {tint && (
        <div style={{
          position: "absolute", inset: 0,
          background: tint, pointerEvents: "none",
        }}/>
      )}
    </div>
  );
}

// ─── Edge hint (directional cue on the side of the screen) ──────────────────

function EdgeHint({
  side, label, active, intensity = 0, positive, visible = true,
}: {
  side: "left" | "right" | "top" | "bottom";
  label: string;
  active: boolean;
  intensity?: number;
  positive?: boolean;
  visible?: boolean;
}) {
  if (!visible && !active) return null;
  const opacity = active ? Math.min(1, 0.25 + intensity * 0.9) : 0.22;
  const color = active
    ? (positive ? "#1b8a3b" : (side === "top" ? "#df5900" : "#0f1115"))
    : "#a0a4ab";
  const arrow = { left: "←", right: "→", top: "↑", bottom: "↓" }[side];

  const baseStyle: React.CSSProperties = {
    position: "absolute",
    display: "flex", alignItems: "center", justifyContent: "center",
    gap: 6,
    fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase",
    fontWeight: 600,
    color, opacity,
    transition: "opacity 180ms ease, color 180ms ease",
    zIndex: 4,
    pointerEvents: "none",
  };

  let positioning: React.CSSProperties = {};
  if (side === "left")   positioning = { left: 10,  top: "50%", transform: "translateY(-50%) rotate(-90deg)", transformOrigin: "center" };
  if (side === "right")  positioning = { right: 10, top: "50%", transform: "translateY(-50%) rotate(90deg)",  transformOrigin: "center" };
  if (side === "top")    positioning = { top: 64,    left: "50%", transform: "translateX(-50%)" };
  if (side === "bottom") positioning = { bottom: 30, left: "50%", transform: "translateX(-50%)" };

  return (
    <div style={{ ...baseStyle, ...positioning }}>
      <span>{arrow}</span>
      <span>{label}</span>
    </div>
  );
}
