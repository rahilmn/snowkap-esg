/**
 * Phase 3G: User preferences settings page.
 * Framework chips, pillar toggles, alert threshold, content depth.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { COLORS, RADII } from "../lib/designTokens";
import { preferences } from "../lib/api";

const ALL_FRAMEWORKS = ["BRSR", "GRI", "TCFD", "ESRS", "CDP", "IFRS_S1", "IFRS_S2", "CSRD", "SASB"];
const ALL_PILLARS = ["E", "S", "G"];
const DEPTH_OPTIONS = ["brief", "standard", "detailed"];

export default function PreferencesPage() {
  const navigate = useNavigate();
  const [selectedFrameworks, setSelectedFrameworks] = useState<string[]>([]);
  const [selectedPillars, setSelectedPillars] = useState<string[]>([]);
  const [alertThreshold, setAlertThreshold] = useState(70);
  const [contentDepth, setContentDepth] = useState("standard");
  const [dismissedTopics, setDismissedTopics] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    preferences.get().then((data) => {
      setSelectedFrameworks(data.preferred_frameworks || []);
      setSelectedPillars(data.preferred_pillars || []);
      setAlertThreshold(data.alert_threshold || 70);
      setContentDepth(data.content_depth || "standard");
      setDismissedTopics(data.dismissed_topics || []);
    }).catch(() => {});
  }, []);

  const toggleFramework = (fw: string) => {
    setSelectedFrameworks((prev) =>
      prev.includes(fw) ? prev.filter((f) => f !== fw) : [...prev, fw]
    );
  };

  const togglePillar = (p: string) => {
    setSelectedPillars((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    );
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await preferences.update({
        preferred_frameworks: selectedFrameworks,
        preferred_pillars: selectedPillars,
        alert_threshold: alertThreshold,
        content_depth: contentDepth,
        dismissed_topics: dismissedTopics,
      });
      navigate(-1);
    } catch {
      // silent
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-[440px] mx-auto min-h-screen" style={{ backgroundColor: COLORS.bgWhite }}>
      <div style={{ padding: "62px 47px 120px" }}>
        {/* Header */}
        <div className="flex items-center gap-3 mb-8">
          <button onClick={() => navigate(-1)} style={{ fontSize: "20px" }}>&larr;</button>
          <h1 style={{ fontSize: "20px", fontWeight: 500, color: COLORS.brand }}>
            Preferences
          </h1>
        </div>

        {/* Frameworks */}
        <section className="mb-6">
          <h2 style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textPrimary }}>
            Preferred Frameworks
          </h2>
          <div className="flex flex-wrap gap-2 mt-3">
            {ALL_FRAMEWORKS.map((fw) => (
              <button
                key={fw}
                onClick={() => toggleFramework(fw)}
                style={{
                  padding: "6px 16px",
                  borderRadius: RADII.pill,
                  fontSize: "14px",
                  fontWeight: 500,
                  border: `1px solid ${selectedFrameworks.includes(fw) ? COLORS.brand : COLORS.textDisabled}`,
                  backgroundColor: selectedFrameworks.includes(fw) ? COLORS.brandLight : COLORS.bgWhite,
                  color: selectedFrameworks.includes(fw) ? COLORS.brand : COLORS.textSecondary,
                  cursor: "pointer",
                }}
              >
                {fw.replace("_", " ")}
              </button>
            ))}
          </div>
        </section>

        {/* ESG Pillars */}
        <section className="mb-6">
          <h2 style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textPrimary }}>
            ESG Pillars
          </h2>
          <div className="flex gap-3 mt-3">
            {ALL_PILLARS.map((p) => {
              const labels: Record<string, string> = { E: "Environmental", S: "Social", G: "Governance" };
              return (
                <button
                  key={p}
                  onClick={() => togglePillar(p)}
                  style={{
                    padding: "8px 20px",
                    borderRadius: RADII.pill,
                    fontSize: "14px",
                    fontWeight: 500,
                    border: `1px solid ${selectedPillars.includes(p) ? COLORS.brand : COLORS.textDisabled}`,
                    backgroundColor: selectedPillars.includes(p) ? COLORS.brandLight : COLORS.bgWhite,
                    color: selectedPillars.includes(p) ? COLORS.brand : COLORS.textSecondary,
                    cursor: "pointer",
                  }}
                >
                  {labels[p]}
                </button>
              );
            })}
          </div>
        </section>

        {/* Alert Threshold */}
        <section className="mb-6">
          <h2 style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textPrimary }}>
            Alert Threshold: {alertThreshold}
          </h2>
          <input
            type="range"
            min={0}
            max={100}
            value={alertThreshold}
            onChange={(e) => setAlertThreshold(Number(e.target.value))}
            className="w-full mt-3"
            style={{ accentColor: COLORS.brand }}
          />
          <div className="flex justify-between" style={{ fontSize: "12px", color: COLORS.textMuted }}>
            <span>Low (all news)</span>
            <span>Critical only</span>
          </div>
        </section>

        {/* Content Depth */}
        <section className="mb-6">
          <h2 style={{ fontSize: "16px", fontWeight: 500, color: COLORS.textPrimary }}>
            Content Depth
          </h2>
          <div className="flex gap-3 mt-3">
            {DEPTH_OPTIONS.map((d) => (
              <button
                key={d}
                onClick={() => setContentDepth(d)}
                style={{
                  padding: "8px 20px",
                  borderRadius: RADII.pill,
                  fontSize: "14px",
                  fontWeight: 500,
                  border: `1px solid ${contentDepth === d ? COLORS.brand : COLORS.textDisabled}`,
                  backgroundColor: contentDepth === d ? COLORS.brandLight : COLORS.bgWhite,
                  color: contentDepth === d ? COLORS.brand : COLORS.textSecondary,
                  cursor: "pointer",
                  textTransform: "capitalize",
                }}
              >
                {d}
              </button>
            ))}
          </div>
        </section>

        {/* Save */}
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            width: "100%",
            padding: "14px",
            backgroundColor: COLORS.darkCard,
            color: COLORS.bgWhite,
            borderRadius: RADII.button,
            fontSize: "20px",
            fontWeight: 500,
            border: "none",
            cursor: saving ? "not-allowed" : "pointer",
            opacity: saving ? 0.7 : 1,
          }}
        >
          {saving ? "Saving..." : "Save Preferences"}
        </button>
      </div>
    </div>
  );
}
