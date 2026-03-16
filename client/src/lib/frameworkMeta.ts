/** Framework metadata — colors, labels, and parsing utilities for Stage 6.6. */

export const FRAMEWORK_COLORS: Record<string, string> = {
  BRSR: "bg-orange-100 text-orange-800 border-orange-300",
  GRI: "bg-blue-100 text-blue-800 border-blue-300",
  TCFD: "bg-teal-100 text-teal-800 border-teal-300",
  ESRS: "bg-purple-100 text-purple-800 border-purple-300",
  CDP: "bg-green-100 text-green-800 border-green-300",
  IFRS_S1: "bg-indigo-100 text-indigo-800 border-indigo-300",
  IFRS_S2: "bg-indigo-100 text-indigo-800 border-indigo-300",
  CSRD: "bg-pink-100 text-pink-800 border-pink-300",
  SASB: "bg-slate-100 text-slate-800 border-slate-300",
};

export const FRAMEWORK_LABELS: Record<string, string> = {
  BRSR: "BRSR",
  GRI: "GRI",
  TCFD: "TCFD",
  ESRS: "ESRS",
  CDP: "CDP",
  IFRS_S1: "IFRS S1",
  IFRS_S2: "IFRS S2",
  CSRD: "CSRD",
  SASB: "SASB",
};

export const FRAMEWORK_DOT_COLORS: Record<string, string> = {
  BRSR: "bg-orange-500",
  GRI: "bg-blue-500",
  TCFD: "bg-teal-500",
  ESRS: "bg-purple-500",
  CDP: "bg-green-500",
  IFRS_S1: "bg-indigo-500",
  IFRS_S2: "bg-indigo-500",
  CSRD: "bg-pink-500",
  SASB: "bg-slate-500",
};

export interface ParsedFramework {
  framework: string;
  indicator: string | null;
}

/** Parse "GRI 305" or "BRSR:P6" into { framework, indicator } */
export function parseFrameworkTag(tag: string): ParsedFramework {
  if (tag.includes(":")) {
    const parts = tag.split(":", 2);
    return { framework: (parts[0] ?? "").trim(), indicator: (parts[1] ?? "").trim() || null };
  }
  if (tag.includes(" ")) {
    const parts = tag.split(" ", 2);
    return { framework: (parts[0] ?? "").trim(), indicator: (parts[1] ?? "").trim() || null };
  }
  return { framework: tag.trim(), indicator: null };
}

/** Frontend fallback: infer likely frameworks from ESG pillar if backend data missing */
export function inferFrameworks(esgPillar: string | null): string[] {
  switch (esgPillar?.toLowerCase()) {
    case "environmental":
    case "e":
      return ["BRSR:P6", "GRI:305", "TCFD:Metrics", "CDP:Climate", "ESRS:E1"];
    case "social":
    case "s":
      return ["BRSR:P3", "GRI:403", "ESRS:S1"];
    case "governance":
    case "g":
      return ["BRSR:P1", "GRI:205", "ESRS:G1"];
    default:
      return [];
  }
}

export function getFrameworkColor(framework: string): string {
  return FRAMEWORK_COLORS[framework] || "bg-gray-100 text-gray-800 border-gray-300";
}
