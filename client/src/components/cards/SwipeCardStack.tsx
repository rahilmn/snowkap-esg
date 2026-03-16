/** Swipe card stack — gesture-driven card navigation (Stage 6.2) */

import { useState, useRef, useCallback, useEffect } from "react";
import { useSprings, animated } from "react-spring";
import { useDrag } from "@use-gesture/react";
import type { Article } from "@/types";

interface SwipeCardStackProps {
  cards: Article[];
  onSwipeRight: (card: Article) => void;
  onSwipeLeft: (card: Article) => void;
  onTap: (card: Article) => void;
  onRefresh?: () => void;
  renderCard: (card: Article, index: number) => React.ReactNode;
}

const SWIPE_THRESHOLD = 100;
const VISIBLE_CARDS = 3;
const CARD_SCALES = [1.0, 0.97, 0.94];
const CARD_OFFSETS = [0, 8, 16];

export function SwipeCardStack({
  cards,
  onSwipeRight,
  onSwipeLeft,
  onTap,
  onRefresh,
  renderCard,
}: SwipeCardStackProps) {
  const [goneIds, setGoneIds] = useState<Set<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);
  const goneRef = useRef<Set<string>>(goneIds);

  const markGone = useCallback((id: string) => {
    setGoneIds((prev) => {
      const next = new Set(prev);
      next.add(id);
      goneRef.current = next;
      return next;
    });
  }, []);

  const [springs, api] = useSprings(cards.length, (i) => {
    const card = cards[i];
    if (!card) return { x: 0, y: 0, scale: 0, rot: 0, opacity: 0 };
    const activeList = cards.filter((c) => !goneIds.has(c.id));
    const pos = activeList.findIndex((c) => c.id === card.id);
    return {
      x: 0,
      y: 0,
      scale: pos >= 0 && pos < VISIBLE_CARDS ? (CARD_SCALES[pos] ?? 0.94) : 0,
      rot: 0,
      opacity: pos >= 0 && pos < VISIBLE_CARDS ? 1 : 0,
      config: { friction: 50, tension: 500 },
    };
  });

  // Sync spring positions whenever goneIds changes (after swipe)
  useEffect(() => {
    const gone = goneRef.current;
    const remaining = cards.filter((c) => !gone.has(c.id));
    api.start((i) => {
      const card = cards[i];
      if (!card) return;
      if (gone.has(card.id)) {
        return { scale: 0, opacity: 0 };
      }
      const pos = remaining.findIndex((c) => c.id === card.id);
      return {
        x: 0,
        y: 0,
        rot: 0,
        scale: pos >= 0 && pos < VISIBLE_CARDS ? (CARD_SCALES[pos] ?? 0.94) : 0.94,
        opacity: pos >= 0 && pos < VISIBLE_CARDS ? 1 : 0,
        config: { friction: 50, tension: 500 },
      };
    });
  }, [goneIds, cards, api]);

  const bind = useDrag(
    ({ args: [index], active, movement: [mx, my], direction: [xDir], velocity: [vx] }) => {
      const card = cards[index];
      if (!card || goneRef.current.has(card.id)) return;

      const trigger = Math.abs(mx) > SWIPE_THRESHOLD || vx > 0.5;
      const currentActive = cards.filter((c) => !goneRef.current.has(c.id));
      const isTopCard = currentActive[0]?.id === card.id;
      const isPullDown = my > 80 && Math.abs(mx) < 40 && isTopCard;

      if (!active && isPullDown && onRefresh) {
        setRefreshing(true);
        onRefresh();
        setTimeout(() => setRefreshing(false), 1000);
        api.start((i) => {
          if (i !== index) return;
          return { y: 0 };
        });
        return;
      }

      if (!active && trigger) {
        markGone(card.id);
        const dir = xDir > 0 ? 1 : -1;
        if (dir > 0) onSwipeRight(card);
        else onSwipeLeft(card);
      }

      // Tap detection — small movement, quick release
      if (!active && Math.abs(mx) < 10 && Math.abs(my) < 10) {
        onTap(card);
        return;
      }

      api.start((i) => {
        const c = cards[i];
        if (!c) return;
        const gone = goneRef.current;
        if (i === index) {
          const isGone = gone.has(card.id);
          const x = isGone ? (200 + window.innerWidth) * (mx > 0 ? 1 : -1) : active ? mx : 0;
          const rot = active ? mx / 15 : 0;
          const scale = isGone ? 0 : active ? 1.02 : 1.0;
          const y = active ? my : 0;
          return {
            x,
            y,
            rot: Math.max(-15, Math.min(15, rot)),
            scale,
            opacity: isGone ? 0 : 1,
            config: active ? { friction: 50, tension: 800 } : { friction: 50, tension: 500 },
          };
        }
        // Update z-order of remaining cards after swipe
        if (!active && !gone.has(c.id)) {
          const remainingCards = cards.filter((cc) => !gone.has(cc.id));
          const pos = remainingCards.findIndex((cc) => cc.id === c.id);
          return {
            x: 0,
            y: CARD_OFFSETS[pos] ?? 16,
            rot: 0,
            scale: CARD_SCALES[pos] ?? 0.94,
            opacity: pos < VISIBLE_CARDS ? 1 : 0,
          };
        }
      });
    },
    { filterTaps: true },
  );

  const activeCards = cards.filter((c) => !goneIds.has(c.id));

  return (
    <div className="relative flex items-center justify-center" style={{ height: 460, touchAction: "none" }}>
      {refreshing && (
        <div className="absolute top-0 z-50 text-sm text-muted-foreground animate-pulse">
          Refreshing...
        </div>
      )}
      {springs.map(({ x, y, rot, scale, opacity }, i) => {
        const card = cards[i];
        if (!card || goneIds.has(card.id)) return null;
        const activeIdx = activeCards.findIndex((c) => c.id === card.id);
        if (activeIdx >= VISIBLE_CARDS) return null;
        return (
          <animated.div
            key={card.id}
            {...bind(i)}
            style={{
              position: "absolute",
              width: "min(calc(100vw - 2rem), 400px)",
              x,
              y,
              scale,
              opacity,
              rotateZ: rot.to((r) => `${r}deg`),
              zIndex: cards.length - activeIdx,
              touchAction: "none",
            }}
            className="cursor-grab active:cursor-grabbing"
          >
            {renderCard(card, i)}
          </animated.div>
        );
      })}
      {goneIds.size >= cards.length && cards.length > 0 && (
        <div className="text-center text-muted-foreground">
          <p className="text-lg font-medium">All caught up!</p>
          <p className="text-sm mt-1">Pull down to refresh</p>
        </div>
      )}
    </div>
  );
}
