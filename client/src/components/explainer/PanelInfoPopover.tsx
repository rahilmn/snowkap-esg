/** Phase 29 — Per-panel info popover.
 *
 * Small popover (≤340px wide) opened by the "i" icon next to a panel
 * header. Replaces the global MethodologyDrawer for the common case —
 * the drawer is still reachable for power users but the per-panel
 * popover is the one users will interact with constantly.
 *
 * Fetches `/api/insights/{id}/methodology?panel={panelId}&role={role}`
 * lazily on open (React Query caches per `(articleId, panelId, role)`).
 * Shows:
 *   1. Panel title.
 *   2. `simple_logic` — one-sentence plain-language explanation.
 *   3. `your_inputs` — actual values for THIS article (≤5 most-relevant,
 *      zeros hidden).
 *   4. Collapsible "Show formula" reveal — the role-weighted formula +
 *      ontology anchors. Hidden by default so the popover stays small.
 *
 * Positioning: anchored to the trigger element using a simple
 * fixed-position approach with viewport-edge clamping. No external
 * popover library required.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { methodology, type MethodologyMetric } from "@/lib/api";
import { COLORS } from "@/lib/designTokens";
import { Spinner } from "@/components/ui/Spinner";

interface Props {
  /** Article id to fetch methodology for. */
  articleId: string;
  /** Panel id (must match `engine/analysis/methodology_provenance.py:METRIC_DISPATCH` key). */
  panelId: string;
  /** Active role lens; "cfo" | "ceo" | "esg-analyst". */
  role: string;
  /** Coordinates of the trigger element so the popover anchors next to it. */
  anchorRect: DOMRect | null;
  /** Closes the popover. */
  onClose: () => void;
}

/** Friendly panel titles for display. Mirrors the keys in
 *  methodology_provenance.METRIC_DISPATCH. */
const PANEL_TITLES: Record<string, string> = {
  criticality: "Criticality score",
  relevance: "Relevance (5D)",
  persona_boost: "Persona boost",
  sentiment_trajectory: "Sentiment trajectory",
  framework_match: "Framework alignment",
  stakeholder_map: "Stakeholder map",
  board_paragraph: "Board paragraph",
  kpi_table: "KPI table",
  risk_matrix: "Risk assessment",
  esg_relevance_score: "ESG relevance (6D)",
  ai_recommendations: "AI Recommendations",
  impact_analysis: "Impact analysis",
};

function formatInputValue(v: unknown): string {
  if (v == null) return "n/a";
  if (typeof v === "number") return v.toFixed(3);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.slice(0, 3).join(", ") + (v.length > 3 ? "…" : "");
  if (typeof v === "object") {
    try { return JSON.stringify(v).slice(0, 60); } catch { return "n/a"; }
  }
  return String(v);
}

/** Hide zeros + nulls from `your_inputs` so the popover stays scannable. */
function compactInputs(inputs: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(inputs)
    .filter(([, v]) => v != null && v !== 0 && v !== "")
    .slice(0, 5);
}


export function PanelInfoPopover({
  articleId, panelId, role, anchorRect, onClose,
}: Props) {
  const [showFormula, setShowFormula] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{top: number; left: number} | null>(null);

  // React Query — cached per (articleId, panelId, role) so re-open is free.
  const query = useQuery({
    queryKey: ["methodology-panel", articleId, panelId, role],
    queryFn: () => methodology.fetch(articleId, role, panelId),
    enabled: !!articleId && !!panelId,
    staleTime: 5 * 60_000,
  });

  // Click-outside + Escape to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const onClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    // Defer the click handler one tick so the opening click doesn't
    // immediately close us
    const t = setTimeout(() => document.addEventListener("mousedown", onClick), 0);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
      clearTimeout(t);
    };
  }, [onClose]);

  // Position the popover next to the anchor, clamped to viewport.
  useLayoutEffect(() => {
    if (!anchorRect) { setPos({ top: 100, left: 100 }); return; }
    const W = window.innerWidth;
    const H = window.innerHeight;
    const POPOVER_W = 340;
    const POPOVER_MAX_H = Math.min(440, H - 80);
    // Prefer below the trigger; fall back above if no room.
    const wantBelow = anchorRect.bottom + POPOVER_MAX_H + 16 < H;
    let top = wantBelow ? anchorRect.bottom + 6 : anchorRect.top - POPOVER_MAX_H - 6;
    // Clamp horizontally so the popover doesn't fall off-screen on the right.
    let left = Math.min(anchorRect.left, W - POPOVER_W - 12);
    left = Math.max(12, left);
    top = Math.max(12, Math.min(top, H - 80));
    setPos({ top, left });
  }, [anchorRect]);

  const data = query.data;
  const metric: MethodologyMetric | undefined = data?.methodology?.[panelId];

  return (
    <div
      ref={popoverRef}
      role="dialog"
      aria-label={`How "${PANEL_TITLES[panelId] || panelId}" is calculated`}
      style={{
        position: "fixed",
        top: pos?.top ?? 100,
        left: pos?.left ?? 100,
        width: 340,
        maxHeight: 440,
        overflow: "auto",
        background: "#fff",
        border: "1px solid #E2E8F0",
        borderRadius: 10,
        boxShadow: "0 10px 30px rgba(15, 23, 42, 0.18)",
        zIndex: 1100,
        padding: 14,
        fontSize: 13,
        color: COLORS.textPrimary,
        animation: "fadeIn 120ms ease-out",
      }}
    >
      <header style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 6,
      }}>
        <div style={{ fontWeight: 700, fontSize: 13 }}>
          {PANEL_TITLES[panelId] || panelId}
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{
            border: "none", background: "transparent",
            cursor: "pointer", fontSize: 18, lineHeight: 1,
            color: COLORS.textMuted, padding: 0,
          }}
        >×</button>
      </header>

      {query.isLoading && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0" }}>
          <Spinner />
          <span style={{ fontSize: 12, color: COLORS.textMuted }}>Loading…</span>
        </div>
      )}

      {query.isError && (
        <p style={{ fontSize: 12, color: COLORS.riskHigh, margin: "8px 0" }}>
          Couldn’t load methodology for this panel.
        </p>
      )}

      {metric && (
        <>
          <p style={{
            margin: "4px 0 8px", fontSize: 12, lineHeight: 1.55,
            color: COLORS.textSecondary,
          }}>
            {metric.simple_logic}
          </p>

          {Object.keys(metric.your_inputs ?? {}).length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{
                fontSize: 9, fontWeight: 700, letterSpacing: 0.4,
                textTransform: "uppercase", color: COLORS.textMuted, marginBottom: 4,
              }}>
                For this article
              </div>
              <div style={{ display: "grid", gap: 3, fontSize: 11 }}>
                {compactInputs(metric.your_inputs as Record<string, unknown>).map(([k, v]) => (
                  <div key={k} style={{
                    display: "flex", justifyContent: "space-between", gap: 12,
                  }}>
                    <span style={{ color: COLORS.textMuted }}>{k}</span>
                    <code style={{ fontSize: 10, color: COLORS.textPrimary }}>
                      {formatInputValue(v)}
                    </code>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={() => setShowFormula(s => !s)}
            style={{
              marginTop: 12, fontSize: 10, fontWeight: 700, letterSpacing: 0.4,
              textTransform: "uppercase", color: COLORS.brand,
              background: "none", border: "none", padding: 0, cursor: "pointer",
            }}
          >
            {showFormula ? "Hide formula" : "Show formula"}
          </button>

          {showFormula && (
            <div style={{ marginTop: 8 }}>
              <pre style={{
                fontSize: 10, lineHeight: 1.5,
                background: "#F8FAFC", border: "1px solid #E2E8F0",
                padding: "6px 8px", borderRadius: 5,
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                margin: 0, color: COLORS.textPrimary,
              }}>
                {metric.formula_human}
              </pre>
              {metric.ontology_anchors?.length > 0 && (
                <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {metric.ontology_anchors.map(a => (
                    <span key={a} style={{
                      fontSize: 9, padding: "2px 6px", borderRadius: 8,
                      background: "#E2E8F0", color: COLORS.textPrimary,
                    }}>{a}</span>
                  ))}
                </div>
              )}
              <code style={{
                display: "block", marginTop: 6, fontSize: 9, color: COLORS.textMuted,
              }}>
                Source: {metric.source}
              </code>
            </div>
          )}
        </>
      )}
    </div>
  );
}
