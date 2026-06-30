/**
 * Phase 56.N — a glossary term with a hover/tap tooltip, plus helpers to
 * (a) wrap a known term inside a headline with that tooltip, and (b) highlight
 * risk words (e.g. "penalties") inside a paragraph.
 *
 * Curated per-article: analysis.glossary = { term, text }, and
 * analysis.highlight_terms = ["penalties", ...].
 */
import { useState, type ReactNode } from "react";

export interface Glossary {
  term?: string;
  text?: string;
}

export function GlossaryTerm({ term, text }: { term: string; text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        // tap (mobile) toggles; preventDefault stops a wrapping <a>/swipe.
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen((v) => !v); }}
        title={text}
        style={{
          borderBottom: "1.5px dotted currentColor",
          cursor: "help", whiteSpace: "nowrap",
        }}
      >
        {term}
      </span>
      {open && (
        <span
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute", left: 0, top: "100%", marginTop: 7, zIndex: 80,
            width: 248, maxWidth: "78vw", padding: "9px 11px",
            background: "#0f1115", color: "#ffffff",
            borderRadius: 9, fontSize: 12, lineHeight: 1.45, fontWeight: 400,
            letterSpacing: "normal", textTransform: "none",
            boxShadow: "0 10px 28px rgba(0,0,0,0.28)", whiteSpace: "normal",
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** Wrap the first occurrence of glossary.term inside `text` with a tooltip. */
export function withGlossary(text: string, glossary?: Glossary | null): ReactNode {
  const term = (glossary?.term || "").trim();
  const desc = (glossary?.text || "").trim();
  if (!term || !desc || !text) return text;
  const idx = text.toLowerCase().indexOf(term.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <GlossaryTerm term={text.slice(idx, idx + term.length)} text={desc} />
      {text.slice(idx + term.length)}
    </>
  );
}

/** Bold + colour any of `terms` found in `text` (case-insensitive, whole words). */
export function highlightTerms(text: string, terms?: string[] | null): ReactNode {
  const clean = (terms || []).map((t) => (t || "").trim()).filter(Boolean);
  if (!clean.length || !text) return text;
  // longest first so "penalties" wins over "penalty"
  clean.sort((a, b) => b.length - a.length);
  const escaped = clean.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`\\b(${escaped.join("|")})\\b`, "gi");
  const lowers = new Set(clean.map((t) => t.toLowerCase()));
  const parts = text.split(re);
  return parts.map((p, i) =>
    lowers.has(p.toLowerCase())
      ? <strong key={i} style={{ color: "#b3402a", fontWeight: 700 }}>{p}</strong>
      : <span key={i}>{p}</span>,
  );
}
