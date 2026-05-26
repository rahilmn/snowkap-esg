/** Phase 28 / Feature 2 — MethodologyDrawer.
 *
 * Side-drawer triggered by the "i" icon on any article. Shows:
 *
 *   1. Per-role analysis (CFO / CEO / Analyst):
 *      - Why is this important for me?
 *      - How does it impact business?
 *      - Analysis result
 *      - Simple logic (one-liner explaining the score)
 *
 *   2. Per-metric methodology (criticality, relevance, persona_boost,
 *      sentiment_trajectory, framework_match):
 *      - Source module (engine/analysis/criticality_scorer.py)
 *      - Simple-language explanation
 *      - Human-readable formula
 *      - Ontology anchors used
 *      - YOUR INPUTS — actual component values for this article
 *
 * Fetches lazily on open so closing/reopening for the same article is
 * a cached query. Role-specific weights come from the `role` prop;
 * switching role re-fetches.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { methodology, type MethodologyResponse } from "@/lib/api";
import { COLORS } from "@/lib/designTokens";
import { Spinner } from "@/components/ui/Spinner";

interface Props {
  articleId: string | null;
  role?: string;
  onClose: () => void;
  /** Phase 33 — when set, narrow the drawer to a single bullet/panel
   * (e.g. "what_changed", "why_it_matters"). The fetch passes
   * `?panel=...` so the response only carries that block. The drawer
   * renders the scoped view with a different header. Null/undefined
   * keeps the legacy full-methodology behaviour. */
  panelId?: string | null;
}

type Tab = "role" | "methods";

const BULLET_LABEL: Record<string, string> = {
  what_changed: "What changed",
  why_it_matters: "Why it matters",
  what_it_triggers: "What it triggers",
  what_to_watch: "What to watch",
};

export function MethodologyDrawer({ articleId, role, onClose, panelId }: Props) {
  const [tab, setTab] = useState<Tab>("role");
  const open = !!articleId;
  const scoped = !!panelId;

  const query = useQuery({
    queryKey: ["methodology", articleId, role, panelId ?? null],
    queryFn: () => (articleId
      ? methodology.fetch(articleId, role, panelId ?? undefined)
      : Promise.resolve(null)),
    enabled: open,
    staleTime: 60_000 * 5,
  });

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, background: "rgba(15, 23, 42, 0.45)",
          zIndex: 1000, animation: "fadeIn 120ms ease-out",
        }}
      />
      {/* Drawer panel */}
      <aside
        role="dialog"
        aria-label="How this is calculated"
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: "min(440px, 96vw)",
          background: "#fff",
          boxShadow: "-4px 0 24px rgba(15, 23, 42, 0.15)",
          zIndex: 1001,
          display: "flex", flexDirection: "column",
        }}
      >
        <header style={{
          padding: "16px 20px", borderBottom: "1px solid #E2E8F0",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <div>
            <h2 style={{ fontSize: 16, margin: 0, color: COLORS.textPrimary, fontWeight: 700 }}>
              {scoped && panelId
                ? `Why we said this — "${BULLET_LABEL[panelId] || panelId}"`
                : "How this is calculated"}
            </h2>
            {query.data?.headline && (
              <p style={{
                fontSize: 11, margin: "4px 0 0", color: COLORS.textMuted,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {query.data.headline}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              border: "none", background: "transparent", cursor: "pointer",
              fontSize: 22, color: COLORS.textMuted, padding: 4,
            }}
          >
            ×
          </button>
        </header>

        {/* Tab switch — hidden in scoped (per-bullet) mode since the
            scoped view renders a single bullet's block, no role split. */}
        {!scoped && (
          <div style={{ display: "flex", borderBottom: "1px solid #E2E8F0" }}>
            {(["role", "methods"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  flex: 1, padding: "10px 12px", fontSize: 12, fontWeight: 600,
                  border: "none", cursor: "pointer",
                  color: tab === t ? COLORS.brand : COLORS.textMuted,
                  background: tab === t ? `${COLORS.brand}0F` : "transparent",
                  borderBottom: tab === t
                    ? `2px solid ${COLORS.brand}`
                    : "2px solid transparent",
                  transition: "background-color 120ms ease, color 120ms ease",
                }}
              >
                {t === "role" ? "Why this matters" : "How we calculated"}
              </button>
            ))}
          </div>
        )}

        <div style={{ flex: 1, overflow: "auto", padding: "16px 20px 32px" }}>
          {query.isLoading && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: 32 }}>
              <Spinner />
              <span style={{ fontSize: 12, color: COLORS.textMuted }}>
                Loading methodology…
              </span>
            </div>
          )}
          {query.isError && (
            <p style={{ fontSize: 12, color: COLORS.riskHigh }}>
              Couldn’t load the breakdown. Click an article in the feed to
              re-trigger on-demand enrichment.
            </p>
          )}
          {scoped && query.data && panelId && (
            <PanelSection data={query.data} panelId={panelId} />
          )}
          {!scoped && query.data && tab === "role" && (
            <RoleSection data={query.data} activeRole={role} />
          )}
          {!scoped && query.data && tab === "methods" && (
            <MethodsSection data={query.data} />
          )}
        </div>
      </aside>
    </>
  );
}


function RoleSection({ data, activeRole }: { data: MethodologyResponse; activeRole?: string }) {
  const order = ["cfo", "ceo", "esg-analyst"] as const;
  return (
    <div style={{ display: "grid", gap: 18 }}>
      {order.map((roleKey) => {
        const block = data.role_explainer[roleKey];
        if (!block) return null;
        const isActive = activeRole === roleKey;
        return (
          <section
            key={roleKey}
            style={{
              border: isActive
                ? `1px solid ${COLORS.brand}`
                : "1px solid #E2E8F0",
              borderRadius: 10, padding: "12px 14px",
              background: isActive ? `${COLORS.brand}08` : "#FFF",
            }}
          >
            <div style={{
              fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
              textTransform: "uppercase", color: isActive ? COLORS.brand : COLORS.textMuted,
              marginBottom: 6,
            }}>
              {roleKey === "esg-analyst" ? "ANALYST" : roleKey.toUpperCase()}
              {isActive ? " · YOUR VIEW" : ""}
            </div>
            {block.why_important_for_me && (
              <ExplainerRow label="Why it matters to you" value={block.why_important_for_me} />
            )}
            {block.how_it_impacts_business && (
              <ExplainerRow label="How it impacts the business" value={block.how_it_impacts_business} />
            )}
            {block.analysis_result && (
              <ExplainerRow label="Analysis result" value={block.analysis_result} accent />
            )}
            {block.simple_logic && (
              <p style={{
                fontSize: 10, color: COLORS.textMuted, margin: "8px 0 0",
                fontStyle: "italic", lineHeight: 1.5,
              }}>
                {block.simple_logic}
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}


/**
 * Phase 33 — Scoped per-bullet view. Renders only the single bullet's
 * methodology block (no role-split, no all-metrics list). The (i) icon
 * on each bullet of the UnifiedAnalysisCard opens the drawer with
 * `panelId` set, so the drawer focuses on just that bullet.
 */
function PanelSection({
  data, panelId,
}: { data: MethodologyResponse; panelId: string }) {
  const m = data.methodology[panelId];
  if (!m) {
    return (
      <p style={{ fontSize: 12, color: COLORS.textMuted, fontStyle: "italic" }}>
        We don't have a methodology block for "{panelId}" on this article yet —
        the on-demand pipeline may still be running. Refresh in 30 seconds.
      </p>
    );
  }
  return (
    <section style={{ display: "grid", gap: 14 }}>
      {/* The plain-English explainer is the primary content */}
      <div>
        <div style={{
          fontSize: 10, fontWeight: 800, letterSpacing: 0.5,
          textTransform: "uppercase", color: COLORS.brand, marginBottom: 4,
        }}>
          How we built this bullet
        </div>
        <p style={{
          fontSize: 13.5, lineHeight: 1.6, color: COLORS.textPrimary, margin: 0,
        }}>
          {m.simple_logic}
        </p>
      </div>

      {/* Source + formula collapsed by default — power-user reference */}
      <details style={{
        background: "#F8FAFC", border: "1px solid #E2E8F0",
        borderRadius: 8, padding: "10px 12px",
      }}>
        <summary style={{
          fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
          textTransform: "uppercase", color: COLORS.textMuted,
          cursor: "pointer",
        }}>
          Show formula + source
        </summary>
        <code style={{
          display: "block", fontSize: 10, marginTop: 8,
          color: COLORS.textMuted,
        }}>
          {m.source}
        </code>
        {m.formula_human && (
          <pre style={{
            fontSize: 10, lineHeight: 1.45,
            background: "#FFFFFF", border: "1px solid #E2E8F0",
            padding: "8px 10px", borderRadius: 6,
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            color: COLORS.textPrimary, marginTop: 6, marginBottom: 0,
          }}>
            {m.formula_human}
          </pre>
        )}
      </details>

      {/* This article's actual computed values */}
      {Object.keys(m.your_inputs || {}).length > 0 && (
        <div>
          <div style={{
            fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
            textTransform: "uppercase", color: COLORS.brand, marginBottom: 6,
          }}>
            For this article
          </div>
          <ul style={{
            margin: 0, padding: 0, listStyle: "none",
            display: "grid", gap: 4,
            fontSize: 12, color: COLORS.textSecondary,
          }}>
            {Object.entries(m.your_inputs).map(([k, v]) => (
              <li key={k} style={{
                display: "flex", justifyContent: "space-between", gap: 8,
                padding: "3px 0", borderBottom: "1px solid #F1F5F9",
              }}>
                <span style={{ color: COLORS.textMuted }}>{k.replace(/_/g, " ")}</span>
                <strong style={{ color: COLORS.textPrimary, textAlign: "right" }}>
                  {formatInput(v)}
                </strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Ontology anchors (power-user; small print) */}
      {m.ontology_anchors && m.ontology_anchors.length > 0 && (
        <p style={{
          fontSize: 10, color: COLORS.textMuted, margin: 0,
          fontStyle: "italic",
        }}>
          Ontology anchors: {m.ontology_anchors.join(" · ")}
        </p>
      )}
    </section>
  );
}

function formatInput(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.map(String).slice(0, 5).join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "number") return Number.isFinite(v) ? v.toFixed(2).replace(/\.00$/, "") : "—";
  return String(v);
}


function ExplainerRow({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ margin: "8px 0" }}>
      <div style={{
        fontSize: 9, fontWeight: 700, letterSpacing: 0.4,
        textTransform: "uppercase", color: accent ? COLORS.brand : COLORS.textMuted,
      }}>
        {label}
      </div>
      <p style={{
        fontSize: 13, margin: "4px 0 0", lineHeight: 1.55,
        color: accent ? COLORS.textPrimary : COLORS.textSecondary,
        fontWeight: accent ? 600 : 400,
      }}>
        {value}
      </p>
    </div>
  );
}


function MethodsSection({ data }: { data: MethodologyResponse }) {
  const metrics = Object.values(data.methodology);
  return (
    <div style={{ display: "grid", gap: 18 }}>
      {metrics.map((m) => (
        <section
          key={m.metric}
          style={{
            border: "1px solid #E2E8F0", borderRadius: 10, padding: "12px 14px",
          }}
        >
          <header style={{ marginBottom: 6 }}>
            <div style={{
              fontSize: 11, fontWeight: 800, letterSpacing: 0.5,
              textTransform: "uppercase", color: COLORS.textPrimary,
            }}>
              {prettyMetric(m.metric)}
              {m.band && (
                <span style={{
                  marginLeft: 8, fontSize: 9, fontWeight: 700,
                  padding: "2px 6px", borderRadius: 8,
                  background: `${COLORS.brand}1A`, color: COLORS.brand,
                }}>
                  {m.band}
                </span>
              )}
            </div>
            <code style={{ fontSize: 10, color: COLORS.textMuted }}>{m.source}</code>
          </header>
          <p style={{ fontSize: 12, margin: "6px 0", lineHeight: 1.5, color: COLORS.textSecondary }}>
            {m.simple_logic}
          </p>
          {m.formula_human && (
            <pre style={{
              fontSize: 10, lineHeight: 1.45,
              background: "#F8FAFC", border: "1px solid #E2E8F0",
              padding: "8px 10px", borderRadius: 6,
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              color: COLORS.textPrimary, margin: "6px 0",
            }}>
              {m.formula_human}
            </pre>
          )}
          {Object.keys(m.your_inputs).length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{
                fontSize: 10, fontWeight: 700, letterSpacing: 0.4,
                textTransform: "uppercase", color: COLORS.brand, cursor: "pointer",
              }}>
                Your inputs
              </summary>
              <div style={{
                marginTop: 6, display: "grid", gap: 4,
                fontSize: 11, color: COLORS.textPrimary,
              }}>
                {Object.entries(m.your_inputs).map(([k, v]) => (
                  <div key={k} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span style={{ color: COLORS.textMuted }}>{k}</span>
                    <code style={{ fontSize: 10 }}>{formatInputValue(v)}</code>
                  </div>
                ))}
              </div>
            </details>
          )}
          {m.ontology_anchors.length > 0 && (
            <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
              {m.ontology_anchors.map((a) => (
                <span
                  key={a}
                  style={{
                    fontSize: 9, padding: "2px 6px", borderRadius: 8,
                    background: "#E2E8F0", color: COLORS.textPrimary,
                  }}
                >
                  {a}
                </span>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}


function prettyMetric(k: string): string {
  return k.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}


function formatInputValue(v: unknown): string {
  if (v == null) return "n/a";
  if (typeof v === "number") return v.toFixed(3);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v).slice(0, 60);
  return String(v);
}
