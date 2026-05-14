/**
 * Phase 2.1 — Number rendering protocol (renderer-only).
 *
 * Three rules applied to every user-visible ₹ figure on narrative surfaces:
 *
 *   Rule 1: Round to 2 significant figures.
 *           1857.6  -> "1,900"
 *           56.1    -> "56"
 *           12345   -> "12,000"
 *
 *   Rule 2: Use ranges in narrative, point estimates in tables.
 *           context='body'     + range_pct=10 -> "₹1,700–2,100 Cr"
 *           context='headline'                -> "~₹1,900 Cr"
 *           context='table'                   -> "₹1,857.6 Cr"  (full precision)
 *
 *   Rule 3: Strip "(engine estimate)" / "(from article)" from rendered output.
 *           Provenance moves to a tooltip / hover (NumberWithProvenance).
 *
 * The cascade math view (Analyst) is the explicit exception — call with
 * context='table' to keep full precision for the audit trail.
 */

export type RupeePrecision = "sig2" | "range";
export type RupeeContext = "headline" | "body" | "table";

export interface RenderRupeeOptions {
  precision?: RupeePrecision;
  range_pct?: number;
  context?: RupeeContext;
}

const PROVENANCE_PATTERN =
  /\s*\((?:engine\s+estimate|from\s+article)\)\.?/gi;

/**
 * Round `value` to N significant figures.
 * Returns the rounded number (caller formats it).
 */
export function roundToSigFigs(value: number, sigFigs: number): number {
  if (!Number.isFinite(value) || value === 0) return 0;
  const sign = value < 0 ? -1 : 1;
  const abs = Math.abs(value);
  const magnitude = Math.floor(Math.log10(abs));
  const factor = Math.pow(10, sigFigs - 1 - magnitude);
  return sign * Math.round(abs * factor) / factor;
}

function formatIndianGrouping(value: number): string {
  // Render integers with Indian thousands separators.
  // For decimals, strip trailing zero noise from sig-fig rounding.
  if (Number.isInteger(value)) {
    return value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  return value.toLocaleString("en-IN", {
    maximumFractionDigits: 1,
    minimumFractionDigits: 0,
  });
}

/**
 * Render a ₹ figure (value in crores) per the protocol.
 *
 * Defaults:
 *   - context='body'
 *   - precision='range' for body, 'sig2' for headline/table
 *   - range_pct=10
 */
export function renderRupee(
  amount_cr: number,
  options: RenderRupeeOptions = {},
): string {
  if (!Number.isFinite(amount_cr)) return "—";

  const context = options.context ?? "body";
  const range_pct = options.range_pct ?? 10;
  const precision =
    options.precision ??
    (context === "body" ? "range" : "sig2");

  if (context === "table") {
    // Full precision — used by the Analyst cascade view only.
    const formatted = amount_cr.toLocaleString("en-IN", {
      maximumFractionDigits: 1,
      minimumFractionDigits: 0,
    });
    return `₹${formatted} Cr`;
  }

  const rounded = roundToSigFigs(amount_cr, 2);

  if (precision === "range" && context === "body") {
    const delta = (rounded * range_pct) / 100;
    const lo = roundToSigFigs(rounded - delta, 2);
    const hi = roundToSigFigs(rounded + delta, 2);
    if (lo === hi) {
      return `~₹${formatIndianGrouping(rounded)} Cr`;
    }
    return `₹${formatIndianGrouping(lo)}–${formatIndianGrouping(hi)} Cr`;
  }

  // headline default + sig2 fallback
  if (context === "headline") {
    return `~₹${formatIndianGrouping(rounded)} Cr`;
  }
  return `₹${formatIndianGrouping(rounded)} Cr`;
}

/**
 * Strip provenance tags ("(engine estimate)", "(from article)") from a string.
 * Used by panels that render LLM narrative text directly.
 */
export function stripProvenanceTags(text: string): string {
  if (!text) return text;
  return text.replace(PROVENANCE_PATTERN, "");
}

/**
 * Re-render every ₹ figure inside a narrative string per the protocol, and
 * strip provenance tags.
 *
 * Matches: ₹ 1,857.6 Cr (engine estimate)
 *          ₹500 Cr
 *          Rs 1,200 Cr
 *
 * Leaves non-Cr ₹ amounts untouched (e.g. ₹50 lakh, ₹100 crore in long form
 * — the engine standard is "Cr"; anything else is data the renderer should not
 * reformat).
 */
const RUPEE_CR_PATTERN =
  /(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)\s?Cr(\s*\((?:engine\s+estimate|from\s+article)\))?/gi;

export function rewriteNarrativeNumbers(
  text: string,
  context: RupeeContext = "body",
  range_pct = 10,
): string {
  if (!text) return text;
  return text.replace(RUPEE_CR_PATTERN, (_match, numStr: string) => {
    const numeric = parseFloat(numStr.replace(/,/g, ""));
    if (!Number.isFinite(numeric)) return _match;
    return renderRupee(numeric, { context, range_pct });
  });
}
