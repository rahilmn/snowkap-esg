/** Prediction summary panel (Stage 6.7) */

import type { ArticlePrediction } from "@/types";
import { confidenceLabel, formatCurrency } from "@/lib/utils";

interface PredictionSummaryProps {
  prediction: ArticlePrediction;
}

const RISK_STYLES: Record<string, string> = {
  low: "bg-green-100 text-green-700",
  medium: "bg-amber-100 text-amber-700",
  high: "bg-red-100 text-red-700",
  critical: "bg-red-200 text-red-800",
};

const HORIZON_LABELS: Record<string, string> = {
  short: "Short-term",
  short_term: "Short-term",
  medium: "Medium-term",
  medium_term: "Medium-term",
  long: "Long-term",
  long_term: "Long-term",
};

export function PredictionSummary({ prediction }: PredictionSummaryProps) {
  const conf = prediction.confidence_score;
  const confLabel = confidenceLabel(conf);
  const riskStyle = RISK_STYLES[prediction.risk_level || "medium"] || RISK_STYLES.medium;
  const horizonLabel = HORIZON_LABELS[prediction.time_horizon || "medium"] || prediction.time_horizon;

  return (
    <div className="bg-amber-50 rounded-lg p-4 border border-amber-200">
      <h4 className="text-sm font-semibold text-amber-900 mb-2">
        Prediction
      </h4>

      <h5 className="text-sm font-medium text-gray-900">{prediction.title}</h5>

      {prediction.summary && (
        <p className="text-xs text-gray-600 mt-1 leading-relaxed">{prediction.summary}</p>
      )}

      <div className="flex flex-wrap gap-2 mt-3">
        <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-white border border-amber-300 text-amber-800">
          Confidence: {(conf * 100).toFixed(0)}% ({confLabel})
        </span>
        {prediction.risk_level && (
          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${riskStyle}`}>
            Risk: {prediction.risk_level}
          </span>
        )}
        {horizonLabel && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
            {horizonLabel}
          </span>
        )}
        {prediction.financial_impact != null && (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-700">
            {formatCurrency(prediction.financial_impact)}
          </span>
        )}
      </div>

      {prediction.prediction_text && (
        <p className="text-xs text-gray-600 mt-3 leading-relaxed line-clamp-4">
          {prediction.prediction_text}
        </p>
      )}
    </div>
  );
}
