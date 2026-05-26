/**
 * v3.0 Article Detail Sheet — ESG Intelligence Brief.
 *
 * Layout (4-tier structure):
 *
 * TIER 1 — PROFITABILITY IMPACT
 *   1. Key Takeaways (+ profitability_connection)
 *   2. Financial Impact & Timeline
 *   3. Risk Assessment — full matrix or spotlight
 *
 * TIER 2 — STRATEGIC CONTEXT
 *   4. ESG Relevance Score
 *   5. Impact Analysis — 6 dimensions
 *   6. Framework Alignment
 *
 * TIER 3 — ACTION & INTELLIGENCE
 *   7. AI Recommendations (REREACT) + Inline Q&A
 *   8. Executive Insight
 *
 * TIER 4 — SUPPORTING EVIDENCE (grouped collapsible)
 *   9. Narrative Intelligence
 *   10. NLP Evidence
 *   11. Geographic Intelligence
 *   12. Causal Chain
 *   13. Related Coverage
 *   14. Net Impact Summary
 *
 * Action Buttons
 */

import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { agent, news as newsApi } from "../../lib/api";
import { COLORS, SHADOWS, RADII } from "../../lib/designTokens";
import { PriorityBadge } from "../ui/PriorityBadge";
import { ShareArticleButton } from "../sharing/ShareArticleButton";
import { useAuthStore } from "@/stores/authStore";
import { VerticalCausalChain } from "./VerticalCausalChain";
import { EsgThemeBar } from "./EsgThemeBar";
import { NarrativeIntelligence } from "./NarrativeIntelligence";
import { RiskMatrixDisplay } from "./RiskMatrixDisplay";
import { FrameworkAlignmentV2 } from "./FrameworkAlignmentV2";
import { NlpEvidencePanel } from "./NlpEvidencePanel";
import { GeographicSignalPanel } from "./GeographicSignalPanel";
import { RiskSpotlight } from "./RiskSpotlight";
import { UnlockFullAnalysis } from "./UnlockFullAnalysis";
import { CrispInsight } from "@/components/CrispInsight";
// POW-6 — PerspectiveSwitcher + perspectiveStore retired (Phase 32
// already collapsed the role surface). Default to 'esg-analyst' for the
// legacy callers still routed through this sheet — POW-6 will replace
// them with the new Power-of-Now ArticleSheet on /now.
import { useRolePanels } from "@/hooks/useRolePanels";
import { useIsMobile } from "@/hooks/useIsMobile";
import { MethodologyDrawer } from "@/components/explainer/MethodologyDrawer";
import { PanelInfoPopover } from "@/components/explainer/PanelInfoPopover";
import { RoleSummary } from "@/components/insight/RoleSummary";
import { UnifiedAnalysisCard } from "@/components/insight/UnifiedAnalysisCard";
import { TLDRLine } from "@/components/insight/TLDRLine";
import type { UnifiedAnalysis } from "@/types";
import { useRoleEssentials, type Role as EssentialRole } from "@/hooks/useRoleEssentials";
// Phase 25 W10 — "Why this matters to YOU" personal stakes card. Renders
// nothing when stakes_for_company is empty so this is purely additive.
import { PersonalStakesCard } from "./PersonalStakesCard";
import { formatCurrency } from "../../lib/utils";
import type { Article } from "../../types";
import type { CrispView as NewCrispView } from "@/lib/snowkap-api";

interface ArticleDetailSheetProps {
  article: Article | null;
  onClose: () => void;
}

/* Collapsible section wrapper.
 * Phase 29 — optional `panelId` + `onInfoClick` add a per-panel "i"
 * icon next to the title. When the user clicks "i", the parent opens
 * `PanelInfoPopover` anchored to the click location showing just THIS
 * panel's methodology (no big drawer). `panelId` must match an entry
 * in `engine/analysis/methodology_provenance.py:METRIC_DISPATCH`. */
function Section({
  title,
  children,
  defaultOpen = false,
  accent,
  panelId,
  onInfoClick,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  accent?: string;
  panelId?: string;
  onInfoClick?: (rect: DOMRect, panelId: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ padding: "16px 24px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 0 8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1 }}>
          <h3
            onClick={() => setOpen(!open)}
            style={{
              fontSize: "14px", fontWeight: 600,
              color: accent || COLORS.textSecondary, margin: 0,
              textTransform: "uppercase", letterSpacing: "0.5px",
              cursor: "pointer", userSelect: "none",
            }}
          >
            {title}
          </h3>
          {panelId && onInfoClick && (
            <button
              type="button"
              aria-label={`How is "${title}" calculated?`}
              title="How is this calculated?"
              onClick={(e) => {
                const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                onInfoClick(rect, panelId);
              }}
              style={{
                width: 18, height: 18, borderRadius: 9,
                border: `1px solid ${COLORS.brand}`,
                background: "transparent", color: COLORS.brand,
                fontStyle: "italic", fontWeight: 700, fontSize: 10,
                cursor: "pointer", lineHeight: 1, padding: 0,
              }}
            >
              i
            </button>
          )}
        </div>
        <button
          onClick={() => setOpen(!open)}
          style={{ border: "none", background: "none", cursor: "pointer", padding: 0 }}
        >
          <span style={{ fontSize: "11px", color: COLORS.textMuted }}>{open ? "Hide" : "Show"}</span>
        </button>
      </div>
      {open && <div style={{ paddingBottom: "4px" }}>{children}</div>}
      <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}` }} />
    </div>
  );
}

/* Phase 29 — inline panel header with optional "i" icon. Used for
 * panels that DON'T live inside a `<Section>` wrapper (e.g. ESG Relevance
 * Score + AI Recommendations which render a raw `<h3>` today). The
 * "i" icon is gated by `panelId + onInfoClick` so callers who don't
 * want it just pass nothing and get the legacy h3 styling. */
function PanelHeaderInline({
  title, panelId, onInfoClick, accent,
}: {
  title: string;
  panelId?: string;
  onInfoClick?: (rect: DOMRect, panelId: string) => void;
  accent?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: "12px",
    }}>
      <h3 style={{
        fontSize: "14px", fontWeight: 600, color: accent || COLORS.textSecondary,
        margin: 0, textTransform: "uppercase", letterSpacing: "0.5px",
      }}>
        {title}
      </h3>
      {panelId && onInfoClick && (
        <button
          type="button"
          aria-label={`How is "${title}" calculated?`}
          title="How is this calculated?"
          onClick={(e) => {
            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            onInfoClick(rect, panelId);
          }}
          style={{
            width: 18, height: 18, borderRadius: 9,
            border: `1px solid ${COLORS.brand}`,
            background: "transparent", color: COLORS.brand,
            fontStyle: "italic", fontWeight: 700, fontSize: 10,
            cursor: "pointer", lineHeight: 1, padding: 0,
          }}
        >
          i
        </button>
      )}
    </div>
  );
}

function P({ text, fallback }: { text?: string | null; fallback?: string }) {
  return (
    <p style={{ fontSize: "14px", color: COLORS.textPrimary, lineHeight: "1.6", margin: "4px 0 8px" }}>
      {text || fallback || "Analysis not available."}
    </p>
  );
}

/* New 6D ESG Relevance Score from deep_insight.esg_relevance_score
 * Phase 33 — only dimensions scoring >= 4/10 render by default. Lower
 * dimensions indicate "not material to this event" (e.g. Environment
 * 0/10 on a banking governance article) — they're noise rather than
 * useful signal. A "Show all 6 dimensions" toggle reveals the rest. */
const ESG_RELEVANCE_VISIBLE_THRESHOLD = 4;

function ESGRelevanceScore6D({ score }: { score: Record<string, { score: number; rationale: string }> }) {
  const [showAll, setShowAll] = useState(false);
  const dims = [
    { key: "environment",          label: "Environment",         color: "#16a34a" },
    { key: "social",               label: "Social",              color: "#2563eb" },
    { key: "governance",           label: "Governance",          color: "#7c3aed" },
    { key: "financial_materiality",label: "Financial",           color: COLORS.brand },
    { key: "regulatory_exposure",  label: "Regulatory",          color: "#dc2626" },
    { key: "stakeholder_impact",   label: "Stakeholders",        color: "#0891b2" },
  ];
  const avg = dims.reduce((s, d) => s + (score[d.key]?.score ?? 0), 0) / dims.length;

  // Phase 33 — partition into high-relevance (visible) vs low-relevance (hidden by default).
  const highRelevance = dims.filter((d) => (score[d.key]?.score ?? 0) >= ESG_RELEVANCE_VISIBLE_THRESHOLD);
  const lowRelevance = dims.filter((d) => (score[d.key]?.score ?? 0) < ESG_RELEVANCE_VISIBLE_THRESHOLD);
  const visibleDims = showAll ? dims : highRelevance;

  const renderDim = (d: { key: string; label: string; color: string }) => {
    const val = score[d.key];
    if (!val) return null;
    return (
      <div key={d.key}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "3px" }}>
          <span style={{ fontSize: "11px", fontWeight: 600, color: COLORS.textSecondary }}>{d.label}</span>
          <span style={{ fontSize: "11px", fontWeight: 700, color: d.color }}>{val.score}/10</span>
        </div>
        <div style={{ height: "4px", borderRadius: "2px", backgroundColor: COLORS.textDisabled, overflow: "hidden", marginBottom: "3px" }}>
          <div style={{ width: `${(val.score / 10) * 100}%`, height: "100%", backgroundColor: d.color, borderRadius: "2px" }} />
        </div>
        {val.rationale && (
          <p style={{ fontSize: "10px", color: COLORS.textMuted, margin: 0, lineHeight: "1.4" }}>{val.rationale}</p>
        )}
      </div>
    );
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginBottom: "14px" }}>
        <span style={{ fontSize: "28px", fontWeight: 700, color: avg >= 7 ? COLORS.brand : avg >= 4 ? COLORS.textPrimary : COLORS.textMuted }}>
          {avg.toFixed(1)}
        </span>
        <span style={{ fontSize: "12px", color: COLORS.textMuted }}>
          /10 avg across 6 dimensions
          {!showAll && lowRelevance.length > 0 && (
            <span style={{ marginLeft: 8, fontSize: 10, fontStyle: "italic" }}>
              · {lowRelevance.length} low-relevance hidden
            </span>
          )}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {visibleDims.length > 0
          ? visibleDims.map(renderDim)
          : (
            <p style={{ fontSize: 11, color: COLORS.textMuted, fontStyle: "italic", margin: 0 }}>
              No dimensions scored ≥ {ESG_RELEVANCE_VISIBLE_THRESHOLD}/10 — this event is below the material-relevance threshold across all 6 dimensions.
            </p>
          )}
      </div>
      {lowRelevance.length > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(!showAll)}
          style={{
            marginTop: 10,
            fontSize: 11, fontWeight: 600,
            color: COLORS.brand,
            background: "transparent",
            border: "none",
            cursor: "pointer",
            padding: 0,
          }}
        >
          {showAll
            ? `Hide ${lowRelevance.length} low-relevance dimension${lowRelevance.length === 1 ? "" : "s"}`
            : `Show all 6 dimensions (${lowRelevance.length} hidden — score < ${ESG_RELEVANCE_VISIBLE_THRESHOLD})`}
        </button>
      )}
    </div>
  );
}

/* 6D relevance radar mini-display (v2.1 — includes Impact Materiality) */
function RelevanceBreakdown({ breakdown }: { breakdown: Record<string, number> | null }) {
  if (!breakdown) return null;
  const dims = [
    { key: "esg_correlation", label: "ESG", max: 2 },
    { key: "financial_impact", label: "Financial", max: 2 },
    { key: "compliance_risk", label: "Compliance", max: 2 },
    { key: "supply_chain_impact", label: "Supply Chain", max: 2 },
    { key: "people_impact", label: "People", max: 2 },
    { key: "impact_materiality", label: "Impact", max: 2 },
  ];
  const total = breakdown.total ?? 0;
  const tier = breakdown.tier as string | undefined;

  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <span style={{ fontSize: "28px", fontWeight: 700, color: total >= 7 ? COLORS.brand : total >= 4 ? COLORS.textPrimary : COLORS.textMuted }}>
          {total.toFixed(1)}
        </span>
        <span style={{ fontSize: "12px", color: COLORS.textMuted }}>/10</span>
        {tier && (
          <span style={{
            fontSize: "11px", fontWeight: 600, padding: "2px 8px", borderRadius: "12px",
            backgroundColor: tier === "HOME" ? "rgba(223,89,0,0.12)" : tier === "SECONDARY" ? "rgba(0,0,0,0.06)" : "rgba(255,0,0,0.08)",
            color: tier === "HOME" ? COLORS.brand : tier === "SECONDARY" ? COLORS.textSecondary : COLORS.riskHigh,
          }}>
            {tier}
          </span>
        )}
      </div>
      <div className="grid grid-cols-5 gap-1">
        {dims.map((d) => {
          const val = breakdown[d.key] ?? 0;
          return (
            <div key={d.key} className="text-center">
              <div style={{ height: "4px", borderRadius: "2px", backgroundColor: COLORS.textDisabled, overflow: "hidden" }}>
                <div style={{
                  width: `${(val / d.max) * 100}%`, height: "100%",
                  backgroundColor: val >= 2 ? COLORS.brand : val >= 1 ? COLORS.elevated : COLORS.textMuted,
                  borderRadius: "2px",
                }} />
              </div>
              <span style={{ fontSize: "10px", color: COLORS.textMuted }}>{d.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* REREACT recommendation card — enhanced with profitability link + Ask AI */
function RecommendationCard({
  rec,
  index,
  articleId,
}: {
  rec: {
    type: string; title: string; description: string;
    framework?: string; framework_section?: string;
    responsible_party?: string; deadline?: string;
    estimated_budget?: string; success_criterion?: string;
    urgency: string; confidence: string; validation_notes?: string;
    profitability_link?: string;
    roi_percentage?: number;
    payback_months?: number;
    priority?: string;
    risk_of_inaction?: number;
  };
  index: number;
  articleId?: string | number;
}) {
  const navigate = useNavigate();
  const confColor = rec.confidence === "HIGH" ? "#16a34a" : rec.confidence === "MEDIUM" ? COLORS.brand : "#dc2626";
  const frameworkDisplay = rec.framework_section || rec.framework;

  const handleAskAIAboutRec = () => {
    // Phase 31 — route to /chat (Phase C surface with MCP + memory +
    // persistence), not the legacy /agent page. The chat seed picks up
    // the article-context fields via URL params; for the
    // recommendation-specific deep-dive we keep the prompt in session
    // storage and prime the input on first /chat mount.
    sessionStorage.setItem("chat_context", JSON.stringify({
      article_id: articleId,
      recommendation_title: rec.title,
      recommendation_description: rec.description,
      prompt: `Walk me through this recommendation in detail: "${rec.title}" — ${rec.description}. What's the supporting evidence and how does the engine score the inaction risk?`,
    }));
    const params = new URLSearchParams();
    if (articleId) params.set("article", String(articleId));
    navigate(`/chat?${params.toString()}`);
  };

  return (
    <div style={{ backgroundColor: COLORS.bgLight, borderRadius: "8px", padding: "12px", marginBottom: "8px", borderLeft: `3px solid ${confColor}` }}>
      <div className="flex items-center justify-between mb-1">
        <span style={{ fontSize: "11px", fontWeight: 600, color: COLORS.textSecondary, textTransform: "uppercase", letterSpacing: "0.3px" }}>
          {rec.type || `Recommendation ${index + 1}`}
        </span>
        <div className="flex items-center gap-2">
          {rec.priority && (
              <span style={{
                  fontSize: "9px", fontWeight: 600, padding: "1px 5px", borderRadius: "3px",
                  backgroundColor: rec.priority === "CRITICAL" ? "rgba(220,38,38,0.08)" : rec.priority === "HIGH" ? "rgba(223,89,0,0.08)" : "rgba(136,136,136,0.08)",
                  color: rec.priority === "CRITICAL" ? "#dc2626" : rec.priority === "HIGH" ? COLORS.brand : COLORS.textMuted,
                  letterSpacing: "0.3px",
              }}>
                  {rec.priority}
              </span>
          )}
          <span style={{ fontSize: "9px", fontWeight: 700, padding: "1px 5px", borderRadius: "3px", backgroundColor: confColor, color: "#fff" }}>
            {rec.confidence}
          </span>
        </div>
      </div>
      <p style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textPrimary, margin: "4px 0" }}>{rec.title}</p>
      <p style={{ fontSize: "13px", color: COLORS.textSecondary, lineHeight: "1.5", margin: "4px 0 8px" }}>{rec.description}</p>

      {/* Profitability link callout */}
      {rec.profitability_link && (
        <div style={{
          backgroundColor: "rgba(22,163,74,0.05)", borderRadius: "4px", padding: "6px 10px",
          margin: "6px 0", borderLeft: "2px solid rgba(22,163,74,0.4)",
        }}>
          <p style={{ fontSize: "11px", fontWeight: 500, color: COLORS.textSecondary, margin: 0, lineHeight: "1.4" }}>
            <span style={{ color: "#16a34a", fontWeight: 600, marginRight: "4px" }}>$</span>
            {rec.profitability_link}
          </p>
        </div>
      )}

      {/* Actionable details grid */}
      {(rec.responsible_party || rec.deadline || rec.estimated_budget || rec.success_criterion) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 12px", margin: "8px 0", padding: "8px 10px", backgroundColor: "rgba(0,0,0,0.03)", borderRadius: "6px" }}>
          {rec.responsible_party && (
            <div>
              <span style={{ fontSize: "10px", color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Owner</span>
              <p style={{ fontSize: "12px", color: COLORS.textPrimary, fontWeight: 500, margin: "1px 0 0" }}>{rec.responsible_party}</p>
            </div>
          )}
          {rec.deadline && (
            <div>
              <span style={{ fontSize: "10px", color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Deadline</span>
              <p style={{ fontSize: "12px", color: COLORS.textPrimary, fontWeight: 500, margin: "1px 0 0" }}>{rec.deadline}</p>
            </div>
          )}
          {rec.estimated_budget && (
            <div>
              <span style={{ fontSize: "10px", color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Budget</span>
              <p style={{ fontSize: "12px", color: COLORS.textPrimary, fontWeight: 500, margin: "1px 0 0" }}>{rec.estimated_budget}</p>
            </div>
          )}
          {rec.success_criterion && (
            <div style={{ gridColumn: "1 / -1" }}>
              <span style={{ fontSize: "10px", color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px" }}>Success Criterion</span>
              <p style={{ fontSize: "12px", color: COLORS.textPrimary, fontWeight: 500, margin: "1px 0 0" }}>{rec.success_criterion}</p>
            </div>
          )}
        </div>
      )}

      <div className="flex items-center justify-between flex-wrap" style={{ gap: "4px", marginTop: "6px" }}>
        <div className="flex items-center gap-2 flex-wrap">
          {frameworkDisplay && <span style={{ fontSize: "10px", color: COLORS.framework, fontWeight: 500 }}>{frameworkDisplay}</span>}
          {rec.urgency && (
            <span style={{
              fontSize: "9px", padding: "1px 5px", borderRadius: "3px",
              backgroundColor: rec.urgency === "immediate" ? "rgba(220,38,38,0.08)" : "rgba(0,0,0,0.04)",
              color: rec.urgency === "immediate" ? "#dc2626" : COLORS.textSecondary,
            }}>
              {rec.urgency}
            </span>
          )}
          {rec.roi_percentage != null && (
            <span style={{ fontSize: "10px", color: rec.roi_percentage > 0 ? "#16a34a" : "#dc2626", fontWeight: 600 }}>
              ROI: {rec.roi_percentage > 0 ? "+" : ""}{rec.roi_percentage}%
            </span>
          )}
          {rec.payback_months != null && (
            <span style={{ fontSize: "10px", color: COLORS.textMuted }}>
              Payback: {rec.payback_months < 12 ? `${rec.payback_months}mo` : `${(rec.payback_months / 12).toFixed(1)}yr`}
            </span>
          )}
          {rec.risk_of_inaction != null && (
            <span
              title="Risk of inaction score: 1 (low) → 10 (critical)"
              style={{
                fontSize: "10px", fontWeight: 600,
                color: rec.risk_of_inaction >= 8 ? "#dc2626" : rec.risk_of_inaction >= 6 ? COLORS.brand : COLORS.textMuted,
              }}
            >
              Inaction risk: {rec.risk_of_inaction}/10
            </span>
          )}
        </div>
        <button
          onClick={handleAskAIAboutRec}
          style={{
            fontSize: "10px", fontWeight: 500, color: COLORS.textSecondary,
            background: "none", border: "none", cursor: "pointer",
            padding: "2px 0", textDecoration: "underline", textUnderlineOffset: "2px",
          }}
        >
          Discuss in chat &rarr;
        </button>
      </div>
      {rec.validation_notes && (
        <p style={{ fontSize: "11px", color: COLORS.textMuted, marginTop: "6px", fontStyle: "italic" }}>{rec.validation_notes}</p>
      )}
    </div>
  );
}

/* Event deduplication: Related Coverage cluster */
function RelatedCoverage({ article }: { article: Article }) {
  const cluster = (article.scoring_metadata as Record<string, unknown> | null)?.event_cluster as {
    is_primary?: boolean;
    cluster_size?: number;
    consolidated_priority?: number;
    consolidated_risks?: Record<string, number>;
  } | undefined;

  if (!cluster || !cluster.cluster_size || cluster.cluster_size <= 1) return null;

  return (
    <div style={{ padding: "12px 24px 0" }}>
      <div style={{
        backgroundColor: "rgba(37,99,235,0.06)", borderRadius: "8px",
        padding: "12px 14px", border: "1px solid rgba(37,99,235,0.15)",
      }}>
        <div className="flex items-center gap-2">
          <span style={{ fontSize: "13px", fontWeight: 600, color: "#2563eb" }}>
            Related Coverage
          </span>
          <span style={{
            fontSize: "10px", fontWeight: 700, padding: "1px 6px", borderRadius: "10px",
            backgroundColor: "rgba(37,99,235,0.12)", color: "#2563eb",
          }}>
            {cluster.cluster_size} articles
          </span>
        </div>
        <p style={{ fontSize: "12px", color: COLORS.textSecondary, marginTop: "4px" }}>
          {cluster.is_primary
            ? `This is the primary article in a cluster of ${cluster.cluster_size} covering the same event. Risk scores consolidated across all coverage.`
            : `Part of a ${cluster.cluster_size}-article cluster. Consolidated risk score applied from primary coverage.`}
        </p>
        {cluster.consolidated_risks && Object.keys(cluster.consolidated_risks).length > 0 && (
          <div className="flex gap-2 flex-wrap mt-2">
            {Object.entries(cluster.consolidated_risks).map(([risk, score]) => (
              <span key={risk} style={{
                fontSize: "10px", padding: "2px 6px", borderRadius: "4px",
                backgroundColor: "rgba(0,0,0,0.05)", color: COLORS.textSecondary,
              }}>
                {risk}: {score}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* Inline Q&A panel — Phase 5C */
function InsightQA({ articleId, companyId, suggestedQuestions }: {
  articleId: string;
  companyId: string;
  suggestedQuestions?: string[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState<Array<{ role: string; content: string }>>([]);

  const handleAsk = async (q: string) => {
    setLoading(true);
    setQuestion(q);
    try {
      // Use dedicated insight chat endpoint if company_id available, else fall back to agent
      const result = companyId
        ? await agent.askAboutInsights(articleId, companyId, q, history)
        : await agent.askAboutNews(articleId, q);
      const responseText = result.response || JSON.stringify(result);
      setAnswer(responseText);
      // Maintain rolling conversation history (last 6 turns)
      setHistory(prev => [
        ...prev.slice(-10),
        { role: "user", content: q },
        { role: "assistant", content: responseText },
      ]);
    } catch {
      setAnswer("Unable to process question. Try again.");
    }
    setLoading(false);
  };

  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        style={{
          fontSize: "13px", fontWeight: 500, color: COLORS.textSecondary,
          background: "none", border: "none", cursor: "pointer",
          padding: "8px 0", textDecoration: "underline", textUnderlineOffset: "3px",
        }}
      >
        Ask questions about this analysis &rarr;
      </button>
    );
  }

  return (
    <div style={{
      backgroundColor: "transparent", borderRadius: "8px", padding: "12px 0",
      marginTop: "10px",
    }}>
      <div style={{ display: "flex", gap: "5px", flexWrap: "wrap", marginBottom: "8px" }}>
        {(suggestedQuestions || []).map((q, i) => (
          <button
            key={i}
            onClick={() => handleAsk(q)}
            disabled={loading}
            style={{
              fontSize: "10px", fontWeight: 500, color: COLORS.brand,
              backgroundColor: "rgba(223,89,0,0.06)", border: `1px solid rgba(223,89,0,0.15)`,
              borderRadius: "12px", padding: "3px 8px", cursor: "pointer",
              opacity: loading ? 0.6 : 1,
            }}
          >
            {q}
          </button>
        ))}
      </div>
      <div style={{ display: "flex", gap: "6px" }}>
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="Ask about this analysis..."
          onKeyDown={e => e.key === "Enter" && question.trim() && handleAsk(question)}
          style={{
            flex: 1, fontSize: "12px", padding: "7px 10px",
            borderRadius: "6px", border: `1px solid ${COLORS.textDisabled}`,
            backgroundColor: COLORS.bgWhite, color: COLORS.textPrimary,
            outline: "none", fontFamily: "inherit",
          }}
        />
        <button
          onClick={() => question.trim() && handleAsk(question)}
          disabled={loading || !question.trim()}
          style={{
            fontSize: "12px", fontWeight: 600, padding: "7px 12px",
            borderRadius: "6px", border: "none", cursor: "pointer",
            backgroundColor: COLORS.darkCard, color: COLORS.bgWhite,
            opacity: loading || !question.trim() ? 0.5 : 1,
            fontFamily: "inherit",
          }}
        >
          {loading ? "..." : "Ask"}
        </button>
      </div>
      {answer && (
        <div style={{
          marginTop: "8px", padding: "10px", borderRadius: "6px",
          backgroundColor: "transparent", border: `1px solid ${COLORS.textDisabled}`,
        }}>
          <p style={{ fontSize: "12px", color: COLORS.textPrimary, lineHeight: "1.6", margin: 0 }}>{answer}</p>
        </div>
      )}
    </div>
  );
}

function renderDeepDict(val: unknown): React.ReactNode {
  if (!val) return null;
  if (typeof val === "string") return <P text={val} />;
  if (typeof val === "object" && val !== null) {
    return (
      <>
        {Object.entries(val as Record<string, unknown>)
          .filter(([, v]) => v != null && v !== "" && v !== "null" && v !== "None")
          .map(([k, v]) => (
          <div key={k} style={{ marginBottom: "8px" }}>
            <span style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textSecondary, textTransform: "capitalize" }}>
              {k.replace(/_/g, " ")}
            </span>
            <P text={typeof v === "string" ? v : Array.isArray(v) ? v.join(", ") : JSON.stringify(v)} />
          </div>
        ))}
      </>
    );
  }
  return null;
}

export function ArticleDetailSheet({ article, onClose }: ArticleDetailSheetProps) {
  const navigate = useNavigate();
  // POW-6 — perspective retired; default to esg-analyst for legacy panels.
  // Cast to the union so the few remaining `=== "cfo"` / `=== "ceo"`
  // branches stay type-valid (they're dead branches now but harmless).
  const activePerspective = "esg-analyst" as "cfo" | "ceo" | "esg-analyst";

  // Phase 28 / Feature 2 — methodology drawer state.
  // Triggered by the "i" icon in the top-right; opens a side panel with
  // per-role analysis (Why matters / How impacts / Result) + per-metric
  // source + simple-language logic for every score we show.
  const [methodologyOpen, setMethodologyOpen] = useState<boolean>(false);

  // Phase 33 §5 — collapse Risk / ESG / Impact / AI Recs behind a
  // "Show full breakdown" toggle when the unified `insight.analysis`
  // block is present. Pre-Phase-32 articles render the flat layout
  // (showDetails defaults to false there because hasUnified is false,
  // and the gate is `!hasUnified || showDetails`).
  const [showDetails, setShowDetails] = useState<boolean>(false);

  // Phase 29 — per-panel info popover state. Replaces the global "i"
  // for everyday use: each kept panel gets its own "i" that opens a
  // small popover anchored next to it. The global drawer stays for
  // power users who want everything in one place.
  const [activePanelInfo, setActivePanelInfo] = useState<
    { panelId: string; anchorRect: DOMRect } | null
  >(null);

  // Phase 29 — role-essential blocks + filter helpers. `essentials` is
  // declared for future panel-level filtering hooks; not yet read by JSX
  // (Phase 29 stitches it incrementally — RoleSummary already renders).
  void useRoleEssentials(activePerspective as EssentialRole | null);

  // W4e — role-aware panel visibility. Reads insight.role_panel_order
  // (stamped by backend W4d) and returns isHidden(panelId) so we can
  // hide irrelevant panels per role without rewriting the JSX tree.
  // Falls open (no hides) when the insight predates W4d.
  // We use article?.deep_insight?.role_panel_order, falling back to {}.
  const rolePanels = useRolePanels(
    activePerspective,
    (article?.deep_insight as { role_panel_order?: never } | undefined)?.role_panel_order,
  );

  // Phase 28 / Feature 5 — mobile role-aware visibility. On viewports
  // ≤640px we additionally hide non-essential panels per the role's
  // MOBILE_ESSENTIAL_PANELS set in useRolePanels. The "Show all panels"
  // toggle below overrides this so power-users on mobile can still see
  // everything when they want to.
  const isMobile = useIsMobile();
  const [showAllOnMobile, setShowAllOnMobile] = useState<boolean>(false);
  const shouldHideOnMobile = (panelId: string): boolean =>
    isMobile && !showAllOnMobile && rolePanels.isHiddenOnMobile(panelId);

  // ── On-demand analysis state (hooks must be before early return) ──
  // Only skip trigger if deep_insight has actual content (headline exists).
  // Empty {} from cleared/stale articles must still trigger fresh analysis.
  const hasAnalysis = !!(article?.deep_insight?.headline);
  const [analysisStatus, setAnalysisStatus] = useState<"idle" | "pending" | "done" | "failed">(
    hasAnalysis ? "done" : "idle"
  );
  const [liveAnalysis, setLiveAnalysis] = useState<Record<string, unknown> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const triggeredRef = useRef(false);
  const lastArticleId = useRef<string | null>(null);

  // Phase 13 S5 — faux-progress for the on-demand analysis spinner. The
  // pipeline runs in 20-60s; static "loading…" copy makes the wait feel
  // longer. We advance through named stages every ~6s so the user sees
  // motion. Stage index is reset whenever a new analysis starts.
  // Phase 24 W3 — added the "CFO credibility check" stage as the final
  // checkpoint (mirrors backend cfo_preflight gating).
  const [analysisStage, setAnalysisStage] = useState(0);
  useEffect(() => {
    if (analysisStatus !== "pending") {
      // Reset on idle/done/failed so next pending starts at 0
      if (analysisStage !== 0) setAnalysisStage(0);
      return;
    }
    setAnalysisStage(0);
    const tick = setInterval(() => {
      // Cap at last stage (max index 5) — never claim "done" before backend confirms.
      setAnalysisStage((prev) => Math.min(prev + 1, 5));
    }, 6000);
    return () => clearInterval(tick);
    // analysisStage intentionally absent from deps — we manage it inside.
  }, [analysisStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  // Reset state when article changes (since we don't use key={} for remount)
  useEffect(() => {
    if (article?.id !== lastArticleId.current) {
      lastArticleId.current = article?.id ?? null;
      triggeredRef.current = false;
      setLiveAnalysis(null);
      setAnalysisStatus(article?.deep_insight?.headline ? "done" : "idle");
    }
  }, [article?.id, article?.deep_insight?.headline]);

  // Phase 31 — detect live-fetched articles. They land here from
  // /api/news/live with is_analyzed=false and a SHA256-derived id that
  // doesn't yet exist in article_index. Hitting trigger-analysis
  // directly would 404. Instead we bootstrap via /api/news/live/analyze
  // (runs stages 1-9 + indexes), then continue with the normal flow.
  const isLiveUnanalyzed = !!(
    article && (article as unknown as { is_analyzed?: boolean }).is_analyzed === false
  );

  const doTrigger = (id: string, force = false) => {
    setAnalysisStatus("pending");

    const continueWithTrigger = async () => {
      try {
        const res = await newsApi.triggerAnalysis(id, force);
        if (res.status === "cached" || res.status === "done") {
          try {
            const r = await newsApi.getAnalysisStatus(id);
            if (r.analysis) {
              setLiveAnalysis(r.analysis as Record<string, unknown>);
              setAnalysisStatus("done");
              return;
            }
          } catch { /* fall through to pending */ }
        }
        if (res.status === ("failed" as string)) {
          setAnalysisStatus("failed");
          return;
        }
        // Other statuses — let the polling effect take it from here.
      } catch {
        setAnalysisStatus("failed");
      }
    };

    if (isLiveUnanalyzed && article?.url && article?.company_slug) {
      // Bootstrap path: indexes the article first, then runs the same
      // on-demand trigger as the legacy SECONDARY-click flow.
      newsApi.liveAnalyze({
        url: String(article.url),
        company_slug: String(article.company_slug),
        title: String(article.title || ""),
        summary: String((article as unknown as { summary?: string }).summary || ""),
        source: String((article as unknown as { source?: string }).source || ""),
        published_at: (article.published_at as string) || undefined,
        image_url: String((article as unknown as { image_url?: string }).image_url || ""),
      })
        .then(async (res) => {
          if (res.status === "daily_cap_reached") {
            setAnalysisStatus("failed");
            return;
          }
          // Use the canonical id the backend returned (matches the
          // article_index PK so the subsequent /trigger-analysis +
          // /analysis polls hit the right row).
          await continueWithTrigger();
        })
        .catch(() => setAnalysisStatus("failed"));
      return;
    }

    // Already-indexed article (legacy SECONDARY click or cached HOME).
    continueWithTrigger();
  };

  // Auto-trigger analysis when article opens and has no analysis
  // Using article.id as the key dependency ensures this fires when a new article is selected
  const articleId = article?.id;
  useEffect(() => {
    if (!articleId || hasAnalysis || triggeredRef.current) return;
    // Phase 13 B5: removed dev-debug console.log. Trigger silently.
    triggeredRef.current = true;
    doTrigger(articleId);
  }, [articleId, hasAnalysis]);

  // Poll every 5s while pending, give up after 24 polls (~2min)
  useEffect(() => {
    if (!article || analysisStatus !== "pending") {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    let count = 0;
    pollRef.current = setInterval(async () => {
      count++;
      if (count > 24) {
        clearInterval(pollRef.current!);
        pollRef.current = null;
        setAnalysisStatus("failed");
        return;
      }
      try {
        const res = await newsApi.getAnalysisStatus(article.id);
        if (res.status === "done" && res.analysis) {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setLiveAnalysis(res.analysis as Record<string, unknown>);
          setAnalysisStatus("done");
        } else if (res.status === "idle" && count > 3) {
          // Backend finished but produced no data — analysis failed silently
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setAnalysisStatus("failed");
        }
      } catch { /* transient error, keep polling */ }
    }, 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [analysisStatus, article?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Phase 11+ — super-admin Share-by-email gate. MUST be declared before any
  // early returns so hooks fire in the same order on every render (Rules of
  // Hooks). Backend API also 403s on `manage_drip_campaigns`, so this is
  // belt + braces.
  // Phase 13 B7 — also gate on server-confirmed email backend liveness so
  // the button doesn't appear when RESEND_API_KEY is missing or the sender
  // address isn't configured. Avoids the "click → preview-only" demo trap.
  const hasSharePermission = useAuthStore((s) => s.hasPermission("manage_drip_campaigns"));
  const emailConfigured = useAuthStore((s) => s.emailConfigured);
  const emailConfigReason = useAuthStore((s) => s.emailConfigReason);
  const canShareByEmail = hasSharePermission && emailConfigured;

  if (!article) return null;

  // Overlay live analysis onto article — prefer liveAnalysis when article fields are empty/stale
  // Use helper: treat empty objects {} and null/undefined as "no data"
  const _has = (v: unknown) => v != null && (typeof v !== "object" || Object.keys(v as object).length > 0);
  const effectiveArticle: Article = liveAnalysis
    ? {
        ...article,
        deep_insight: (_has(article.deep_insight) && (article.deep_insight as Record<string, unknown>)?.headline
          ? article.deep_insight
          : liveAnalysis.deep_insight) as Article["deep_insight"],
        rereact_recommendations: (_has(article.rereact_recommendations)
          ? article.rereact_recommendations
          : liveAnalysis.rereact_recommendations) as Article["rereact_recommendations"],
        risk_matrix: (_has(article.risk_matrix) ? article.risk_matrix : liveAnalysis.risk_matrix) as Article["risk_matrix"],
        framework_matches: (_has(article.framework_matches) ? article.framework_matches : liveAnalysis.framework_matches) as Article["framework_matches"],
        priority_score: article.priority_score ?? (liveAnalysis.priority_score as number | null),
        priority_level: article.priority_level ?? (liveAnalysis.priority_level as string | null),
        perspectives: (_has(article.perspectives) ? article.perspectives : liveAnalysis.perspectives) as Article["perspectives"],
        // Phase 3 §5.2 — pull role_payloads from the live analysis when
        // the cached article doesn't have them yet (pre-Phase-3 articles
        // get the field on first re-enrichment).
        role_payloads: ((article as Article).role_payloads
          ?? (liveAnalysis as { role_payloads?: Article["role_payloads"] }).role_payloads
        ) as Article["role_payloads"],
      }
    : article;

  const topScore = article.impact_scores?.[0];
  const financialAmount = article.financial_signal?.amount;
  const nlp = article.nlp_extraction;
  const themes = article.esg_themes;

  const diRaw = effectiveArticle.deep_insight as Record<string, unknown> | null;
  const di = diRaw as Record<string, string | Record<string, string> | string[]> | null;
  const rr = effectiveArticle.rereact_recommendations;
  const hasDeep = !!di && Object.keys(di).length > 0;
  const hasRereact = !!rr?.validated_recommendations?.length;

  // Phase 33 — Phase 5: when the unified `insight.analysis` block is
  // present (schema 3.0+), the article opens with: hero + TL;DR +
  // 4-bullet card. We hide Executive Insight + Framework Alignment
  // (redundant with the card) and collapse Risk + ESG + Impact +
  // AI Recommendations behind a single "Show full breakdown" accordion.
  // Pre-Phase-32 articles fall through to the flat legacy layout.
  const _diRaw = effectiveArticle.deep_insight as { analysis?: UnifiedAnalysis | null } | null | undefined;
  const hasUnified = !!_diRaw?.analysis?.what_changed;

  // Derive labels for hero card
  const primaryTheme = themes?.primary_theme || article.frameworks?.[0]?.split(":")[0] || "ESG";
  const pillarLabel = themes?.primary_pillar || (
    article.esg_pillar === "E" ? "Environmental" : article.esg_pillar === "S" ? "Social" : article.esg_pillar === "G" ? "Governance" : "ESG"
  );
  const tonePrimary = nlp?.tone?.primary;
  const sourceTier = nlp?.source_credibility?.tier;
  const rmRaw = effectiveArticle.risk_matrix as unknown as Record<string, unknown> | null;
  // Detect full matrix by mode tag OR presence of categories array (handles backfilled data)
  const isFullRiskMatrix = rmRaw?.mode === "full" || Array.isArray(rmRaw?.categories);
  const riskMode = isFullRiskMatrix ? "full" : (rmRaw?.mode as string | undefined);

  // Count supporting evidence sub-sections for Tier 4 badge
  const supportingEvidenceCount = [
    true, // Narrative Intelligence is always present
    !!(nlp && (nlp.esg_signals?.named_entities?.length || nlp.esg_signals?.quantitative_claims?.length || nlp.esg_signals?.regulatory_references?.length)),
    !!article.geographic_signal,
    !!article.impact_scores,
    !!(article.scoring_metadata as Record<string, unknown> | null)?.event_cluster,
    !!(hasDeep && (di?.net_impact_summary || di?.final_synthesis)),
  ].filter(Boolean).length;

  const handleAskAI = () => {
    onClose();
    // Phase 31 — consolidated to /chat (Phase C persistent chat with
    // MCP tools, Toulmin badges, advisor hints, stateful conversations).
    // Previously this routed to /agent (legacy AgentChatPage) which is
    // a strictly weaker surface — no MCP, no persistence, no role
    // routing. Both buttons ("Ask AI" + "Discuss this article in chat")
    // now land on the same `/chat?company=…&article=…` URL so behaviour
    // is identical and the user has one mental model.
    const params = new URLSearchParams();
    if (article.company_id || article.company_slug) {
      params.set("company", String(article.company_id ?? article.company_slug));
    }
    params.set("article", String(article.id));
    navigate(`/chat?${params.toString()}`);
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto" style={{ backgroundColor: COLORS.bgWhite }}>
      <div className="max-w-[440px] mx-auto min-h-screen relative pb-20">
        {/* Back button */}
        <button
          onClick={onClose}
          className="fixed z-50"
          style={{
            top: "28px",
            left: "max(16px, calc((100vw - 440px) / 2 + 16px))",
            width: "40px", height: "40px", borderRadius: "50%",
            backgroundColor: COLORS.bgWhite, boxShadow: SHADOWS.button,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: "18px", border: "none", cursor: "pointer",
          }}
          aria-label="Back"
        >
          &larr;
        </button>

        {/* Phase 28 / Feature 2 — "How is this calculated?" info icon.
            Opens MethodologyDrawer showing the per-role analysis +
            per-metric source / simple-language logic / formula. Sits
            next to the back button on the left so it doesn't fight
            the share button on the right. */}
        {article?.id && (
          <button
            onClick={() => setMethodologyOpen(true)}
            className="fixed z-50"
            style={{
              top: "28px",
              left: "max(64px, calc((100vw - 440px) / 2 + 64px))",
              width: "32px", height: "32px", borderRadius: "50%",
              backgroundColor: COLORS.bgWhite, boxShadow: SHADOWS.button,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "14px", fontWeight: 700,
              fontStyle: "italic", color: COLORS.brand,
              border: "none", cursor: "pointer",
            }}
            aria-label="How is this calculated?"
            title="How is this calculated?"
          >
            i
          </button>
        )}

        {/* Share button (super-admin only) — fixed top-right, mirrors the back button.
            Phase 13 B7: gated on BOTH `manage_drip_campaigns` permission AND
            server-confirmed email-backend liveness (`emailConfigured`). When
            permission is held but backend is down/missing, render a disabled
            badge with a tooltip so the demo doesn't show a button that
            silently no-ops. */}
        {hasSharePermission && article?.id && (
          <div
            className="fixed z-50"
            style={{
              top: "28px",
              right: "max(16px, calc((100vw - 440px) / 2 + 16px))",
            }}
          >
            {canShareByEmail ? (
              <ShareArticleButton
                articleId={String(article.id)}
                variant="default"
                label="Share via email"
                onSent={(res) => {
                  // Phase 13 B5: silent confirmation. Toast UX is the next
                  // polish item; no dev-debug console output in production.
                  void res;
                }}
              />
            ) : (
              <span
                title={emailConfigReason || "Email backend is not configured for this deployment."}
                style={{
                  display: "inline-flex", alignItems: "center", gap: "6px",
                  fontSize: "12px", fontWeight: 500, color: "#888",
                  background: "#f3f3f3", border: "1px solid #e0e0e0",
                  borderRadius: "16px", padding: "6px 14px", cursor: "not-allowed",
                  userSelect: "none",
                }}
              >
                Share unavailable
              </span>
            )}
          </div>
        )}

        {/* ═══ ZONE A: HERO CARD ═══ */}
        <div style={{ margin: "80px 24px 0", backgroundColor: COLORS.darkCard, borderRadius: RADII.card, boxShadow: SHADOWS.darkCard, padding: "28px" }}>
          {/* Theme + Pillar breadcrumb */}
          <p style={{ fontSize: "14px", color: COLORS.bgWhite, opacity: 0.8 }}>
            {primaryTheme} / {pillarLabel}
          </p>

          <h2 style={{ fontSize: "22px", color: COLORS.bgWhite, marginTop: "8px", lineHeight: "1.3" }}>
            {(hasDeep && typeof di?.headline === "string") ? di.headline : article.title}
          </h2>

          <p style={{ fontSize: "15px", color: "rgba(255,255,255,0.7)", marginTop: "12px", lineHeight: "1.5" }}>
            {article.summary}
          </p>

          {financialAmount && (
            <p style={{ fontSize: "14px", color: COLORS.textMuted, marginTop: "12px" }}>
              Financial Exposure: {formatCurrency(financialAmount)}
            </p>
          )}

          {/* Bottom meta row */}
          <div style={{ borderTop: "1px solid rgba(255,255,255,0.15)", marginTop: "16px", paddingTop: "12px" }}>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <PriorityBadge level={article.priority_level} />
                {tonePrimary && tonePrimary !== "neutral" && (
                  <span style={{
                    fontSize: "10px", padding: "2px 6px", borderRadius: "10px",
                    backgroundColor: "rgba(255,255,255,0.1)", color: "rgba(255,255,255,0.7)",
                    textTransform: "capitalize",
                  }}>
                    {tonePrimary}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span style={{ fontSize: "13px", color: "rgba(255,255,255,0.5)" }}>{article.source}</span>
                {sourceTier && (
                  <span style={{
                    fontSize: "9px", padding: "1px 5px", borderRadius: "8px",
                    backgroundColor: sourceTier === 1 ? "rgba(22,163,74,0.3)" : sourceTier === 2 ? "rgba(37,99,235,0.3)" : "rgba(255,255,255,0.1)",
                    color: sourceTier <= 2 ? "#fff" : "rgba(255,255,255,0.5)",
                  }}>
                    Tier {sourceTier}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ═══ ZONE A2: "WHY THIS MATTERS TO YOU" PERSONAL STAKES CARD ═══
            Phase 25 W10 — surfaces the W9 stakes_for_company block
            ABOVE the perspective switcher so a CFO opening a CRITICAL
            article gets the company-specific verdict in the first
            screen-full. Returns null when stakes_for_company is empty
            (LLM failed, REJECTED, or pre-W9 article), so legacy
            articles keep their existing layout untouched. */}
        {effectiveArticle.deep_insight && (
          <div style={{ padding: "12px 24px 0" }}>
            <PersonalStakesCard insight={effectiveArticle.deep_insight as never} />
          </div>
        )}

        {/* ═══ ZONE B: ESG THEME BAR ═══ */}
        {themes && (
          <div style={{ padding: "12px 24px 0" }}>
            <EsgThemeBar esgThemes={themes} />
          </div>
        )}

        {/* ═══ ZONE B1.5: INLINE PERSPECTIVE SWITCHER (REMOVED) ═══
            Phase 32 — role toggle removed from the article-detail view.
            The unified 4-bullet analysis is horizontally consumable by any
            role, so the CFO/CEO/Analyst chooser is dead weight. The
            PerspectiveSwitcher component is still mounted globally in the
            header for pages that haven't been migrated yet; this in-sheet
            mount is gone for good. */}

        {/* Phase 29 — 2-liner role summary at the top of the role view.
            Reads `deep_insight.criticality_summary` (global) +
            `deep_insight.role_explainer[role].why_important_for_me`
            (role-specific). Renders ABOVE everything else; hides itself
            when both values are empty (REJECTED / low-confidence). */}
        {(() => {
          // Phase 33 — TL;DR line above the unified card. Renders
          // `criticality_summary` with a band-tinted chip. Hidden when
          // empty (REJECTED articles).
          const di = effectiveArticle.deep_insight as {
            analysis?: UnifiedAnalysis | null;
            criticality_summary?: string;
            criticality?: { band?: string };
          } | undefined;
          const summary = di?.criticality_summary || "";
          if (!summary.trim()) return null;
          return <TLDRLine summary={summary} band={di?.criticality?.band ?? null} />;
        })()}
        {(() => {
          // Phase 32 — Unified 4-bullet analysis card. Replaces the per-role
          // RoleSummary + RoleDistinctView + CrispInsight stack as the
          // visual focus. Hidden on pre-Phase-32 articles where
          // `insight.analysis` isn't stamped yet — those fall back to the
          // legacy stack below (1-release shim per DECISION 5.2). The
          // on-demand re-enrichment path stamps `analysis` on next view.
          const di = effectiveArticle.deep_insight as {
            analysis?: UnifiedAnalysis | null;
            criticality_summary?: string;
            criticality?: { band?: string };
            role_explainer?: Record<string, { why_important_for_me?: string }>;
          } | undefined;
          const unified = di?.analysis;
          if (unified && unified.what_changed) {
            return (
              <UnifiedAnalysisCard
                analysis={unified}
                articleId={effectiveArticle.id}
              />
            );
          }
          // Legacy fallback (1-release shim) — pre-Phase-32 article.
          const critSummary = di?.criticality_summary ?? "";
          const whyMatters = di?.role_explainer?.[activePerspective]?.why_important_for_me ?? "";
          const band = di?.criticality?.band ?? null;
          if (!critSummary && !whyMatters) return null;
          return (
            <RoleSummary
              criticalitySummary={critSummary}
              whyItMattersToYou={whyMatters}
              role={activePerspective as EssentialRole}
              band={band}
            />
          );
        })()}

        {/* ═══ ZONE B2: CRISP INSIGHT — ontology-driven perspective view ═══
            Renders the CFO / CEO / ESG Analyst crisp card based on the
            global PerspectiveSwitcher. Driven entirely from the new
            ontology-backed pipeline's `perspectives` field.

            Phase 13 B6: empty-perspective fallback. If the deep insight
            exists but the requested perspective is not yet generated (e.g.
            on-demand pipeline only emitted CFO so far, user toggles to CEO),
            show an explicit fallback rather than silent blank space. */}
        {/* Phase 3 §5.2 — Stage 11 v2 RoleDistinctPayload preview.
            Renders ABOVE the legacy CrispInsight when `role_payloads`
            is present on the article. Surfaces the role-distinct
            headline + hero metric + role takeaways + role paragraph
            from the shared EvidencePack. Skips silently when absent
            (pre-Phase-3 articles). */}
        {(() => {
          // Phase 32 — hide legacy role-distinct + CrispInsight blocks when
          // unified analysis is present (UnifiedAnalysisCard above is now
          // the canonical surface). 1-release shim: pre-Phase-32 articles
          // without `analysis` still see the legacy stack.
          const di = effectiveArticle.deep_insight as { analysis?: UnifiedAnalysis | null } | undefined;
          const hasUnified = !!di?.analysis?.what_changed;
          if (hasUnified) return null;
          return (
            <>
              {effectiveArticle.role_payloads?.[activePerspective] && (
                <div style={{ padding: "12px 24px 0" }}>
                  <RoleDistinctView payload={effectiveArticle.role_payloads[activePerspective]} />
                </div>
              )}
              {effectiveArticle.perspectives?.[activePerspective] ? (
                <div style={{ padding: "12px 24px 0" }}>
                  <CrispInsight
                    view={effectiveArticle.perspectives[activePerspective] as unknown as NewCrispView}
                  />
                </div>
              ) : null}
            </>
          );
        })()}
        {/* Phase 32 — legacy "perspective not yet generated" fallback only
            shows for pre-Phase-32 articles (no unified analysis stamped).
            Phase 30 will delete this block along with the rest of the
            role-toggle surface. */}
        {(() => {
          const di = effectiveArticle.deep_insight as { analysis?: UnifiedAnalysis | null } | undefined;
          const hasUnified = !!di?.analysis?.what_changed;
          if (hasUnified) return null;
          if (effectiveArticle.perspectives?.[activePerspective]) return null;
          if (!hasDeep || analysisStatus === "pending") return null;
          return (
            <div style={{ padding: "12px 24px 0" }}>
              <div style={{
                padding: "16px 18px", borderRadius: "10px",
                background: "rgba(0,0,0,0.03)",
                border: "1px solid rgba(0,0,0,0.08)",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: "16px", flexWrap: "wrap",
              }}>
                <div style={{ flex: 1, minWidth: "220px" }}>
                  <p style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textPrimary, margin: 0 }}>
                    {activePerspective === "cfo" ? "CFO" : activePerspective === "ceo" ? "CEO" : "ESG Analyst"} view not yet available
                  </p>
                  <p style={{ fontSize: "11px", color: COLORS.textMuted, margin: "4px 0 0", lineHeight: 1.45 }}>
                    Deep insight is ready, but this perspective hasn't been generated.
                    Click below to run the perspective generator (~10s).
                  </p>
                </div>
                <button
                  onClick={() => {
                    triggeredRef.current = false;
                    doTrigger(article.id, /* force */ true);
                  }}
                  style={{
                    fontSize: "12px", fontWeight: 600, color: "#fff",
                    background: COLORS.brand, border: "none",
                    borderRadius: "16px", padding: "6px 16px", cursor: "pointer",
                    flexShrink: 0,
                  }}
                >
                  Generate {activePerspective === "esg-analyst" ? "ESG Analyst" : activePerspective.toUpperCase()} view
                </button>
              </div>
            </div>
          );
        })()}

        {/* ═══ On-demand analysis loading skeleton ═══
            Phase 13 S5: faux-progress stage labels advance every 6s so the
            45-60s wait feels purposeful. Stages are illustrative not
            literal — the pipeline runs all stages in parallel/sequence
            internally; we just surface progress to the user.
        */}
        {analysisStatus === "pending" && (
          <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: "12px",
              padding: "14px 16px", borderRadius: "10px",
              backgroundColor: "rgba(223,89,0,0.06)",
              border: "1px solid rgba(223,89,0,0.14)",
            }}>
              <div className="analysis-spinner" />
              <div>
                <p style={{ fontSize: "13px", fontWeight: 600, color: "#DF5900", margin: 0 }}>
                  Generating Intelligence Brief
                </p>
                <p style={{ fontSize: "11px", color: "#888", margin: "2px 0 0" }}>
                  {[
                    "Stage 1 of 6 · Extracting article themes & sentiment",
                    "Stage 2 of 6 · Matching ESG frameworks (BRSR, GRI, TCFD)",
                    "Stage 3 of 6 · Computing financial cascade & ₹ exposure",
                    "Stage 4 of 6 · Generating CFO / CEO / Analyst perspectives",
                    "Stage 5 of 6 · Drafting actionable recommendations",
                    "Stage 6 of 6 · CFO credibility check (6-gate preflight)",
                  ][analysisStage]}
                </p>
              </div>
            </div>
            <div className="analysis-skeleton" style={{ height: "130px" }} />
            <div className="analysis-skeleton" style={{ height: "90px" }} />
            <div className="analysis-skeleton" style={{ height: "70px" }} />
            <div className="analysis-skeleton" style={{ height: "60px" }} />
            <div className="analysis-skeleton" style={{ height: "50px" }} />
          </div>
        )}

        {/* ═══ On-demand analysis failed — retry ═══ */}
        {analysisStatus === "failed" && (
          <div style={{ padding: "20px 24px" }}>
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "14px 16px", borderRadius: "10px",
              backgroundColor: "rgba(0,0,0,0.03)",
              border: "1px solid rgba(0,0,0,0.08)",
            }}>
              <div>
                <p style={{ fontSize: "13px", fontWeight: 600, color: COLORS.textPrimary, margin: 0 }}>
                  Analysis unavailable
                </p>
                <p style={{ fontSize: "11px", color: COLORS.textMuted, margin: "2px 0 0" }}>
                  The pipeline timed out or encountered an error
                </p>
              </div>
              <button
                onClick={() => {
                  triggeredRef.current = false;
                  doTrigger(article.id);
                }}
                style={{
                  fontSize: "12px", fontWeight: 600, color: COLORS.brand,
                  background: "none", border: `1px solid ${COLORS.brand}`,
                  borderRadius: "16px", padding: "5px 14px", cursor: "pointer",
                  flexShrink: 0, marginLeft: "12px",
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* ═══ TIER 1 — PROFITABILITY IMPACT ═══ */}

        {/* ═══ 1. KEY TAKEAWAYS ═══ */}
        {hasDeep && di?.core_mechanism && (
          <Section title="Key Takeaways" defaultOpen accent={COLORS.brand}>
            <P text={di.core_mechanism as string} />
            {hasDeep && typeof di?.profitability_connection === "string" && (
              <p style={{ fontSize: "13px", color: "#16a34a", fontStyle: "italic", marginTop: "6px" }}>
                <span style={{ marginRight: "4px" }}>$</span>
                {di.profitability_connection as string}
              </p>
            )}
            {hasDeep && typeof di?.translation === "string" && (
              <p style={{ fontSize: "13px", color: COLORS.brand, fontStyle: "italic", marginTop: "4px" }}>
                {di.translation}
              </p>
            )}
          </Section>
        )}

        {/* ═══ 2. FINANCIAL IMPACT & TIMELINE ═══
            Phase 31 — (i) icon opens financial_timeline methodology
            popover (cascade β × Δ × base, primitive-engine sourcing). */}
        {hasDeep && di?.financial_timeline ? (
          <Section
            title="Financial Impact & Timeline"
            panelId="financial_timeline"
            onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
          >
            {(() => {
              const ft = di.financial_timeline as Record<string, unknown>;

              // Detect structured v2.1 format (buckets are objects with headline)
              const isStructured = ft.immediate && typeof ft.immediate === "object" && (ft.immediate as Record<string, unknown>).headline;

              if (isStructured) {
                const structuredBuckets = [
                  { key: "immediate", label: "Immediate (0-6 months)", accent: "#dc2626", accentBg: "rgba(220,38,38,0.06)" },
                  { key: "structural", label: "Structural (6-24 months)", accent: "#d97706", accentBg: "rgba(217,119,6,0.06)" },
                  { key: "long_term", label: "Long-term (2-5+ years)", accent: "#2563eb", accentBg: "rgba(37,99,235,0.06)" },
                ];
                // Phase 22.3 — polarity-aware labels. Positive events get
                // "Margin Benefit" / "Revenue Opportunity" labels;
                // negative/neutral keep the legacy "Margin Pressure" /
                // "Revenue at Risk". The polarity flag is set by the
                // backend in DeepInsight.event_polarity (Phase 22.3).
                const isPositive = (di as Record<string, unknown>)?.event_polarity === "positive";
                const metricLabels: Record<string, string> = {
                  cost_of_capital_impact: "Cost of Capital",
                  margin_pressure: isPositive ? "Margin Benefit" : "Margin Pressure",
                  cash_flow_impact: "Cash Flow",
                  revenue_at_risk: isPositive ? "Revenue Opportunity" : "Revenue at Risk",
                  valuation_rerating: "Valuation Re-rating",
                  investor_flow_impact: "Investor Flows",
                  competitive_position: "Competitive Position",
                  credit_rating_risk: isPositive ? "Credit Rating Upside" : "Credit Rating",
                  secular_trajectory: "Secular Trajectory",
                  stranded_asset_risk: isPositive ? "Asset Repositioning" : "Stranded Assets",
                  green_revenue_opportunity: "Green Revenue",
                  market_share_shift: "Market Share",
                };
                return (
                  <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                    {structuredBuckets.map((b) => {
                      const bucket = ft[b.key] as Record<string, string> | undefined;
                      if (!bucket) return null;
                      const headline = bucket.headline;
                      const pathway = bucket.profitability_pathway;
                      const metrics = Object.entries(bucket).filter(
                        ([k]) => k !== "headline" && k !== "profitability_pathway"
                      );
                      return (
                        <div key={b.key} style={{
                          backgroundColor: b.accentBg, borderRadius: "10px",
                          overflow: "hidden", border: `1px solid ${b.accent}22`,
                        }}>
                          <div style={{ backgroundColor: b.accent, padding: "8px 14px" }}>
                            <span style={{ fontSize: "11px", fontWeight: 700, color: "#fff", textTransform: "uppercase", letterSpacing: "0.5px" }}>
                              {b.label}
                            </span>
                          </div>
                          <div style={{ padding: "12px 14px" }}>
                            {headline && (
                              <p style={{ fontSize: "14px", fontWeight: 700, color: COLORS.textPrimary, margin: "0 0 8px", lineHeight: "1.4" }}>
                                {headline}
                              </p>
                            )}
                            {pathway && (
                              <div style={{
                                display: "flex", flexWrap: "wrap", alignItems: "center", gap: "3px",
                                margin: "0 0 8px", padding: "5px 8px",
                                backgroundColor: "rgba(0,0,0,0.02)", borderRadius: "4px",
                              }}>
                                {pathway.split("→").map((step: string, i: number, arr: string[]) => (
                                  <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: "3px" }}>
                                    <span style={{ fontSize: "11px", fontWeight: 500, color: COLORS.textMuted, lineHeight: "1.4" }}>
                                      {step.trim()}
                                    </span>
                                    {i < arr.length - 1 && (
                                      <span style={{ fontSize: "10px", fontWeight: 600, color: `${b.accent}88` }}>→</span>
                                    )}
                                  </span>
                                ))}
                              </div>
                            )}
                            {metrics.length > 0 && (
                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 10px" }}>
                                {metrics.map(([k, v]) => (
                                  <div key={k}>
                                    <span style={{ fontSize: "9px", color: COLORS.textMuted, textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 500 }}>
                                      {metricLabels[k] || k.replace(/_/g, " ")}
                                    </span>
                                    <p style={{ fontSize: "11px", fontWeight: 600, color: COLORS.textPrimary, margin: "1px 0 0", lineHeight: "1.35" }}>
                                      {v}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                );
              }

              const flatBuckets = [
                { key: "immediate_impact", label: "Immediate (0-6 months)", accent: "#dc2626" },
                { key: "structural_shift", label: "Structural (6-24 months)", accent: COLORS.brand },
                { key: "long_term_trajectory", label: "Long-term (2-5+ years)", accent: "#2563eb" },
              ];
              return (
                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  {flatBuckets.map((b) => {
                    const val = (ft as Record<string, string>)[b.key];
                    if (!val) return null;
                    return (
                      <div key={b.key} style={{
                        backgroundColor: COLORS.bgLight, borderRadius: "8px", padding: "12px 14px",
                        borderLeft: `3px solid ${b.accent}`,
                      }}>
                        <span style={{ fontSize: "11px", fontWeight: 700, color: b.accent, textTransform: "uppercase", letterSpacing: "0.5px" }}>
                          {b.label}
                        </span>
                        <p style={{ fontSize: "13px", color: COLORS.textPrimary, lineHeight: "1.5", margin: "4px 0 0" }}>
                          {val}
                        </p>
                      </div>
                    );
                  })}
                </div>
              );
            })()}
          </Section>
        ) : hasDeep && (di?.financial_valuation_impact || di?.time_horizon) ? (
          <>
            {di?.financial_valuation_impact && (
              <Section title="Financial & Valuation Impact">
                {renderDeepDict(di.financial_valuation_impact)}
              </Section>
            )}
            {di?.time_horizon && (
              <Section title="Time Horizon">
                {renderDeepDict(di.time_horizon)}
              </Section>
            )}
          </>
        ) : null}

        {/* ═══ TIER 2 — DETAILED BREAKDOWN (Phase 33 §5) ═══
            When the unified `insight.analysis` is stamped, sections 3, 4,
            5, 7 below are gated on `showDetails`. The reader sees a
            single "Show full breakdown" toggle above the detailed block
            and expands on demand. Pre-Phase-32 articles render the flat
            layout (showDetails defaults to true). */}
        {hasUnified && (
          <div style={{
            padding: "16px 24px 0", display: "flex",
            alignItems: "center", gap: 10,
          }}>
            <div style={{ flex: 1, borderTop: `1px solid ${COLORS.textDisabled}` }} />
            <button
              type="button"
              onClick={() => setShowDetails((v) => !v)}
              style={{
                fontSize: "11px", fontWeight: 700, letterSpacing: "0.5px",
                textTransform: "uppercase", color: COLORS.brand,
                background: "transparent", border: "none", cursor: "pointer",
                padding: "4px 8px",
              }}
            >
              {showDetails ? "▾ Hide full breakdown" : "▸ Show full breakdown — Risk · ESG · Impact · Recs"}
            </button>
            <div style={{ flex: 1, borderTop: `1px solid ${COLORS.textDisabled}` }} />
          </div>
        )}

        {/* ═══ 3. RISK ASSESSMENT ═══
            Phase 29 — "i" icon opens risk_matrix methodology popover
            (TEMPLES P×E logic, industry weighting). Phase 33 §5 —
            gated on `showDetails` when `hasUnified`. */}
        {(!hasUnified || showDetails) && (riskMode === "full" ? (
          <div style={{ padding: "16px 24px 0" }}>
            <PanelHeaderInline
              title="Risk Assessment"
              panelId="risk_matrix"
              onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
            />
            <RiskMatrixDisplay riskMatrix={article.risk_matrix} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : article.risk_matrix?.top_risks ? (
          <div style={{ padding: "16px 24px 0" }}>
            <PanelHeaderInline
              title="Risk Spotlight"
              panelId="risk_matrix"
              onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
            />
            <RiskSpotlight topRisks={(article.risk_matrix as unknown as Record<string, unknown>).top_risks as Array<{category_name: string; classification: string; rationale: string}>} />
            <UnlockFullAnalysis
              relevanceScore={article.relevance_score ?? 0}
              onAskAI={handleAskAI}
            />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : null)}

        {/* ═══ TIER 2 — STRATEGIC CONTEXT ═══ */}

        {/* ═══ 4. ESG RELEVANCE SCORE ═══
            Phase 29 — `panelId="esg_relevance_score"` enables the per-panel
            "i" icon → opens PanelInfoPopover with calculation methodology
            for this block only. Phase 33 §5 — gated on `showDetails`
            when `hasUnified`. */}
        {(!hasUnified || showDetails) && (
          (hasDeep && di?.esg_relevance_score && Object.keys(di.esg_relevance_score).length > 0) ? (
            <div style={{ padding: "16px 24px 0" }}>
              <PanelHeaderInline
                title="ESG Relevance Score"
                panelId="esg_relevance_score"
                onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
              />
              <ESGRelevanceScore6D score={di.esg_relevance_score as unknown as Record<string, { score: number; rationale: string }>} />
              <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
            </div>
          ) : article.relevance_breakdown ? (
            <div style={{ padding: "16px 24px 0" }}>
              <PanelHeaderInline
                title="ESG Relevance Score"
                panelId="relevance"
                onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
              />
              <RelevanceBreakdown breakdown={article.relevance_breakdown} />
              <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
            </div>
          ) : null
        )}

        {/* ═══ 5. IMPACT ANALYSIS (6 dimensions) ═══
            Phase 29 — "i" icon opens the per-panel popover with the
            generation methodology for these 6 sub-blocks. */}
        {(!hasUnified || showDetails) && hasDeep && di?.impact_analysis && (
          <Section
            title="Impact Analysis"
            defaultOpen
            panelId="impact_analysis"
            onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
          >
            {renderDeepDict(di.impact_analysis)}
          </Section>
        )}
        {(!hasUnified || showDetails) && hasDeep && !di?.impact_analysis && di?.esg_impact_analysis && (
          <Section
            title="ESG Impact Analysis"
            defaultOpen
            panelId="impact_analysis"
            onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
          >
            {renderDeepDict(di.esg_impact_analysis)}
          </Section>
        )}

        {/* ═══ 6. FRAMEWORK ALIGNMENT ═══
            W4e — hidden for CFO + CEO per RolePanelPriority ontology rules.
            Phase 29 — "i" icon opens framework_match methodology popover.
            Phase 33 — hidden permanently when the unified analysis is
            present (framework codes already in `what_it_triggers`). */}
        {!hasUnified && !rolePanels.isHidden("framework_alignment") && !shouldHideOnMobile("framework_alignment") && (
          article.framework_matches && article.framework_matches.length > 0 ? (
            <Section
              title="Framework Alignment"
              panelId="framework_match"
              onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
            >
              <FrameworkAlignmentV2 frameworkMatches={article.framework_matches} />
            </Section>
          ) : hasDeep && di?.compliance_regulatory_impact ? (
            <Section
              title="Compliance & Regulatory Impact"
              panelId="framework_match"
              onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
            >
              {renderDeepDict(di.compliance_regulatory_impact)}
            </Section>
          ) : null
        )}

        {/* ═══ TIER 3 — ACTION & INTELLIGENCE ═══ */}

        {/* ═══ 7. AI RECOMMENDATIONS (REREACT) ═══
            Phase 29 — "i" icon opens ai_recommendations methodology popover
            (3-agent RE³ pipeline explanation + per-rec type breakdown). */}
        {(!hasUnified || showDetails) && hasRereact && (
          <div style={{ padding: "16px 24px 0" }}>
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginBottom: "16px" }} />
            <PanelHeaderInline
              title="AI Recommendations"
              panelId="ai_recommendations"
              onInfoClick={(rect, id) => setActivePanelInfo({ panelId: id, anchorRect: rect })}
              accent={COLORS.brand}
            />
            <p style={{ fontSize: "12px", color: COLORS.textMuted, marginBottom: "12px", marginTop: "-8px" }}>
              Validated by 3-agent RE³ pipeline
            </p>
            {(() => {
              const recs = rr!.validated_recommendations;
              const rankings = rr!.recommendation_rankings;
              const typeFilters = rr!.perspective_type_filters;
              const perspKey = activePerspective === "esg-analyst" ? "esg-analyst" : activePerspective;

              // Step 1: Reorder by perspective-specific ranking
              const order = rankings?.[perspKey];
              type Rec = typeof recs[number];
              const ordered: Rec[] = order && order.length > 0
                ? order
                    .filter((idx: number) => idx >= 0 && idx < recs.length)
                    .map((idx: number) => recs[idx])
                    .filter((r): r is Rec => r != null)
                : recs;

              // Step 2: Filter by perspective-specific allowed types
              const allowedTypes = typeFilters?.[perspKey];
              const filtered = allowedTypes && allowedTypes.length > 0
                ? ordered.filter((rec: Rec) => allowedTypes.includes(rec.type))
                : ordered;

              return filtered.map((rec: Rec, i: number) => (
                <RecommendationCard key={i} rec={rec} index={i} articleId={article.id} />
              ));
            })()}
            {rr!.validation_summary && (
              <p style={{ fontSize: "12px", color: COLORS.textMuted, fontStyle: "italic", marginTop: "8px" }}>{rr!.validation_summary}</p>
            )}
            {/* Inline Q&A — Phase 5C/5B dedicated endpoint */}
            <InsightQA
              articleId={String(article.id)}
              companyId={topScore?.company_id ?? ""}
              suggestedQuestions={rr!.suggested_questions}
            />
          </div>
        )}

        {/* ═══ 8. EXECUTIVE INSIGHT ═══
            Phase 33 — hidden permanently when the unified analysis is
            present (the headline + criticality_summary already cover
            this on the unified card + TL;DR). */}
        {!hasUnified && (
          <div style={{ padding: "16px 24px 0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textSecondary, marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Executive Insight</h3>
            <P
              text={article.executive_insight}
              fallback={topScore?.explanation ? `${topScore.explanation}.` : "Open '💬 Discuss this article in chat' below for a detailed executive briefing."}
            />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "8px" }} />
          </div>
        )}

        {/* ═══ TIER 4 — SUPPORTING EVIDENCE (grouped collapsible) ═══ */}
        {/* W4e — entire supporting-evidence section hidden for CFO per ontology */}
        {!rolePanels.isHidden("narrative_intelligence") && !shouldHideOnMobile("narrative_intelligence") && (
        <Section title={`Supporting Evidence (${supportingEvidenceCount})`} defaultOpen={false}><>
          {/* Narrative Intelligence */}
          <div style={{ marginBottom: "14px" }}>
            <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>Narrative Intelligence</h4>
            <NarrativeIntelligence nlpExtraction={nlp} />
          </div>

          {/* NLP Evidence */}
          {nlp && !!(nlp.esg_signals?.named_entities?.length || nlp.esg_signals?.quantitative_claims?.length || nlp.esg_signals?.regulatory_references?.length) ? (
            <div style={{ marginBottom: "14px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>NLP Evidence</h4>
              <NlpEvidencePanel nlpExtraction={nlp} />
            </div>
          ) : null}

          {/* Geographic Intelligence */}
          {article.geographic_signal ? (
            <div style={{ marginBottom: "14px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>Geographic Intelligence</h4>
              <GeographicSignalPanel geoSignal={article.geographic_signal} />
            </div>
          ) : null}

          {/* Causal Chain */}
          {article.impact_scores && (
            <div style={{ marginBottom: "14px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>Causal Chain Analysis</h4>
              <VerticalCausalChain
                chainPath={topScore?.chain_path ?? undefined}
                explanation={topScore?.explanation ?? undefined}
                relationshipType={topScore?.relationship_type}
                hops={topScore?.causal_hops}
                frameworks={topScore?.frameworks}
                articleTitle={article.title}
                confidence={topScore?.confidence ?? undefined}
              />
            </div>
          )}

          {/* Related Coverage */}
          {(article.scoring_metadata as Record<string, unknown> | null)?.event_cluster && (
            <div style={{ marginBottom: "14px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>Related Coverage</h4>
              <RelatedCoverage article={article} />
            </div>
          )}

          {/* Net Impact Summary */}
          {hasDeep && (di?.net_impact_summary || di?.final_synthesis) ? (
            <div style={{ marginBottom: "6px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textMuted, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.4px" }}>Net Impact Summary</h4>
              <P text={(di.net_impact_summary || di.final_synthesis) as string} />
            </div>
          ) : null}
        </></Section>
        )}

        {/* ═══ ACTION BUTTONS ═══
            Phase 31 — "Ask AI" removed; the same destination is served
            by "💬 Discuss this article in chat" below. Keeping one CTA
            instead of two stops the confusion the user flagged. */}
        <div className="flex gap-3" style={{ padding: "24px 24px 12px" }}>
          <button
            onClick={() => article.url && window.open(article.url, "_blank")}
            className="flex-1 font-medium"
            style={{ backgroundColor: COLORS.bgLight, color: COLORS.textPrimary, borderRadius: RADII.button, padding: "14px 0", fontSize: "18px", fontWeight: 500, border: `1px solid ${COLORS.textDisabled}`, cursor: "pointer" }}
          >
            View Article
          </button>
        </div>

        {/* Phase 28 / Feature 6 — "Discuss this article" entrypoint.
            Opens /chat with `?company=...&article=...` URL params so
            the chat page auto-primes a contextual prompt + the LLM can
            call `intelligence-forecast` / `intelligence-competitors` /
            `memory-recall` MCP tools against the right company. */}
        {article?.id && (
          <div style={{ padding: "0 24px 28px" }}>
            <button
              onClick={() => {
                const params = new URLSearchParams();
                if (article.company_id || article.company_slug) {
                  params.set(
                    "company",
                    String(article.company_id ?? article.company_slug),
                  );
                }
                params.set("article", String(article.id));
                onClose();
                navigate(`/chat?${params.toString()}`);
              }}
              className="w-full font-medium"
              style={{
                background: "none",
                color: COLORS.brand,
                border: `1px dashed ${COLORS.brand}`,
                borderRadius: RADII.button,
                padding: "12px 0", fontSize: 13, fontWeight: 600,
                cursor: "pointer",
              }}
            >
              💬 Discuss this article in chat
            </button>
          </div>
        )}
      </div>

      {/* Phase 28 / Feature 5 — "Show all panels" override on mobile.
          Only renders when we're actually hiding panels (mobile + role
          active) so the desktop view never sees this toggle. */}
      {isMobile && activePerspective && !showAllOnMobile && (
        <div style={{
          maxWidth: 440, margin: "12px auto 0",
          padding: "0 24px",
          textAlign: "center",
        }}>
          <button
            onClick={() => setShowAllOnMobile(true)}
            style={{
              background: "none", border: `1px dashed ${COLORS.textMuted}`,
              borderRadius: 8, padding: "8px 14px", color: COLORS.textMuted,
              fontSize: 12, fontWeight: 500, cursor: "pointer",
            }}
          >
            Show all panels for this article
          </button>
        </div>
      )}

      {/* Phase 28 / Feature 2 — methodology drawer (mounted at sheet
          root so it overlays the entire detail view).
          Phase 29: kept reachable for power users (chat slash-command,
          deep-link). The default per-panel "i" icons use the popover
          below instead, which is lighter-weight. */}
      <MethodologyDrawer
        articleId={methodologyOpen && article?.id ? String(article.id) : null}
        role={activePerspective}
        onClose={() => setMethodologyOpen(false)}
      />

      {/* Phase 29 — per-panel info popover. Anchored to whichever "i"
          icon was clicked (panel header). Closes on click-outside or
          Escape. Cached by React Query so re-open is instant. */}
      {activePanelInfo && article?.id && (
        <PanelInfoPopover
          articleId={String(article.id)}
          panelId={activePanelInfo.panelId}
          role={activePerspective || "cfo"}
          anchorRect={activePanelInfo.anchorRect}
          onClose={() => setActivePanelInfo(null)}
        />
      )}
    </div>
  );
}


/** Phase 29 — `<PanelHeader>` wraps a section title + an "i" icon that
 * opens the per-panel info popover. Any caller that wants per-panel
 * methodology disclosure just renders this in place of a raw <h3>. */
export interface PanelHeaderProps {
  title: string;
  panelId: string;
  /** Callback the parent uses to open the popover with anchorRect. */
  onInfoClick: (anchorRect: DOMRect, panelId: string) => void;
}

export function PanelHeader({ title, panelId, onInfoClick }: PanelHeaderProps) {
  const btnRef = (el: HTMLButtonElement | null) => { (btnRef as { current?: HTMLButtonElement | null }).current = el; };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{ flex: 1 }}>{title}</span>
      <button
        ref={btnRef}
        type="button"
        aria-label={`How is "${title}" calculated?`}
        title="How is this calculated?"
        onClick={(e) => {
          const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
          onInfoClick(rect, panelId);
        }}
        style={{
          width: 20, height: 20, borderRadius: 10,
          border: `1px solid ${COLORS.brand}`,
          background: "transparent", color: COLORS.brand,
          fontStyle: "italic", fontWeight: 700, fontSize: 11,
          cursor: "pointer", lineHeight: 1, padding: 0,
        }}
      >
        i
      </button>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────────
   Phase 3 §5.2 — RoleDistinctView
   Renders the structured RoleDistinctPayload (headline + hero metric
   + role takeaways + role paragraph) above the legacy CrispInsight.
   Skipped silently when role_payloads is absent (pre-Phase-3 articles).
   ─────────────────────────────────────────────────────────────────── */
import type { RoleDistinctPayload } from "@/types";

function RoleDistinctView({ payload }: { payload: RoleDistinctPayload }) {
  const { role, headline, hero_metric, role_takeaways, role_paragraph } = payload;
  const roleLabel = role === "cfo" ? "CFO" : role === "ceo" ? "CEO" : "ESG Analyst";

  // Hero metric label colour-codes per role: orange for CFO (₹-led),
  // emerald for CEO (strategy-led), blue for Analyst (framework-led)
  const heroAccent =
    role === "cfo" ? COLORS.brand
    : role === "ceo" ? COLORS.opportunity
    : COLORS.framework;
  const heroBg =
    role === "cfo" ? COLORS.brandLight
    : role === "ceo" ? COLORS.opportunityBg
    : COLORS.frameworkBg;

  // Decision window / horizon / deadline — show whichever the role uses
  const heroAffix =
    hero_metric.decision_window
      ? `Decide by ${hero_metric.decision_window}`
      : hero_metric.horizon
      ? `Horizon ${hero_metric.horizon}`
      : hero_metric.deadline
      ? `Due ${hero_metric.deadline}`
      : "";

  return (
    <section
      style={{
        background: "#fff",
        border: `1px solid ${COLORS.cardBorder}`,
        borderRadius: 10,
        padding: "16px 18px",
        boxShadow: SHADOWS.card,
      }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        marginBottom: 10,
      }}>
        <div style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
          color: heroAccent, textTransform: "uppercase",
        }}>
          {roleLabel} brief
        </div>
        <div style={{ fontSize: 9, color: COLORS.textMuted, letterSpacing: "0.04em" }}>
          Stage 11 v2
        </div>
      </div>

      <h3 style={{
        fontSize: 16, fontWeight: 700, lineHeight: 1.35,
        color: COLORS.textPrimary, margin: "0 0 12px",
      }}>
        {headline}
      </h3>

      {/* Hero metric pill */}
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "8px 14px", borderRadius: 8,
        background: heroBg,
        border: `1px solid ${heroAccent}`,
        marginBottom: 12,
      }}>
        <div>
          <div style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.08em",
            color: heroAccent, textTransform: "uppercase",
          }}>
            {hero_metric.label}
          </div>
          <div style={{ fontSize: 14, fontWeight: 700, color: COLORS.textPrimary }}>
            {hero_metric.value}
          </div>
          {heroAffix && (
            <div style={{ fontSize: 10, color: COLORS.textMuted, marginTop: 2 }}>
              {heroAffix}
            </div>
          )}
        </div>
      </div>

      {/* Role takeaways — bulleted list */}
      {role_takeaways && role_takeaways.length > 0 && (
        <ul style={{
          listStyle: "none", padding: 0, margin: "0 0 10px",
          display: "flex", flexDirection: "column", gap: 6,
        }}>
          {role_takeaways.map((bullet, i) => (
            <li key={i} style={{
              display: "flex", alignItems: "flex-start", gap: 8,
              fontSize: 13, color: COLORS.textPrimary, lineHeight: 1.5,
            }}>
              <span style={{
                flexShrink: 0, marginTop: 6,
                width: 5, height: 5, borderRadius: "50%",
                background: heroAccent,
              }} />
              <span>{bullet}</span>
            </li>
          ))}
        </ul>
      )}

      {role_paragraph && (
        <p style={{
          fontSize: 12, color: COLORS.textSecondary, lineHeight: 1.55,
          margin: "8px 0 0", fontStyle: "italic",
        }}>
          {role_paragraph}
        </p>
      )}
    </section>
  );
}
