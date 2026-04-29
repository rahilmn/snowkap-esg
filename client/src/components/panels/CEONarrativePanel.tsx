/** CEO narrative panel (Phase 4 Stage 11b output renderer).
 *
 * Renders board-ready content: 1-paragraph brief, stakeholder map, analogous
 * precedent card, 3-year trajectory (do-nothing vs act-now), Q&A drafts for
 * earnings call / press / board / regulator.
 *
 * Designed to land in a "CEO view" tab next to CFO + ESG Analyst. Every
 * section degrades gracefully when the underlying field is empty.
 */

import type {
  CeoAnalogousPrecedent,
  CeoNarrativePerspective,
  CeoStakeholderEntry,
} from "@/types/perspectives";

interface CEONarrativePanelProps {
  data: CeoNarrativePerspective;
  companyName?: string;
}

export function CEONarrativePanel({ data, companyName }: CEONarrativePanelProps) {
  return (
    <div className="space-y-6 text-sm text-gray-800">
      {/* Headline + board paragraph */}
      <header className="border-l-4 border-rose-600 pl-4">
        <h2 className="text-lg font-semibold text-gray-900">{data.headline}</h2>
        {companyName && (
          <p className="text-xs text-gray-500 mt-1">CEO view · {companyName}</p>
        )}
      </header>

      {data.board_paragraph && (
        <section className="bg-rose-50 border border-rose-100 rounded p-4">
          <div className="text-[11px] uppercase text-rose-700 font-semibold mb-2">
            Board-ready brief
          </div>
          <p className="text-sm leading-relaxed text-gray-800">
            {data.board_paragraph}
          </p>
        </section>
      )}

      {/* Stakeholder map */}
      {data.stakeholder_map.length > 0 && (
        <StakeholderMap stakeholders={data.stakeholder_map} />
      )}

      {/* Analogous precedent */}
      {data.analogous_precedent && Object.keys(data.analogous_precedent).length > 0 && (
        <PrecedentCard precedent={data.analogous_precedent} />
      )}

      {/* 3-year trajectory */}
      {(data.three_year_trajectory.do_nothing || data.three_year_trajectory.act_now) && (
        <Trajectory traj={data.three_year_trajectory} />
      )}

      {/* Q&A drafts */}
      {Object.values(data.qna_drafts).some(Boolean) && (
        <QnaAccordion qna={data.qna_drafts} />
      )}

      {/* Warnings */}
      {data.warnings.length > 0 && (
        <div className="text-[11px] text-amber-600 border-l-2 border-amber-300 pl-2">
          {data.warnings.map((w, i) => (
            <div key={i}>⚠ {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StakeholderMap({ stakeholders }: { stakeholders: CeoStakeholderEntry[] }) {
  return (
    <section>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">
        Stakeholder map ({stakeholders.length})
      </h3>
      <div className="space-y-2">
        {stakeholders.map((s, i) => (
          <div
            key={i}
            className="bg-white border border-gray-200 rounded p-3 text-xs"
          >
            <div className="font-semibold text-gray-900">{s.stakeholder}</div>
            <div className="mt-1 text-gray-700">
              <span className="text-gray-500 font-medium mr-1">Stance:</span>
              {s.stance}
            </div>
            {s.precedent && (
              <div className="mt-1 text-gray-600 italic">
                <span className="text-gray-500 font-medium mr-1 not-italic">
                  Precedent:
                </span>
                {s.precedent}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function PrecedentCard({ precedent }: { precedent: CeoAnalogousPrecedent }) {
  const hasYear = precedent.year;
  const hasCost = precedent.cost;
  const hasDuration = precedent.duration;
  return (
    <section>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">
        Closest analogous precedent
      </h3>
      <div className="bg-indigo-50 border-l-4 border-indigo-500 rounded-r p-4">
        {precedent.case_name && (
          <div className="font-semibold text-gray-900">
            {precedent.case_name}
          </div>
        )}
        <div className="text-xs text-gray-700 mt-1">
          {precedent.company && <span className="font-medium">{precedent.company}</span>}
          {hasYear && <span className="ml-1">({precedent.year})</span>}
          {hasCost && (
            <span className="ml-2 text-gray-500">· cost {precedent.cost}</span>
          )}
          {hasDuration && (
            <span className="ml-2 text-gray-500">· duration {precedent.duration}</span>
          )}
        </div>
        {precedent.outcome && (
          <div className="mt-2 text-xs text-gray-700">
            <span className="text-gray-500 font-medium mr-1">Outcome:</span>
            {precedent.outcome}
          </div>
        )}
        {precedent.applicability && (
          <div className="mt-2 text-xs text-gray-600 italic">
            Why this matches: {precedent.applicability}
          </div>
        )}
      </div>
    </section>
  );
}

function Trajectory({
  traj,
}: {
  traj: { do_nothing?: string; act_now?: string };
}) {
  return (
    <section>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">3-year trajectory</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {traj.do_nothing && (
          <div className="bg-red-50 border border-red-100 rounded p-3">
            <div className="text-[11px] uppercase text-red-700 font-semibold mb-1">
              Do nothing
            </div>
            <div className="text-xs text-gray-800">{traj.do_nothing}</div>
          </div>
        )}
        {traj.act_now && (
          <div className="bg-emerald-50 border border-emerald-100 rounded p-3">
            <div className="text-[11px] uppercase text-emerald-700 font-semibold mb-1">
              Act now
            </div>
            <div className="text-xs text-gray-800">{traj.act_now}</div>
          </div>
        )}
      </div>
    </section>
  );
}

function QnaAccordion({
  qna,
}: {
  qna: {
    earnings_call?: string;
    press_statement?: string;
    board_qa?: string;
    regulator_qa?: string;
  };
}) {
  const contexts: { key: keyof typeof qna; label: string }[] = [
    { key: "earnings_call", label: "Earnings call" },
    { key: "press_statement", label: "Press statement" },
    { key: "board_qa", label: "Board Q&A" },
    { key: "regulator_qa", label: "Regulator Q&A" },
  ];
  return (
    <section>
      <h3 className="text-sm font-semibold text-gray-700 mb-2">Q&A drafts</h3>
      <div className="space-y-2">
        {contexts.map(({ key, label }) =>
          qna[key] ? (
            <details
              key={key}
              className="bg-gray-50 border border-gray-200 rounded p-2"
            >
              <summary className="cursor-pointer text-xs font-semibold text-gray-700">
                {label}
              </summary>
              <p className="mt-2 text-xs text-gray-700 leading-relaxed">
                {qna[key]}
              </p>
            </details>
          ) : null
        )}
      </div>
    </section>
  );
}
