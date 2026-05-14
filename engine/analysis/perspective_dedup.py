"""Phase 25 W9 — cross-perspective dedup verifier.

The user's #7 ask: "make sure there is a huge differentiation among the
roles and no intelligence is repeated".

The CFO + CEO + ESG Analyst perspectives all derive from the same
Stage 10 ``deep_insight`` JSON, so they CAN end up paraphrasing each
other's headlines / impact bullets / framework citations. Phase 14
shipped polarity-aware prompts but didn't enforce post-hoc dedup.

This module computes pairwise n-gram (trigram by default) overlap
across the three perspectives' content fields. When any pair exceeds
``DEFAULT_OVERLAP_THRESHOLD`` (40%), the offending perspective is
flagged for regeneration with explicit instructions about which
content to differentiate against.

The verifier is purely DETECTOR + ADVISORY. The actual regeneration
is left to the caller (insight_generator) so the dedup logic stays
testable in isolation.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_OVERLAP_THRESHOLD = 0.40
NGRAM_SIZE = 3
PERSPECTIVE_NAMES = ("cfo", "ceo", "esg-analyst")

# Stop tokens — words too generic to count toward "overlap" (would
# inflate the score across any two business-prose blocks).
_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "this", "that", "is", "was", "are", "were",
    "be", "been", "being", "have", "has", "had", "as", "it", "its",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_overlap(text_a: str, text_b: str, n: int = NGRAM_SIZE) -> float:
    """Compute normalised n-gram overlap between two text blocks.

    Returns 0.0-1.0. Computed as |intersection| / min(|A|, |B|) so a
    short snippet fully contained in a longer one scores 1.0 (high
    duplication signal); two completely disjoint texts score 0.0.

    Stop words are stripped before tokenisation so generic prose
    ("the company has been ...") doesn't inflate the score.
    """
    a_grams = _ngrams(text_a, n)
    b_grams = _ngrams(text_b, n)
    if not a_grams or not b_grams:
        return 0.0
    intersection = (a_grams & b_grams).total()
    smaller = min(a_grams.total(), b_grams.total())
    if smaller == 0:
        return 0.0
    return min(1.0, intersection / smaller)


def verify_perspectives_distinct(
    perspectives: dict[str, Any],
    *,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    n: int = NGRAM_SIZE,
) -> list[dict[str, Any]]:
    """Run pairwise overlap check across CFO/CEO/Analyst perspectives.

    Returns a list of warning dicts (empty when all pairs pass):

        [
          {
            "perspective_a": "cfo",
            "perspective_b": "ceo",
            "overlap": 0.52,
            "threshold": 0.40,
            "field_compared": "headline+what_matters+key_risk",
            "regen_instruction": "CFO output overlaps 52% with CEO. Re-generate CFO
                                  focusing on financial-only axis: ₹ exposure, margin
                                  pressure, cost-of-capital. Drop strategic + framework
                                  language."
          },
        ]

    Caller (insight_generator) decides whether to act on warnings —
    re-prompt the offending generator with the regen_instruction, or
    accept the overlap and ship anyway. Pure detector here.
    """
    warnings: list[dict[str, Any]] = []
    blobs = {
        name: _build_perspective_blob(perspectives.get(name))
        for name in PERSPECTIVE_NAMES
    }
    pairs = [
        ("cfo", "ceo"), ("cfo", "esg-analyst"), ("ceo", "esg-analyst"),
    ]
    for a, b in pairs:
        if not blobs[a] or not blobs[b]:
            continue
        overlap = compute_overlap(blobs[a], blobs[b], n)
        if overlap >= threshold:
            warnings.append({
                "perspective_a": a,
                "perspective_b": b,
                "overlap": round(overlap, 3),
                "threshold": threshold,
                "field_compared": "headline+what_matters+key_risk",
                "regen_instruction": _regen_instruction(a, b, overlap),
            })
    return warnings


def all_perspectives_distinct(perspectives: dict[str, Any], **kwargs) -> bool:
    """Convenience wrapper — True iff no overlap warnings raised."""
    return len(verify_perspectives_distinct(perspectives, **kwargs)) == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z][a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase + alpha-numeric tokens, drop stop words."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if t not in _STOP_WORDS]


def _ngrams(text: str, n: int) -> Counter:
    """Return a Counter of n-grams over the tokenised text."""
    tokens = _tokenize(text)
    if len(tokens) < n:
        return Counter()
    return Counter(tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1))


def _build_perspective_blob(perspective: Any) -> str:
    """Concatenate the content-bearing fields of a perspective into a
    single string for n-gram comparison.

    Robust to dict-shaped (legacy CFO transform) and dataclass-shaped
    (Phase 4 CEO/Analyst generators) inputs."""
    if not perspective:
        return ""

    def _get(field: str) -> str:
        if isinstance(perspective, dict):
            v = perspective.get(field)
        else:
            v = getattr(perspective, field, None)
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return " ".join(str(x) for x in v if x)
        if isinstance(v, dict):
            return " ".join(str(x) for x in v.values() if isinstance(x, str))
        return str(v)

    parts = [
        _get("headline"),
        _get("what_matters"),
        _get("action"),
        _get("key_risk"),
        _get("board_paragraph"),         # CEO-only
        _get("ceo_strategic_stakes_paragraph"),  # W9 required CEO field
        _get("cfo_10_second_verdict"),   # W9 required CFO field
        _get("analyst_regulatory_checklist"),    # W9 required Analyst field
    ]
    return " ".join(p for p in parts if p)


def _regen_instruction(a: str, b: str, overlap: float) -> str:
    """Generate a perspective-specific regen instruction the caller can
    feed back to the LLM as a system-message addendum on retry."""
    pct = int(overlap * 100)
    if a == "cfo":
        focus = "financial axis only: ₹ exposure, margin pressure, cost-of-capital, ROI"
        drop = "strategic positioning, board-level concerns, framework deep-dive"
    elif a == "ceo":
        focus = "strategic axis: competitive position, brand, board narrative, stakeholder optics"
        drop = "₹ figures, framework section codes, compliance checklist"
    else:  # esg-analyst
        focus = "regulatory + framework deep-dive: section codes, deadlines, KPI table, audit trail"
        drop = "P&L narrative, board-level prose, stakeholder positioning"
    return (
        f"{a.upper()} output overlaps {pct}% with {b.upper()}. "
        f"Re-generate {a.upper()} focusing on {focus}. Drop {drop}."
    )
