/**
 * v2.0 Article Detail Sheet — ESG Intelligence Brief.
 *
 * Layout:
 * 1. Hero Card (dark) — headline, summary, financial exposure, priority, source + credibility + tone
 * 2. ESG Theme Bar — primary + secondary theme pills
 * 3. Narrative Intelligence — core claim, causation, stakeholder chips, temporal
 * 4. Risk Matrix — 10-category heatmap (always open, most prominent)
 * 5. 5D Relevance Score
 * 6. Executive Insight
 * 7. Core Mechanism (collapsible)
 * 8. Impact Analysis — 6 dimensions (collapsible)
 * 9. Framework Alignment v2 (collapsible)
 * 10. Financial & Valuation (collapsible)
 * 11. Time Horizon (collapsible)
 * 12. Net Impact Summary (collapsible)
 * 13. NLP Evidence (collapsible, default closed)
 * 14. Geographic Signal (collapsible, default closed)
 * 15. REREACT Recommendations
 * 16. Causal Chain
 * 17. Action Buttons
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { formatCurrency } from "../../lib/utils";
import type { Article } from "../../types";

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
    <div style={{ padding: "0 28px" }}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-3"
        style={{ border: "none", background: "none", cursor: "pointer" }}
      >
        <h3 style={{ fontSize: "15px", fontWeight: 600, color: accent || COLORS.textSecondary, margin: 0 }}>
          {title}
        </h3>
        <span style={{ fontSize: "12px", color: COLORS.textMuted }}>{open ? "Hide" : "Show"}</span>
      </button>
      {open && <div style={{ paddingBottom: "16px" }}>{children}</div>}
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

/* REREACT recommendation card — enhanced with GAP 6 actionable fields */
function RecommendationCard({
  rec,
  index,
}: {
  rec: {
    type: string; title: string; description: string;
    framework?: string; framework_section?: string;
    responsible_party?: string; deadline?: string;
    estimated_budget?: string; success_criterion?: string;
    urgency: string; confidence: string; validation_notes?: string;
  };
  index: number;
}) {
  const confColor = rec.confidence === "HIGH" ? "#16a34a" : rec.confidence === "MEDIUM" ? COLORS.brand : "#dc2626";
  const frameworkDisplay = rec.framework_section || rec.framework;
  return (
    <div style={{ backgroundColor: COLORS.bgLight, borderRadius: "8px", padding: "14px", marginBottom: "10px", borderLeft: `3px solid ${confColor}` }}>
      <div className="flex items-center justify-between mb-1">
        <span style={{ fontSize: "12px", fontWeight: 600, color: COLORS.textSecondary, textTransform: "uppercase" }}>
          {rec.type || `Recommendation ${index + 1}`}
        </span>
        <span style={{ fontSize: "10px", fontWeight: 700, padding: "2px 6px", borderRadius: "4px", backgroundColor: confColor, color: "#fff" }}>
          {rec.confidence}
        </span>
      </div>
      <p style={{ fontSize: "14px", fontWeight: 600, color: COLORS.textPrimary, margin: "4px 0" }}>{rec.title}</p>
      <p style={{ fontSize: "13px", color: COLORS.textSecondary, lineHeight: "1.5", margin: "4px 0 8px" }}>{rec.description}</p>

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

      <div className="flex items-center gap-3 flex-wrap">
        {frameworkDisplay && <span style={{ fontSize: "11px", color: COLORS.framework, fontWeight: 500 }}>{frameworkDisplay}</span>}
        {rec.urgency && (
          <span style={{
            fontSize: "10px", padding: "1px 6px", borderRadius: "4px",
            backgroundColor: rec.urgency === "immediate" ? "rgba(220,38,38,0.1)" : "rgba(0,0,0,0.05)",
            color: rec.urgency === "immediate" ? "#dc2626" : COLORS.textSecondary,
          }}>
            {rec.urgency}
          </span>
        )}
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
    <div style={{ padding: "12px 28px 0" }}>
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

  if (!article) return null;

  const topScore = article.impact_scores?.[0];
  const financialAmount = article.financial_signal?.amount;
  const nlp = article.nlp_extraction;
  const themes = article.esg_themes;

  const diRaw = article.deep_insight as Record<string, unknown> | null;
  const di = diRaw as Record<string, string | Record<string, string> | string[]> | null;
  const rr = article.rereact_recommendations;
  const hasDeep = !!di && Object.keys(di).length > 0;
  const hasRereact = !!rr?.validated_recommendations?.length;

  // Derive labels for hero card
  const primaryTheme = themes?.primary_theme || article.frameworks?.[0]?.split(":")[0] || "ESG";
  const pillarLabel = themes?.primary_pillar || (
    article.esg_pillar === "E" ? "Environmental" : article.esg_pillar === "S" ? "Social" : article.esg_pillar === "G" ? "Governance" : "ESG"
  );
  const tonePrimary = nlp?.tone?.primary;
  const sourceTier = nlp?.source_credibility?.tier;
  const rmRaw = article.risk_matrix as unknown as Record<string, unknown> | null;
  // Detect full matrix by mode tag OR presence of categories array (handles backfilled data)
  const isFullRiskMatrix = rmRaw?.mode === "full" || Array.isArray(rmRaw?.categories);
  const riskMode = isFullRiskMatrix ? "full" : (rmRaw?.mode as string | undefined);

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
        <div style={{ padding: "12px 28px 0" }}>
          <EsgThemeBar esgThemes={themes} />
        </div>

        {/* ═══ ZONE C: NARRATIVE INTELLIGENCE ═══ */}
        <div style={{ padding: "8px 28px 0" }}>
          <NarrativeIntelligence nlpExtraction={nlp} />
          <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "12px" }} />
        </div>

        {/* ═══ ZONE D: RISK — Full Matrix (HOME) or Spotlight (FEED) ═══ */}
        {riskMode === "full" ? (
          <div style={{ padding: "16px 28px 0" }}>
            <RiskMatrixDisplay riskMatrix={article.risk_matrix} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : article.risk_matrix?.top_risks ? (
          <div style={{ padding: "16px 28px 0" }}>
            <RiskSpotlight topRisks={(article.risk_matrix as unknown as Record<string, unknown>).top_risks as Array<{category_name: string; classification: string; rationale: string}>} />
            <UnlockFullAnalysis
              relevanceScore={article.relevance_score ?? 0}
              onAskAI={handleAskAI}
            />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        ) : null}

        {/* ═══ EVENT DEDUPLICATION: RELATED COVERAGE ═══ */}
        <RelatedCoverage article={article} />

        {/* ═══ ZONE E: 5D RELEVANCE SCORE ═══ */}
        {article.relevance_breakdown && (
          <div style={{ padding: "16px 28px 0" }}>
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: COLORS.textSecondary, marginBottom: "10px" }}>
              ESG Relevance Score
            </h3>
            <RelevanceBreakdown breakdown={article.relevance_breakdown} />
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "16px" }} />
          </div>
        )}

        {/* ═══ ZONE F: EXECUTIVE INSIGHT ═══ */}
        <div style={{ padding: "16px 28px 0" }}>
          <h3 style={{ fontSize: "15px", fontWeight: 600, color: COLORS.textSecondary }}>Executive Insight</h3>
          <P
            text={article.executive_insight}
            fallback={topScore?.explanation ? `${topScore.explanation}.` : "Tap Ask AI for a detailed executive briefing."}
          />
          <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginTop: "8px" }} />
        </div>

        {/* ═══ ZONE G: CORE MECHANISM ═══ */}
        {hasDeep && di?.core_mechanism && (
          <Section title="Core Mechanism" defaultOpen accent={COLORS.brand}>
            <P text={di.core_mechanism as string} />
            {hasDeep && typeof di?.translation === "string" && (
              <p style={{ fontSize: "13px", color: COLORS.brand, fontStyle: "italic", marginTop: "4px" }}>
                {di.translation}
              </p>
            )}
          </Section>
        )}

        {/* ═══ ZONE H: IMPACT ANALYSIS (6 dimensions) ═══ */}
        {hasDeep && di?.impact_analysis && (
          <Section title="Impact Analysis" defaultOpen>
            {renderDeepDict(di.impact_analysis)}
          </Section>
        )}
        {/* Fallback to old field name */}
        {hasDeep && !di?.impact_analysis && di?.esg_impact_analysis && (
          <Section title="ESG Impact Analysis" defaultOpen>
            {renderDeepDict(di.esg_impact_analysis)}
          </Section>
        )}

        {/* ═══ ZONE I: FRAMEWORK ALIGNMENT v2 ═══ */}
        {article.framework_matches && article.framework_matches.length > 0 ? (
          <Section title="Framework Alignment">
            <FrameworkAlignmentV2 frameworkMatches={article.framework_matches} />
          </Section>
        ) : hasDeep && di?.compliance_regulatory_impact ? (
          <Section title="Compliance & Regulatory Impact">
            {renderDeepDict(di.compliance_regulatory_impact)}
          </Section>
        ) : null}

        {/* ═══ ZONE J: FINANCIAL & VALUATION ═══ */}
        {hasDeep && di?.financial_valuation_impact && (
          <Section title="Financial & Valuation Impact">
            {renderDeepDict(di.financial_valuation_impact)}
          </Section>
        )}

        {/* ═══ ZONE K: TIME HORIZON ═══ */}
        {hasDeep && di?.time_horizon && (
          <Section title="Time Horizon">
            {renderDeepDict(di.time_horizon)}
          </Section>
        )}

        {/* ═══ ZONE L: NET IMPACT SUMMARY ═══ */}
        {hasDeep && (di?.net_impact_summary || di?.final_synthesis) && (
          <Section title="Net Impact Summary" defaultOpen>
            <P text={(di.net_impact_summary || di.final_synthesis) as string} />
          </Section>
        )}

        {/* ═══ ZONE M: NLP EVIDENCE (default closed) ═══ */}
        {nlp && (nlp.esg_signals?.named_entities?.length || nlp.esg_signals?.quantitative_claims?.length || nlp.esg_signals?.regulatory_references?.length) && (
          <Section title="NLP Evidence">
            <NlpEvidencePanel nlpExtraction={nlp} />
          </Section>
        )}

        {/* ═══ ZONE N: GEOGRAPHIC SIGNAL (default closed) ═══ */}
        {article.geographic_signal && (
          <Section title="Geographic Intelligence">
            <GeographicSignalPanel geoSignal={article.geographic_signal} />
          </Section>
        )}

        {/* ═══ REREACT RECOMMENDATIONS ═══ */}
        {hasRereact && (
          <div style={{ padding: "16px 28px 0" }}>
            <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginBottom: "16px" }} />
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: COLORS.brand, marginBottom: "4px" }}>
              AI Recommendations
            </h3>
            <p style={{ fontSize: "12px", color: COLORS.textMuted, marginBottom: "12px" }}>
              Validated by 3-agent RE³ pipeline
            </p>
            {rr!.validated_recommendations.map((rec, i) => (
              <RecommendationCard key={i} rec={rec} index={i} />
            ))}
            {rr!.validation_summary && (
              <p style={{ fontSize: "12px", color: COLORS.textMuted, fontStyle: "italic", marginTop: "8px" }}>{rr!.validation_summary}</p>
            )}
          </div>
        )}

        {/* ═══ CAUSAL CHAIN ═══ */}
        <div style={{ padding: "16px 28px 0" }}>
          <div style={{ borderBottom: `1px solid ${COLORS.textDisabled}`, marginBottom: "16px" }} />
          <h3 style={{ fontSize: "15px", fontWeight: 600, color: COLORS.textSecondary }}>Causal Chain Analysis</h3>
        </div>
        <div style={{ marginTop: "8px" }}>
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

        {/* ═══ ACTION BUTTONS ═══ */}
        <div className="flex gap-3" style={{ padding: "24px 28px 32px" }}>
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
