/**
 * Phase 34.3 — 4-direction swipe gesture recogniser.
 *
 * Ported from `Power of Now UI/swipe-deck.jsx` lines 27-49 + 100-120.
 * Wraps pointer events (mouse + touch via the modern Pointer Events
 * API — single code path) + the keyboard fallback (arrow keys), and
 * exposes a deterministic gesture model:
 *
 *   - drag.dir: "left" | "right" | "up" | "down" | null
 *   - finishGesture(dx, dy) is called when the pointer is released.
 *
 * Thresholds (90 px horizontal, 80 px vertical, 12 px direction-detect
 * floor) are intentionally identical to the prototype so the muscle-
 * memory of designers / testers carries over.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type SwipeDir = "left" | "right" | "up" | "down" | null;

export interface SwipeState {
  x: number;
  y: number;
  active: boolean;
  dir: SwipeDir;
}

interface UseSwipeGesturesOpts {
  /** Called when the user releases past the directional threshold. */
  onSwipe: (dir: Exclude<SwipeDir, null>) => void;
  /** Skip the keyboard arrow-key bindings (default: enabled). */
  disableKeyboard?: boolean;
  /** When false, all gestures are ignored — used while a card is
   *  animating off-screen so the user can't double-swipe. */
  enabled?: boolean;
}

interface UseSwipeGesturesResult {
  drag: SwipeState;
  intensity: number;
  /** Wire to the draggable element via `<div {...handlers} />` */
  handlers: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp:   (e: React.PointerEvent) => void;
    onPointerCancel: (e: React.PointerEvent) => void;
  };
}

const DIRECTION_FLOOR = 12;
const HORIZONTAL_THRESHOLD = 90;
const VERTICAL_THRESHOLD = 80;

export function useSwipeGestures({
  onSwipe,
  disableKeyboard = false,
  enabled = true,
}: UseSwipeGesturesOpts): UseSwipeGesturesResult {
  const [drag, setDrag] = useState<SwipeState>({ x: 0, y: 0, active: false, dir: null });
  const startRef = useRef<{ x: number; y: number; t: number } | null>(null);

  const finishGesture = useCallback((dx: number, dy: number) => {
    const adx = Math.abs(dx);
    const ady = Math.abs(dy);
    if (adx > HORIZONTAL_THRESHOLD && adx > ady) {
      onSwipe(dx < 0 ? "left" : "right");
    } else if (ady > VERTICAL_THRESHOLD && ady > adx) {
      onSwipe(dy < 0 ? "up" : "down");
    }
    setDrag({ x: 0, y: 0, active: false, dir: null });
  }, [onSwipe]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (!enabled) return;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    startRef.current = { x: e.clientX, y: e.clientY, t: Date.now() };
    setDrag({ x: 0, y: 0, active: true, dir: null });
  }, [enabled]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!enabled || !startRef.current) return;
    const dx = e.clientX - startRef.current.x;
    const dy = e.clientY - startRef.current.y;
    const adx = Math.abs(dx);
    const ady = Math.abs(dy);
    let dir: SwipeDir = null;
    if (Math.max(adx, ady) > DIRECTION_FLOOR) {
      if (adx > ady) dir = dx < 0 ? "left" : "right";
      else dir = dy < 0 ? "up" : "down";
    }
    setDrag({ x: dx, y: dy, active: true, dir });
  }, [enabled]);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (!startRef.current) return;
    const dx = e.clientX - startRef.current.x;
    const dy = e.clientY - startRef.current.y;
    startRef.current = null;
    finishGesture(dx, dy);
  }, [finishGesture]);

  // Keyboard fallback — useful on desktop for QA + testing.
  useEffect(() => {
    if (disableKeyboard || !enabled) return;
    const onKey = (e: KeyboardEvent) => {
      // Skip when focus is in a form field — let the form handle the arrow.
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (e.key === "ArrowLeft")  finishGesture(-200, 0);
      else if (e.key === "ArrowRight") finishGesture(200, 0);
      else if (e.key === "ArrowUp")    finishGesture(0, -200);
      else if (e.key === "ArrowDown")  finishGesture(0, 200);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [finishGesture, disableKeyboard, enabled]);

  const intensity = Math.min(
    1,
    Math.max(Math.abs(drag.x), Math.abs(drag.y)) / 160,
  );

  return {
    drag,
    intensity,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
    },
  };
}
