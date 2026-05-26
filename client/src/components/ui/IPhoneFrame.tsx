/**
 * Responsive app shell — previously a phone bezel + status bar + home
 * indicator (Phase 34.1). Now drops the faux-iOS chrome entirely and
 * just provides a centered, max-width container so the same component
 * tree reads naturally on both phone and desktop.
 *
 * Behaviour:
 *   - <= 640px wide: full-bleed (children fill the viewport).
 *   - > 640px wide: centered column with `max-width: 560px`, subtle
 *     shadow + 1px border to delineate the app area. Background outside
 *     the column uses a soft warm tint so the layout reads as "app".
 *
 * Children still rely on `position: absolute; inset: 0` for layout; the
 * inner container is `position: relative; height: 100%` so that contract
 * keeps working unchanged.
 *
 * The `forced` and `showStatusBar` props are kept for API back-compat
 * but no longer change the rendering — calling sites don't need to
 * change. We may remove them in a future cleanup once no callers pass
 * them.
 */

interface Props {
  children: React.ReactNode;
  /** @deprecated — phone bezel is removed; this prop is now a no-op. */
  forced?: boolean;
  /** @deprecated — phone status bar is removed; this prop is now a no-op. */
  showStatusBar?: boolean;
}

export function IPhoneFrame({ children }: Props) {
  return (
    <div style={{
      position: "fixed", inset: 0,
      background: "#f6f3ef",
      fontFamily: "Inter, -apple-system, system-ui, sans-serif",
      display: "flex", justifyContent: "center",
      overflow: "hidden",
    }}>
      <div style={{
        position: "relative",
        width: "100%",
        maxWidth: 560,
        height: "100%",
        background: "#fff",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.04), 0 12px 40px rgba(15,17,21,0.06)",
        overflow: "hidden",
      }}>
        {children}
      </div>
    </div>
  );
}
