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
            >
              <div className="opacity-80">{col}</div>
              <div className="text-sm">{level ?? "—"}</div>
            </div>
          ))}
        </div>
      )}

      {/* What matters */}
      {view.what_matters.length > 0 && (
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

      {/* Action */}
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

      {/* Materiality footer */}
      <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
        <span>
          Materiality: <span className="font-semibold text-slate-600">{view.materiality}</span>
        </span>
      </div>
    </div>
  );
}
