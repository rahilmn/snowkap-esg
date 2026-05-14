/** Phase 6 §8.2 — Persona MCQ wizard.
 *
 * Inline single-page form (not a multi-step wizard — 6 questions fit one
 * scroll on desktop and abandonment data favours a single submit). Reads
 * the schema from /api/me/persona/questions and the user's current
 * answers from /api/me/persona; saves via PUT.
 *
 * - Multi-select questions cap at `max_selections` (3 per plan §8.2)
 * - Single-select renders as radio chips
 * - "Skip for now" lets the user dismiss without saving — the section
 *   collapses but the banner stays so they can come back
 * - Optimistic UI: on save success the section updates immediately and
 *   the parent's mcq_completed callback fires
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  me,
  type Persona,
  type PersonaDecisionStyle,
  type PersonaHorizon,
  type PersonaRiskAppetite,
  type PersonaRole,
  type PersonaUpsertBody,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { COLORS } from "@/lib/designTokens";

interface Props {
  /** Called when persona is successfully saved (so parent can hide the
   * "complete your profile" banner). */
  onSaved?: (persona: Persona) => void;
}

type DraftAnswers = {
  role: PersonaRole;
  esg_focus: string[];
  frameworks: string[];
  geographies: string[];
  horizon: PersonaHorizon;
  decision_style: PersonaDecisionStyle;
  risk_appetite: PersonaRiskAppetite;
};

const ROLE_OPTIONS: { value: PersonaRole; label: string }[] = [
  { value: "cfo", label: "CFO" },
  { value: "ceo", label: "CEO" },
  { value: "analyst", label: "ESG Analyst" },
  { value: "other", label: "Other" },
];

const MULTI_SELECT_FIELDS = ["esg_focus", "frameworks", "geographies"] as const;
const SINGLE_SELECT_FIELDS = ["horizon", "decision_style", "risk_appetite"] as const;

export function PersonaMCQ({ onSaved }: Props) {
  const qc = useQueryClient();
  const questionsQ = useQuery({
    queryKey: ["persona", "questions"],
    queryFn: () => me.personaQuestions(),
    staleTime: 1000 * 60 * 60, // schema is static — cache for an hour
  });
  const personaQ = useQuery({
    queryKey: ["persona", "self"],
    queryFn: () => me.getPersona(),
  });

  const initial: DraftAnswers | null = useMemo(() => {
    const p = personaQ.data?.persona;
    if (!p) return null;
    return {
      role: p.role,
      esg_focus: [...(p.esg_focus || [])],
      frameworks: [...(p.frameworks || [])],
      geographies: [...(p.geographies || [])],
      horizon: p.horizon,
      decision_style: p.decision_style,
      risk_appetite: p.risk_appetite,
    };
  }, [personaQ.data]);

  const [draft, setDraft] = useState<DraftAnswers | null>(null);
  useEffect(() => {
    if (initial && !draft) setDraft(initial);
  }, [initial, draft]);

  const saveMutation = useMutation({
    mutationFn: (body: PersonaUpsertBody) => me.upsertPersona(body),
    onSuccess: (data) => {
      qc.setQueryData(["persona", "self"], data);
      onSaved?.(data.persona);
    },
  });

  if (questionsQ.isLoading || personaQ.isLoading || !draft) {
    return (
      <p style={{ fontSize: 13, color: COLORS.textMuted, margin: 0 }}>
        Loading your preferences…
      </p>
    );
  }

  if (questionsQ.error || personaQ.error) {
    return (
      <p style={{ fontSize: 13, color: "#DC2626", margin: 0 }}>
        Couldn't load persona settings. Please refresh.
      </p>
    );
  }

  const questions = questionsQ.data?.questions || [];

  const toggleMulti = (field: typeof MULTI_SELECT_FIELDS[number], value: string, max: number) => {
    setDraft((d) => {
      if (!d) return d;
      const current = d[field];
      const has = current.includes(value);
      let next: string[];
      if (has) {
        next = current.filter((v) => v !== value);
      } else if (current.length >= max) {
        // Capped — dropping the oldest is friendlier than blocking the click
        next = [...current.slice(1), value];
      } else {
        next = [...current, value];
      }
      return { ...d, [field]: next };
    });
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    saveMutation.mutate(draft as PersonaUpsertBody);
  };

  const dirty = initial && JSON.stringify(initial) !== JSON.stringify(draft);

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Role — not in PERSONA_QUESTIONS schema; rendered as the first chip row */}
      <FieldGroup label="Your role">
        <ChipRow
          options={ROLE_OPTIONS}
          selected={[draft.role]}
          onToggle={(v) =>
            setDraft((d) => (d ? { ...d, role: v as PersonaRole } : d))
          }
          mode="single"
        />
      </FieldGroup>

      {questions.map((q) => {
        const isMulti = q.type === "multi_select";
        const selected = (draft as Record<string, unknown>)[q.id] as string | string[];
        const selectedArr = Array.isArray(selected) ? selected : [selected];
        return (
          <FieldGroup
            key={q.id}
            label={q.question}
            hint={
              isMulti && q.max_selections
                ? `Pick up to ${q.max_selections}`
                : undefined
            }
          >
            <ChipRow
              options={q.options}
              selected={selectedArr}
              onToggle={(v) => {
                if (isMulti && (MULTI_SELECT_FIELDS as readonly string[]).includes(q.id)) {
                  toggleMulti(
                    q.id as typeof MULTI_SELECT_FIELDS[number],
                    v,
                    q.max_selections || 3,
                  );
                } else if ((SINGLE_SELECT_FIELDS as readonly string[]).includes(q.id)) {
                  setDraft((d) =>
                    d ? { ...d, [q.id]: v } as DraftAnswers : d,
                  );
                }
              }}
              mode={isMulti ? "multi" : "single"}
            />
          </FieldGroup>
        );
      })}

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 4 }}>
        <Button type="submit" disabled={saveMutation.isPending || !dirty}>
          {saveMutation.isPending
            ? "Saving…"
            : personaQ.data?.mcq_completed
              ? "Update preferences"
              : "Save preferences"}
        </Button>
        {!personaQ.data?.mcq_completed && (
          <span style={{ fontSize: 12, color: COLORS.textMuted }}>
            ~90 seconds
          </span>
        )}
        {saveMutation.isError && (
          <span style={{ fontSize: 13, color: "#DC2626" }}>
            Save failed. Try again.
          </span>
        )}
        {saveMutation.isSuccess && !dirty && (
          <span style={{ fontSize: 13, color: "#16A34A" }}>Saved.</span>
        )}
      </div>
    </form>
  );
}

function FieldGroup({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: COLORS.textPrimary }}>
          {label}
        </div>
        {hint && (
          <div style={{ fontSize: 11, color: COLORS.textMuted, marginTop: 2 }}>
            {hint}
          </div>
        )}
      </div>
      {children}
    </div>
  );
}

function ChipRow({
  options,
  selected,
  onToggle,
  mode,
}: {
  options: { value: string; label: string }[];
  selected: string[];
  onToggle: (value: string) => void;
  mode: "single" | "multi";
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
      {options.map((opt) => {
        const isOn = selected.includes(opt.value);
        return (
          <button
            type="button"
            key={opt.value}
            onClick={() => onToggle(opt.value)}
            aria-pressed={isOn}
            style={{
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: 500,
              borderRadius: 999,
              cursor: "pointer",
              border: `1px solid ${isOn ? COLORS.brand : "#CBD5E1"}`,
              background: isOn ? COLORS.brand : "#fff",
              color: isOn ? "#fff" : COLORS.textPrimary,
              transition: "background 120ms ease, color 120ms ease",
            }}
          >
            {mode === "multi" && isOn ? "✓ " : ""}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export default PersonaMCQ;
