import * as Tooltip from "@radix-ui/react-tooltip";
import { renderRupee, type RupeeContext } from "../../lib/number_format";

export interface ProvenanceMeta {
  source?: "engine_estimate" | "from_article" | string;
  beta?: number;
  lag_months?: number;
  lo_cr?: number;
  hi_cr?: number;
  derived_from?: string;
}

interface Props {
  amount_cr: number;
  context?: RupeeContext;
  range_pct?: number;
  provenance?: ProvenanceMeta | null;
  className?: string;
}

function buildTooltip(
  amount_cr: number,
  provenance: ProvenanceMeta | null | undefined,
): string {
  if (!provenance) return `Engine value · ₹${amount_cr.toLocaleString("en-IN")} Cr`;

  const parts: string[] = [];
  if (provenance.source === "from_article") parts.push("From article");
  else if (provenance.source === "engine_estimate") parts.push("Engine cascade");
  else if (provenance.source) parts.push(String(provenance.source));
  else parts.push("Engine value");

  if (typeof provenance.beta === "number")
    parts.push(`β=${provenance.beta.toFixed(2)}`);
  if (typeof provenance.lag_months === "number")
    parts.push(`${provenance.lag_months}mo lag`);
  if (
    typeof provenance.lo_cr === "number" &&
    typeof provenance.hi_cr === "number"
  ) {
    parts.push(
      `range ₹${provenance.lo_cr.toLocaleString("en-IN")}–${provenance.hi_cr.toLocaleString("en-IN")} Cr`,
    );
  } else {
    parts.push(`point ₹${amount_cr.toLocaleString("en-IN")} Cr`);
  }
  if (provenance.derived_from)
    parts.push(`derived from: ${provenance.derived_from}`);
  return parts.join(" · ");
}

export function NumberWithProvenance({
  amount_cr,
  context = "body",
  range_pct = 10,
  provenance = null,
  className,
}: Props) {
  const rendered = renderRupee(amount_cr, { context, range_pct });
  const tooltip = buildTooltip(amount_cr, provenance);

  const triggerClass =
    "underline decoration-dotted decoration-slate-400 underline-offset-2 cursor-help focus:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 rounded";

  return (
    <Tooltip.Provider delayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span
            tabIndex={0}
            className={`${triggerClass} ${className ?? ""}`.trim()}
            aria-label={tooltip}
          >
            {rendered}
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            side="top"
            align="center"
            sideOffset={6}
            className="z-50 max-w-xs rounded-md bg-slate-900 px-3 py-2 text-xs leading-snug text-white shadow-lg"
          >
            {tooltip}
            <Tooltip.Arrow className="fill-slate-900" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

export default NumberWithProvenance;
