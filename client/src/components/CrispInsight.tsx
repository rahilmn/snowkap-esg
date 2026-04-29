import type { ReactNode } from "react";
import type { CrispView } from "@/lib/snowkap-api";

const LEVEL_COLOR = {
  HIGH: "bg-red-500 text-white",
  MEDIUM: "bg-amber-500 text-white",
  LOW: "bg-emerald-500 text-white",
} as const;

const PERSPECTIVE_LABEL: Record<string, { label: string; tagline: string; tint: string }> = {
  "esg-analyst": {
    label: "ESG Analyst View",
    tagline: "Frameworks · Disclosures · KPIs",
    tint: "bg-purple-100 text-purple-700",
  },
  cfo: {
    label: "CFO View",
    tagline: "P&L · Cost of capital · Cash flow",
    tint: "bg-blue-100 text-blue-700",
  },
  ceo: {
    label: "CEO View",
    tagline: "Strategy · Brand · Competitive position",
    tint: "bg-orange-100 text-orange-700",
  },
};

export function CrispInsight({ view }: { view: CrispView }) {
  const grid = (view.impact_grid as Record<string, "HIGH" | "MEDIUM" | "LOW">) || {};
  const gridCols = Object.entries(grid);
  const meta =
    PERSPECTIVE_LABEL[view.perspective] ??
    PERSPECTIVE_LABEL["esg-analyst"] ?? {
      label: "Analyst View",
      tagline: "",
      tint: "bg-slate-100 text-slate-600",
    };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      {/* Lens label */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <span
          className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ${meta.tint}`}
        >
          {meta.label}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-slate-400">
          {meta.tagline}
        </span>
      </div>

      <h3 className="text-lg font-semibold leading-snug text-slate-900">
        {view.headline}
      </h3>

      {/* Impact grid — columns vary per perspective */}
      {gridCols.length > 0 && (
        <div className="mt-4 grid gap-2" style={{ gridTemplateColumns: `repeat(${gridCols.length}, minmax(0, 1fr))` }}>
          {gridCols.map(([col, level]) => (
            <div
              key={col}
              className={`rounded-lg px-3 py-2 text-center text-xs font-semibold uppercase tracking-wide ${
                LEVEL_COLOR[level] ?? "bg-slate-300 text-slate-700"
              }`}
              title={col}  /* Phase 13 P7: full label on hover for truncated grids */
            >
              {/* Phase 13 P7: truncate long dimension labels (e.g. "Stakeholder Impact")
                  so the 3-4 col grid stays readable at 375-440px panel width. */}
              <div className="opacity-80 truncate">{col}</div>
              <div className="text-sm">{level ?? "—"}</div>
            </div>
          ))}
        </div>
      )}

      {/* What matters
          Phase 16 white-screen fix: CEO + ESG-Analyst perspective generators
          (Phase 4) emit different field sets than the legacy CFO transform —
          they produce `kpi_table` / `stakeholder_map` / `analogous_precedent`
          INSTEAD OF `what_matters` and `action`. Without these guards, the
          unconditional `view.what_matters.length` access crashed the entire
          panel to a white screen on toggle to CEO or ESG Analyst. */}
      {Array.isArray(view.what_matters) && view.what_matters.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            What matters for {meta.label.replace(" View", "")}
          </div>
          <ul className="space-y-1.5 text-sm text-slate-700">
            {view.what_matters.map((bullet, idx) => (
              <li key={idx} className="flex gap-2">
                <span className="text-orange-500">•</span>
                <span>{bullet}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action — only render if the perspective shape carries it. */}
      {Array.isArray(view.action) && view.action.length > 0 && (
        <div
          className={`mt-4 rounded-lg border-2 px-3 py-2 text-sm ${
            view.do_nothing
              ? "border-slate-300 bg-slate-50 text-slate-600"
              : "border-emerald-500 bg-emerald-50 text-emerald-800"
          }`}
        >
          <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide">
            {view.do_nothing ? "No Action Required" : "Action"}
          </div>
          {view.action.map((a, idx) => (
            <div key={idx}>{a}</div>
          ))}
        </div>
      )}

      {/* Ontology-sourced impact dimensions */}
      {view.active_impact_dimensions && view.active_impact_dimensions.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Ontology dimensions for this lens
          </div>
          <div className="flex flex-wrap gap-1.5">
            {view.active_impact_dimensions.map((d) => (
              <span
                key={d}
                className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600"
              >
                {d}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Phase 16 — rich perspective extras. CEO + ESG-Analyst Phase 4
          generators emit fields beyond the legacy what_matters/action shape.
          Render whatever's present without crashing on missing fields. */}
      <RichPerspectiveExtras view={view} />

      {/* Materiality footer */}
      <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
        <span>
          Materiality: <span className="font-semibold text-slate-600">{view.materiality}</span>
        </span>
      </div>
    </div>
  );
}


/**
 * Phase 16 — render the rich extras carried by CEO + ESG-Analyst perspectives.
 * All fields are optional; missing ones are skipped silently. The shape
 * mirrors what `engine/analysis/ceo_narrative_generator.py` and
 * `engine/analysis/esg_analyst_generator.py` emit, plus the audit_trail
 * field added in Phase 13 S1 + Phase 14.
 */
function RichPerspectiveExtras({ view }: { view: CrispView }) {
  // Cast to a flexible shape since the canonical TypeScript CrispView is
  // a union not exhaustively typed for the dedicated-generator outputs.
  const v = view as unknown as Record<string, unknown>;

  const boardParagraph = typeof v.board_paragraph === "string" ? v.board_paragraph : "";
  const stakeholderMap = Array.isArray(v.stakeholder_map) ? v.stakeholder_map : null;
  const precedent = (v.analogous_precedent && typeof v.analogous_precedent === "object")
    ? v.analogous_precedent as Record<string, string>
    : null;
  const trajectory = (v.three_year_trajectory && typeof v.three_year_trajectory === "object")
    ? v.three_year_trajectory as Record<string, string>
    : null;
  const qna = (v.qna_drafts && typeof v.qna_drafts === "object")
    ? v.qna_drafts as Record<string, string>
    : null;
  const kpiTable = Array.isArray(v.kpi_table) ? v.kpi_table : null;
  const auditTrail = Array.isArray(v.audit_trail) ? v.audit_trail : null;
  const frameworkCitations = Array.isArray(v.framework_citations) ? v.framework_citations : null;

  if (
    !boardParagraph && !stakeholderMap && !precedent && !trajectory &&
    !qna && !kpiTable && !auditTrail && !frameworkCitations
  ) {
    return null;
  }

  return (
    <div className="mt-4 space-y-4">
      {boardParagraph && (
        <Block label="Board paragraph">
          <p className="text-sm leading-relaxed text-slate-700">{boardParagraph}</p>
        </Block>
      )}

      {stakeholderMap && stakeholderMap.length > 0 && (
        <Block label={`Stakeholder map · ${stakeholderMap.length}`}>
          <div className="space-y-2">
            {stakeholderMap.slice(0, 6).map((s, i) => {
              const r = (s as Record<string, unknown>);
              return (
                <div key={i} className="rounded-md border border-slate-200 bg-slate-50 p-2.5">
                  <div className="text-xs font-semibold text-slate-900">{String(r.stakeholder ?? "")}</div>
                  {typeof r.stance === "string" && r.stance && (
                    <div className="mt-1 text-xs text-slate-700 leading-relaxed">{r.stance}</div>
                  )}
                  {typeof r.precedent === "string" && r.precedent && (
                    <div className="mt-1 text-[11px] italic text-slate-500">Precedent: {r.precedent}</div>
                  )}
                </div>
              );
            })}
          </div>
        </Block>
      )}

      {precedent?.case_name && (
        <Block label="Analogous precedent">
          <div className="rounded-md border border-amber-200 bg-amber-50 p-2.5">
            <div className="text-xs font-semibold text-amber-900">
              {precedent.case_name}
              {precedent.year && <span className="ml-2 font-normal text-amber-700">({precedent.year})</span>}
              {precedent.cost && <span className="ml-2 font-normal text-amber-700">· {precedent.cost}</span>}
            </div>
            {precedent.outcome && (
              <div className="mt-1 text-xs leading-relaxed text-amber-800">{precedent.outcome}</div>
            )}
            {precedent.applicability && (
              <div className="mt-1 text-[11px] italic text-amber-700">{precedent.applicability}</div>
            )}
          </div>
        </Block>
      )}

      {trajectory && (trajectory.do_nothing || trajectory.act_now) && (
        <Block label="3-year trajectory">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {trajectory.do_nothing && (
              <div className="rounded-md border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-900">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-rose-700">Do nothing</div>
                <div className="mt-1 leading-relaxed">{trajectory.do_nothing}</div>
              </div>
            )}
            {trajectory.act_now && (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2.5 text-xs text-emerald-900">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-emerald-700">Act now</div>
                <div className="mt-1 leading-relaxed">{trajectory.act_now}</div>
              </div>
            )}
          </div>
        </Block>
      )}

      {qna && Object.keys(qna).length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500 select-none">
            Q&amp;A drafts ({Object.keys(qna).length})
          </summary>
          <div className="mt-2 space-y-2">
            {Object.entries(qna).map(([k, val]) => typeof val === "string" && val ? (
              <div key={k} className="rounded border border-slate-200 bg-white p-2.5 text-xs">
                <div className="font-semibold capitalize text-slate-600">{k.replace(/_/g, " ")}</div>
                <div className="mt-1 text-slate-700 leading-relaxed">{val}</div>
              </div>
            ) : null)}
          </div>
        </details>
      )}

      {kpiTable && kpiTable.length > 0 && (
        <Block label={`KPI table · ${kpiTable.length}`}>
          <div className="space-y-2">
            {kpiTable.slice(0, 5).map((k, i) => {
              const r = k as Record<string, unknown>;
              return (
                <div key={i} className="rounded-md border border-slate-200 bg-slate-50 p-2.5">
                  <div className="text-xs font-semibold text-slate-900">
                    {String(r.kpi_name ?? "")}
                    {r.company_value !== undefined && (
                      <span className="ml-2 font-normal text-slate-700">
                        · {String(r.company_value)} {String(r.unit ?? "")}
                      </span>
                    )}
                  </div>
                  {typeof r.peer_quartile === "string" && r.peer_quartile && (
                    <div className="mt-1 text-[11px] text-slate-600">
                      Peer quartile: <span className="font-medium">{r.peer_quartile}</span>
                      {typeof r.peer_examples === "string" && r.peer_examples && (
                        <span className="ml-2 text-slate-500">({r.peer_examples})</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Block>
      )}

      {frameworkCitations && frameworkCitations.length > 0 && (
        <Block label={`Frameworks · ${frameworkCitations.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {frameworkCitations.slice(0, 8).map((c, i) => {
              const r = c as Record<string, unknown>;
              const code = String(r.code ?? r);
              const deadline = typeof r.deadline === "string" ? r.deadline : "";
              return (
                <span
                  key={i}
                  title={typeof r.rationale === "string" ? r.rationale : undefined}
                  className="rounded bg-purple-100 px-2 py-0.5 text-[10px] font-medium text-purple-700"
                >
                  {code}
                  {deadline && <span className="ml-1 text-purple-500">{deadline}</span>}
                </span>
              );
            })}
          </div>
        </Block>
      )}

      {auditTrail && auditTrail.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500 select-none">
            Audit trail · {auditTrail.length}
          </summary>
          <div className="mt-2 space-y-1.5">
            {auditTrail.slice(0, 6).map((a, i) => {
              const r = a as Record<string, unknown>;
              const claim = String(r.claim ?? r.value ?? "");
              const derivation = typeof r.derivation === "string" ? r.derivation : "";
              const sources = Array.isArray(r.sources) ? r.sources : null;
              return (
                <div key={i} className="rounded border border-slate-200 bg-white p-2 text-[11px]">
                  <div className="font-medium text-slate-700">{claim}</div>
                  {derivation && <div className="mt-0.5 text-slate-600 italic">{derivation}</div>}
                  {sources && sources.length > 0 && (
                    <div className="mt-1 text-slate-500">← {sources.map(String).join(" · ")}</div>
                  )}
                </div>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}

function Block({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      {children}
    </div>
  );
}
