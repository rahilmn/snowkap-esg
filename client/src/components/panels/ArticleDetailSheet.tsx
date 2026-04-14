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
import { usePerspective } from "@/stores/perspectiveStore";
import { formatCurrency } from "../../lib/utils";
import type { Article } from "../../types";
import type { CrispView as NewCrispView } from "@/lib/snowkap-api";

interface ArticleDetailSheetProps {
  article: Article | null;
  onClose: () => void;
}

/* Collapsible section wrapper */
function Section({
  title,
  children,
  defaultOpen = false,
  accent,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  accent?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ padding: "16px 24px" }}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between"
        style={{ border: "none", background: "none", cursor: "pointer", padding: "0 0 8px" }}
      >
        <h3 style={{ fontSize: "14px", fontWeight: 600, color: accent || COLORS.textSecondary, margin: 0, textTransform: "uppercase", letterSpacing: "0.5px" }}>
          {title}
        </h3>
        <span style={{ fontSize: "11px", color: COLORS.textMuted }}>{open ? "Hide" : "Show"}</span>
      </button>
      {open && <div style={{ paddingBottom: "4px" }}>{children}</div>}
      <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}` }} />
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

/* New 6D ESG Relevance Score from deep_insight.esg_relevance_score */
function ESGRelevanceScore6D({ score }: { score: Record<string, { score: number; rationale: string }> }) {
  const dims = [
    { key: "environment",          label: "Environment",         color: "#16a34a" },
    { key: "social",               label: "Social",              color: "#2563eb" },
    { key: "governance",           label: "Governance",          color: "#7c3aed" },
    { key: "financial_materiality",label: "Financial",           color: COLORS.brand },
    { key: "regulatory_exposure",  label: "Regulatory",          color: "#dc2626" },
    { key: "stakeholder_impact",   label: "Stakeholders",        color: "#0891b2" },
  ];
  const avg = dims.reduce((s, d) => s + (score[d.key]?.score ?? 0), 0) / dims.length;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginBottom: "14px" }}>
        <span style={{ fontSize: "28px", fontWeight: 700, color: avg >= 7 ? COLORS.brand : avg >= 4 ? COLORS.textPrimary : COLORS.textMuted }}>
          {avg.toFixed(1)}
        </span>
        <span style={{ fontSize: "12px", color: COLORS.textMuted }}>/10 avg across 6 dimensions</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {dims.map((d) => {
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
        })}
      </div>
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
    sessionStorage.setItem("agent_context", JSON.stringify({
      article_id: articleId,
      recommendation_title: rec.title,
      recommendation_description: rec.description,
      prompt: `Tell me more about: "${rec.title}" — ${rec.description}`,
    }));
    navigate("/agent");
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
          Ask AI about this &rarr;
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
  const activePerspective = usePerspective((s) => s.active);

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

  // Reset state when article changes (since we don't use key={} for remount)
  useEffect(() => {
    if (article?.id !== lastArticleId.current) {
      lastArticleId.current = article?.id ?? null;
      triggeredRef.current = false;
      setLiveAnalysis(null);
      setAnalysisStatus(article?.deep_insight?.headline ? "done" : "idle");
    }
  }, [article?.id, article?.deep_insight?.headline]);

  // Trigger analysis (extracted so retry can reuse)
  const doTrigger = (id: string) => {
    setAnalysisStatus("pending");
    newsApi.triggerAnalysis(id)
      .then((res) => {
        if (res.status === "cached") {
          return newsApi.getAnalysisStatus(id).then((r) => {
            if (r.analysis) {
              setLiveAnalysis(r.analysis as Record<string, unknown>);
              setAnalysisStatus("done");
            }
          });
        }
        // "done" — fetch the analysis immediately
        if (res.status === "done") {
          return newsApi.getAnalysisStatus(id).then((r) => {
            if (r.analysis) {
              setLiveAnalysis(r.analysis as Record<string, unknown>);
              setAnalysisStatus("done");
            }
          });
        }
        // "triggered" or "already_running" — polling will handle it
      })
      .catch(() => { setAnalysisStatus("failed"); });
  };

  // Auto-trigger analysis when article opens and has no analysis
  useEffect(() => {
    if (!article || hasAnalysis || triggeredRef.current) return;
    triggeredRef.current = true;
    doTrigger(article.id);
  }, [article?.id, hasAnalysis]); // eslint-disable-line react-hooks/exhaustive-deps

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

  if (!article) return null;

  // Overlay live analysis onto article (only fills null fields)
  const effectiveArticle: Article = liveAnalysis
    ? {
        ...article,
        deep_insight: (article.deep_insight ?? liveAnalysis.deep_insight) as Article["deep_insight"],
        rereact_recommendations: (article.rereact_recommendations ?? liveAnalysis.rereact_recommendations) as Article["rereact_recommendations"],
        risk_matrix: (article.risk_matrix ?? liveAnalysis.risk_matrix) as Article["risk_matrix"],
        framework_matches: (article.framework_matches ?? liveAnalysis.framework_matches) as Article["framework_matches"],
        priority_score: article.priority_score ?? (liveAnalysis.priority_score as number | null),
        priority_level: article.priority_level ?? (liveAnalysis.priority_level as string | null),
        perspectives: (article.perspectives ?? liveAnalysis.perspectives) as Article["perspectives"],
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
    // Pass v2 context for richer auto-prompt
    const rm = article.risk_matrix;
    const topRisk = rm?.top_risks?.[0];
    navigate("/agent", {
      state: {
        articleId: article.id,
        articleTitle: article.title,
        articleSummary: article.summary,
        priorityLevel: article.priority_level,
        contentType: article.content_type,
        frameworks: article.frameworks,
        impactScore: topScore?.impact_score,
        explanation: topScore?.explanation,
        executiveInsight: article.executive_insight,
        // v2 context for richer prompt
        topRiskName: topRisk?.category_name,
        topRiskScore: topRisk?.risk_score,
        topRiskClass: topRisk?.classification,
        tonePrimary: tonePrimary,
        primaryTheme: primaryTheme,
        frameworkCount: article.framework_matches?.length || 0,
        aggregateRisk: rm?.aggregate_score,
        riskMode: riskMode || "spotlight",
        relevanceScore: article.relevance_score,
      },
    });
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
        >
          &larr;
        </button>

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

        {/* ═══ ZONE B: ESG THEME BAR ═══ */}
        {themes && (
          <div style={{ padding: "12px 24px 0" }}>
            <EsgThemeBar esgThemes={themes} />
          </div>
        )}

        {/* ═══ ZONE B2: CRISP INSIGHT — ontology-driven perspective view ═══
            Renders the CFO / CEO / ESG Analyst crisp card based on the
            global PerspectiveSwitcher. Driven entirely from the new
            ontology-backed pipeline's `perspectives` field. */}
        {effectiveArticle.perspectives?.[activePerspective] && (
          <div style={{ padding: "12px 24px 0" }}>
            <CrispInsight
              view={effectiveArticle.perspectives[activePerspective] as unknown as NewCrispView}
            />
          </div>
        )}

        {/* ═══ On-demand analysis loading skeleton ═══ */}
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
                  Running ESG analysis pipeline — 20 to 60 seconds
                </p>
              </div>
            </div>
            <div className="analysis-skeleton" style={{ height: "130px" }} />
            <div className="analysis-skeleton" style={{ height: "90px" }} />
            <div className="analysis-skeleton" style={{ height: "70px" }} />
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

        {/* ═══ 2. FINANCIAL IMPACT & TIMELINE ═══ */}
        {hasDeep && di?.financial_timeline ? (
          <Section title="Financial Impact & Timeline">
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
                const metricLabels: Record<string, string> = {
                  cost_of_capital_impact: "Cost of Capital",
                  margin_pressure: "Margin Pressure",
                  cash_flow_impact: "Cash Flow",
                  revenue_at_risk: "Revenue at Risk",
                  valuation_rerating: "Valuation Re-rating",
                  investor_flow_impact: "Investor Flows",
                  competitive_position: "Competitive Position",
                  credit_rating_risk: "Credit Rating",
                  secular_trajectory: "Secular Trajectory",
                  stranded_asset_risk: "Stranded Assets",
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

        {/* ═══ 3. RISK ASSESSMENT ═══ */}
        {riskMode === "full" ? (
          <div style={{ padding: "16px 24px 0" }}>
            <RiskMatrixDisplay riskMatrix={article.risk_matrix} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : article.risk_matrix?.top_risks ? (
          <div style={{ padding: "16px 24px 0" }}>
            <RiskSpotlight topRisks={(article.risk_matrix as unknown as Record<string, unknown>).top_risks as Array<{category_name: string; classification: string; rationale: string}>} />
            <UnlockFullAnalysis
              relevanceScore={article.relevance_score ?? 0}
              onAskAI={handleAskAI}
            />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : null}

        {/* ═══ TIER 2 — STRATEGIC CONTEXT ═══ */}

        {/* ═══ 4. ESG RELEVANCE SCORE ═══ */}
        {(hasDeep && di?.esg_relevance_score && Object.keys(di.esg_relevance_score).length > 0) ? (
          <div style={{ padding: "16px 24px 0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textSecondary, marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              ESG Relevance Score
            </h3>
            <ESGRelevanceScore6D score={di.esg_relevance_score as unknown as Record<string, { score: number; rationale: string }>} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : article.relevance_breakdown ? (
          <div style={{ padding: "16px 24px 0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textSecondary, marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              ESG Relevance Score
            </h3>
            <RelevanceBreakdown breakdown={article.relevance_breakdown} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : null}

        {/* ═══ 5. IMPACT ANALYSIS (6 dimensions) ═══ */}
        {hasDeep && di?.impact_analysis && (
          <Section title="Impact Analysis" defaultOpen>
            {renderDeepDict(di.impact_analysis)}
          </Section>
        )}
        {hasDeep && !di?.impact_analysis && di?.esg_impact_analysis && (
          <Section title="ESG Impact Analysis" defaultOpen>
            {renderDeepDict(di.esg_impact_analysis)}
          </Section>
        )}

        {/* ═══ 6. FRAMEWORK ALIGNMENT ═══ */}
        {article.framework_matches && article.framework_matches.length > 0 ? (
          <Section title="Framework Alignment">
            <FrameworkAlignmentV2 frameworkMatches={article.framework_matches} />
          </Section>
        ) : hasDeep && di?.compliance_regulatory_impact ? (
          <Section title="Compliance & Regulatory Impact">
            {renderDeepDict(di.compliance_regulatory_impact)}
          </Section>
        ) : null}

        {/* ═══ TIER 3 — ACTION & INTELLIGENCE ═══ */}

        {/* ═══ 7. AI RECOMMENDATIONS (REREACT) ═══ */}
        {hasRereact && (
          <div style={{ padding: "16px 24px 0" }}>
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginBottom: "16px" }} />
            <h3 style={{ fontSize: "14px", fontWeight: 600, color: COLORS.brand, marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              AI Recommendations
            </h3>
            <p style={{ fontSize: "12px", color: COLORS.textMuted, marginBottom: "12px" }}>
              Validated by 3-agent RE³ pipeline
            </p>
            {(() => {
              const recs = rr!.validated_recommendations;
              const rankings = rr!.recommendation_rankings;
              const typeFilters = rr!.perspective_type_filters;
              const perspKey = activePerspective === "esg-analyst" ? "esg-analyst" : activePerspective;

              // Step 1: Reorder by perspective-specific ranking
              const order = rankings?.[perspKey];
              const ordered = order && order.length > 0
                ? order.filter((idx: number) => idx < recs.length).map((idx: number) => recs[idx])
                : recs;

              // Step 2: Filter by perspective-specific allowed types
              const allowedTypes = typeFilters?.[perspKey];
              const filtered = allowedTypes && allowedTypes.length > 0
                ? ordered.filter((rec) => allowedTypes.includes(rec.type))
                : ordered;

              return filtered.map((rec: typeof recs[number], i: number) => (
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

        {/* ═══ 8. EXECUTIVE INSIGHT ═══ */}
        <div style={{ padding: "16px 24px 0" }}>
          <h3 style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textSecondary, marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Executive Insight</h3>
          <P
            text={article.executive_insight}
            fallback={topScore?.explanation ? `${topScore.explanation}.` : "Tap Ask AI for a detailed executive briefing."}
          />
          <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "8px" }} />
        </div>

        {/* ═══ TIER 4 — SUPPORTING EVIDENCE (grouped collapsible) ═══ */}
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

        {/* ═══ ACTION BUTTONS ═══ */}
        <div className="flex gap-3" style={{ padding: "24px 24px 32px" }}>
          <button
            onClick={handleAskAI}
            className="flex-1 text-white font-medium"
            style={{ backgroundColor: COLORS.darkCard, borderRadius: RADII.button, padding: "14px 0", fontSize: "18px", fontWeight: 500, border: "none", cursor: "pointer" }}
          >
            Ask AI
          </button>
          <button
            onClick={() => article.url && window.open(article.url, "_blank")}
            className="flex-1 font-medium"
            style={{ backgroundColor: COLORS.bgLight, color: COLORS.textPrimary, borderRadius: RADII.button, padding: "14px 0", fontSize: "18px", fontWeight: 500, border: `1px solid ${COLORS.textDisabled}`, cursor: "pointer" }}
          >
            View Article
          </button>
        </div>
      </div>
    </div>
  );
}
