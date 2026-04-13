import { usePerspective } from "@/stores/perspectiveStore";
import type { Perspective } from "@/lib/snowkap-api";

const LENSES: { value: Perspective; label: string; hint: string }[] = [
  { value: "esg-analyst", label: "ESG Analyst", hint: "Deep frameworks" },
  { value: "cfo", label: "CFO", hint: "10-sec verdict" },
  { value: "ceo", label: "CEO", hint: "Strategic brief" },
];

export function PerspectiveSwitcher() {
  const { active, setActive } = usePerspective();
  return (
    <div className="inline-flex rounded-lg border border-slate-300 bg-white p-0.5 shadow-sm">
      {LENSES.map((lens) => {
        const isActive = active === lens.value;
        return (
          <button
            key={lens.value}
            type="button"
            onClick={() => setActive(lens.value)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              isActive
                ? "bg-orange-500 text-white shadow"
                : "text-slate-600 hover:text-slate-900"
            }`}
            title={lens.hint}
          >
            {lens.label}
          </button>
        );
      })}
    </div>
  );
}
