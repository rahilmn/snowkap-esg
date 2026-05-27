"""Phase 38 — Editorial tone guardrails.

The single source of truth for what makes Snowkap's prose read as
*serious editorial* rather than *AI-generated marketing copy*.

Derived from `content rule and structure.docx` (sections A, C, E, F, N, O).
The rules originated in a B2B sales-outreach product but the editorial
discipline they codify is directly transferrable to Snowkap's CFO/CEO
newsletter audience — the docx audit found Snowkap's current Stage-10
prose violates every single one.

This module is intentionally pure-Python with no I/O so it can be:
  * appended into LLM system prompts (Stage 10 deep insight, Stage 12
    recommendations, subject-line generation)
  * called from the post-render HTML scrubber as a regex / substring
    detection layer
  * unit-tested cheaply

Three layers of enforcement, each catching what the previous missed:

    LLM compose (Opus 4.6 / gpt-4.1)
        ↓ prompt-level guardrails via `apply_to_system_prompt(prompt)`
        ↓ catches ~85% of violations at generation time
    Scanner
        ↓ `scan_for_violations(text)` surfaces residual hits
        ↓ ~12%
    Post-render scrubber
        ↓ contextual word/phrase substitution
        ↓ residual ~3%

For a newsletter going to 100s of CFOs/CEOs per day, the difference
between 10% violation rate and <1% is the difference between "looks
polished" and "reads AI-generated" at first glance.
"""

from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
# A — Hemingway Set (writing style)
# ---------------------------------------------------------------------------
# Single sentence each. Active voice. Short common words. Concrete nouns.
# 2-4 sentences per paragraph; 8-14 words per sentence average; max 20.
# Max 2 commas per sentence. No em-dashes. No semicolons.
# No headers/subheadings/bold labels in body (framework should be invisible).

HEMINGWAY_RULES = (
    "Use short common words.",
    "Use active voice. Subject does the action.",
    "Max 2 commas per sentence. No em-dashes. No semicolons.",
    "Average 8-14 words per sentence. Hard ceiling 20.",
    "2-4 sentences per paragraph.",
    "Concrete nouns over abstract.",
    "Cut adjectives: no 'truly', 'significantly', 'incredibly', 'really', 'just', 'absolutely', 'completely'.",
    "No headers, subheadings, or bold labels inside body prose.",
    "Lead with the fact, not the framing.",
)


# ---------------------------------------------------------------------------
# C — AI Tell-Tale Word Ban (single words)
# ---------------------------------------------------------------------------
# These words signal "ChatGPT wrote this" to a sophisticated reader within
# 2-3 sentences. Buyer pattern-matches and stops reading.

BANNED_WORDS = frozenset({
    "delve",
    "leverage",
    "leveraging",
    "leverages",
    "leveraged",
    "tapestry",
    "nuanced",
    "intricate",
    "elevate",
    "elevates",
    "elevated",
    "elevating",
    "robust",
    "cutting-edge",
    "holistic",
    "holistically",
    "synergy",
    "synergies",
    "unleash",
    "unleashing",
    "empower",
    "empowering",
    "empowered",
    "bespoke",
    "seamless",
    "seamlessly",
    "meticulously",
    "meticulous",
    "myriad",
    "plethora",
    "paramount",
    "vibrant",
    "embark",
})


# ---------------------------------------------------------------------------
# C — AI Tell-Tale Phrase Ban (multi-word)
# ---------------------------------------------------------------------------
# These phrases appear in ~80% of LLM-emitted analytical prose. They
# signal template-feel even when individual words are fine.

BANNED_PHRASES = (
    "navigating the landscape",
    "navigating the complex",
    "in the realm of",
    "at the heart of",
    "in today's",
    "in the world of",
    "it's important to note",
    "it is important to note",
    "important to note that",
    "dive deep",
    "deep dive",
    "game-changer",
    "game changer",
    "paradigm shift",
    "at the intersection of",
    "speaks volumes",
    "a testament to",
    "stands as a testament",
    "the world of",
    "in the ever-evolving",
    "ever-changing landscape",
    "ever-evolving landscape",
    "uncharted territory",
    "tip of the iceberg",
    "at its core",
    "lies at the heart",
)


# ---------------------------------------------------------------------------
# C — AI Tell-Tale Openers
# ---------------------------------------------------------------------------
# Sentences that START with these constructions are almost always
# AI-generated framing. Strip them at the scrubber layer.

BANNED_OPENERS = (
    "in today's",
    "in the realm of",
    "in an era",
    "in a world",
    "in the ever-",
    "navigating the",
    "as we ",
    "it's worth noting",
    "it is worth noting",
    "moreover,",
    "furthermore,",
    "additionally,",
    "in conclusion,",
)


# ---------------------------------------------------------------------------
# E — Corporate Jargon → Plain English Substitutes
# ---------------------------------------------------------------------------
# Direct one-to-one swaps. The scrubber applies these contextually
# (preserves capitalisation, respects word boundaries).

CORPORATE_JARGON_MAP = {
    "utilize": "use",
    "utilizes": "uses",
    "utilized": "used",
    "utilizing": "using",
    "utilization": "use",
    "facilitate": "help",
    "facilitates": "helps",
    "facilitated": "helped",
    "facilitating": "helping",
    "demonstrate": "show",
    "demonstrates": "shows",
    "demonstrated": "showed",
    "demonstrating": "showing",
    "commence": "start",
    "commences": "starts",
    "commenced": "started",
    "commencing": "starting",
    "regarding": "about",
    "in regards to": "about",
    "in regard to": "about",
    "with regard to": "about",
    "with regards to": "about",
    "prior to": "before",
    "subsequent to": "after",
    "in order to": "to",
    "in the event that": "if",
    "due to the fact that": "because",
    "for the purpose of": "for",
    "with the intention of": "to",
    "methodology": "method",
    "methodologies": "methods",
    "optimal": "best",
    "optimally": "best",
    "substantial": "large",
    "substantially": "much",
    "endeavor": "try",
    "endeavour": "try",
    "ascertain": "find out",
    "terminate": "end",
    "terminated": "ended",
    "initiate": "start",
    "initiated": "started",
    "leverage": "use",
    "leverages": "uses",
    "leveraged": "used",
    "leveraging": "using",
}


# ---------------------------------------------------------------------------
# F — Hedging / Filler Bans
# ---------------------------------------------------------------------------
# Words that soften claims, signal uncertainty, or pad sentence count.
# CFOs / CEOs treat hedge-laden prose as low-confidence and discount it.

HEDGING_FILLER = (
    "perhaps",
    "possibly",
    "potentially",
    "somewhat",
    "in fact",
    "actually",
    "basically",
    "essentially",
    "at the end of the day",
    "needless to say",
    "obviously",
    "clearly",
    "of course",
    "rather",
    "quite",
    "very",
    "extremely",
    "tend to",
    "tends to",
    "may erode",
    "could potentially",
    "might potentially",
    "it seems that",
    "it would appear",
    "to some extent",
    "in some way",
    "various",
    "a number of",
    "a variety of",
)


# ---------------------------------------------------------------------------
# Sentence-shape rules
# ---------------------------------------------------------------------------

MAX_SENTENCE_WORDS = 22  # docx says hard ceiling 20; allow 22 for technical sentences
MAX_COMMAS_PER_SENTENCE = 2
MAX_PARAGRAPH_SENTENCES = 4


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


_TONE_GUARDRAILS_BLOCK = """
EDITORIAL TONE — STRICT (Snowkap content rule, all violations stripped post-generation)

You are writing for CFOs / CEOs of top-1000 Indian listed companies. They read
Mint, FT, Bloomberg daily. They pattern-match "AI marketing copy" within 2-3
sentences and stop reading. Follow these rules without exception.

VOICE — Hemingway:
- Short common words. Active voice. Subject does the action.
- 8-14 words per sentence average. Hard ceiling 22.
- Max 2 commas per sentence. No em-dashes. No semicolons.
- 2-4 sentences per paragraph.
- Concrete nouns over abstract.
- No headers, subheadings, or bold labels inside body prose.
- Lead with the fact, not the framing.

BANNED WORDS (do not use under any circumstance):
delve, leverage, tapestry, nuanced, intricate, elevate, robust, cutting-edge,
holistic, synergy, unleash, empower, bespoke, seamless, meticulously, myriad,
plethora, paramount, vibrant, embark.

BANNED PHRASES (do not use):
"navigating the landscape", "in the realm of", "at the heart of",
"in today's", "in the world of", "it's important to note", "dive deep",
"game-changer", "paradigm shift", "at the intersection of", "speaks volumes",
"a testament to", "in the ever-evolving", "ever-changing landscape",
"uncharted territory", "tip of the iceberg".

BANNED OPENERS (do not start a sentence with):
"In today's", "In the realm of", "In an era", "In a world",
"Navigating the", "As we", "Moreover,", "Furthermore,", "Additionally,".

BANNED HEDGING (cut every instance):
perhaps, possibly, potentially, somewhat, in fact, actually, basically,
essentially, at the end of the day, needless to say, obviously, clearly,
of course, rather, quite, very, extremely, may erode, could potentially.

CORPORATE JARGON — USE PLAIN ENGLISH:
- "utilize" → "use"
- "facilitate" → "help"
- "demonstrate" → "show"
- "commence" → "start"
- "regarding" → "about"
- "prior to" → "before"
- "in order to" → "to"
- "methodology" → "method"
- "optimal" → "best"
- "substantial" → "large"

CONCRETE NUMBERS — REQUIRED:
- Lead with the ₹ figure when material. Never bury it.
- Never invent statistics. Every claim traces to a metric or article fact.
- No bracketed placeholders. No "[insert figure]".

The reader is a sophisticated business buyer. They value factual specificity
over breathless adjectives. Write like Mint editorial, not LinkedIn.
""".strip()


def apply_to_system_prompt(prompt: str) -> str:
    """Append the canonical tone-guardrails block to an LLM system prompt.

    Use at every LLM call site that produces user-facing prose:
        - engine/analysis/insight_generator.py (Stage 10 deep insight)
        - engine/analysis/recommendation_engine.py (Stage 12 recs)
        - engine/output/subject_line.py (subject lines)

    Idempotent — appending twice produces the same single trailing block
    (skips re-appending when the marker is already present).
    """
    if "EDITORIAL TONE — STRICT (Snowkap content rule" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + _TONE_GUARDRAILS_BLOCK


def apply_subject_line_guardrails(prompt: str) -> str:
    """A smaller subset for subject-line prompts (≤ 90 chars; cost matters less).

    Subject lines don't have the room to violate every rule — the LLM only
    needs the banned-opener + banned-word subset to write a clean subject.
    """
    if "SUBJECT LINE GUARDRAILS" in prompt:
        return prompt
    short = (
        "SUBJECT LINE GUARDRAILS — STRICT:\n"
        "- Max 1 comma. No em-dash. No semicolon.\n"
        "- Banned openers: 'In the realm of', 'In today's', 'Navigating the'.\n"
        "- Banned words: leverage, robust, holistic, elevate, seamless, "
        "myriad, paramount, intricate, nuanced.\n"
        "- Lead with the ₹ figure or the regulator/event. Concrete first.\n"
        "- No emoji. No quotation marks. No 'Snowkap:' prefix."
    )
    return prompt.rstrip() + "\n\n" + short


# ---------------------------------------------------------------------------
# Scanner — used by the post-render scrubber and the smoke harness
# ---------------------------------------------------------------------------


def _word_boundary_pattern(words: Iterable[str]) -> re.Pattern[str]:
    """Compile a single case-insensitive regex matching any of `words`
    on word boundaries. Used by both the scanner and the substitutor."""
    parts = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    if not parts:
        # Match-nothing pattern keeps callers simple.
        return re.compile(r"$.^", re.IGNORECASE)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    """Compile a case-insensitive regex matching any of the multi-word phrases.
    Unlike `_word_boundary_pattern`, phrases may have internal spaces /
    hyphens, so we don't constrain trailing boundaries."""
    parts = sorted({re.escape(p) for p in phrases}, key=len, reverse=True)
    if not parts:
        return re.compile(r"$.^", re.IGNORECASE)
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


# Compiled once at import time. Cheap to reuse on every newsletter render.
_BANNED_WORD_RE = _word_boundary_pattern(BANNED_WORDS)
_BANNED_PHRASE_RE = _phrase_pattern(BANNED_PHRASES)
_HEDGING_RE = _phrase_pattern(HEDGING_FILLER)
_JARGON_RE = _word_boundary_pattern(CORPORATE_JARGON_MAP.keys())


def scan_for_violations(text: str) -> list[dict]:
    """Return a list of violation dicts found in `text`.

    Each entry: `{kind, hit, start, end}` where kind ∈
    {banned_word, banned_phrase, hedging, jargon, em_dash, banned_opener}.

    Pure read-only — used by the scrubber to decide what to rewrite, by
    the test harness to assert clean output, and by the smoke endpoint
    in admin for operator visibility.
    """
    if not text:
        return []
    hits: list[dict] = []

    for m in _BANNED_WORD_RE.finditer(text):
        hits.append({"kind": "banned_word", "hit": m.group(0),
                     "start": m.start(), "end": m.end()})

    for m in _BANNED_PHRASE_RE.finditer(text):
        hits.append({"kind": "banned_phrase", "hit": m.group(0),
                     "start": m.start(), "end": m.end()})

    for m in _HEDGING_RE.finditer(text):
        hits.append({"kind": "hedging", "hit": m.group(0),
                     "start": m.start(), "end": m.end()})

    for m in _JARGON_RE.finditer(text):
        hits.append({"kind": "jargon", "hit": m.group(0),
                     "start": m.start(), "end": m.end()})

    # Em-dash sweep (— and -- both)
    for m in re.finditer(r"—|--", text):
        hits.append({"kind": "em_dash", "hit": m.group(0),
                     "start": m.start(), "end": m.end()})

    # Banned-opener: a sentence whose first 30 chars start with any banned opener.
    # Sentence-split is best-effort via period/exclaim/question + space.
    for sent_match in re.finditer(r"(?:^|(?<=[.!?]\s))([A-Z][^.!?]{1,120}[.!?])", text):
        sentence = sent_match.group(1)
        s_lower = sentence.lstrip().lower()
        for opener in BANNED_OPENERS:
            if s_lower.startswith(opener):
                hits.append({"kind": "banned_opener", "hit": opener,
                             "start": sent_match.start(),
                             "end": sent_match.start() + len(opener)})
                break

    return hits


__all__ = [
    "HEMINGWAY_RULES",
    "BANNED_WORDS",
    "BANNED_PHRASES",
    "BANNED_OPENERS",
    "HEDGING_FILLER",
    "CORPORATE_JARGON_MAP",
    "MAX_SENTENCE_WORDS",
    "MAX_COMMAS_PER_SENTENCE",
    "MAX_PARAGRAPH_SENTENCES",
    "apply_to_system_prompt",
    "apply_subject_line_guardrails",
    "scan_for_violations",
]
