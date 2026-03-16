/** Intro card — FOMO metrics on session start (Stage 6.4) */

import { useAuthStore } from "@/stores/authStore";

interface IntroCardProps {
  articleCount: number;
  highImpactCount: number;
  predictionCount: number;
  newSinceLastVisit: number;
  onStart: () => void;
}

export function IntroCard({
  articleCount,
  highImpactCount,
  predictionCount,
  newSinceLastVisit,
  onStart,
}: IntroCardProps) {
  const name = useAuthStore((s) => s.name);
  const firstName = name?.split(" ")[0] || "there";

  return (
    <div className="rounded-xl shadow-lg bg-white border border-gray-100 overflow-hidden select-none w-full max-w-[400px]">
      <div className="bg-gradient-to-br from-indigo-600 to-purple-700 px-6 py-8 text-white">
        <h2 className="text-xl font-bold">
          Hello {firstName},
        </h2>
        <p className="mt-1 text-sm text-white/80">
          Here are {articleCount} news stories that might impact your business today
        </p>
      </div>

      <div className="px-6 py-4 grid grid-cols-3 gap-3">
        <MetricCard value={highImpactCount} label="High Impact" color="text-red-600" />
        <MetricCard value={predictionCount} label="Predictions" color="text-amber-600" />
        <MetricCard value={newSinceLastVisit} label="New Today" color="text-blue-600" />
      </div>

      <div className="px-6 pb-6">
        <button
          onClick={onStart}
          className="w-full py-3 bg-indigo-600 hover:bg-indigo-700 text-white font-medium rounded-lg transition-colors"
        >
          Start Reading →
        </button>
      </div>
    </div>
  );
}

function MetricCard({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div className="text-center">
      <p className={`text-2xl font-bold ${color}`}>{value}</p>
      <p className="text-[10px] text-muted-foreground mt-0.5">{label}</p>
    </div>
  );
}
