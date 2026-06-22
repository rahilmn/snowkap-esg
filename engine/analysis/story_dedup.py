"""Story-level de-duplication for the CRITICAL deck tier (Phase 54).

The deck's critical tier shows the N most deck-worthy articles. Without a
story guard, N near-identical articles about the SAME underlying event — e.g.
three outlets covering one ₹83 crore fraud bail hearing — can fill all N
critical slots, crowding genuinely DISTINCT material stories (a separate
₹661 crore case at the same company) down into the light / quick-read tier.

This module clusters CRITICAL candidates by STORY so each slot is a different
event. A candidate is "the same story" as an already-published critical when:

  * they share a money amount (₹83cr ≈ "Rs 83-crore" ≈ "₹83 crore") AND their
    titles share at least a little vocabulary, OR
  * their titles overlap heavily on their own (token Jaccard ≥ threshold).

Same-story candidates are DEMOTED to the light tier (they still appear as
quick-reads) rather than promoted to critical — nothing is dropped, the deck
just stops showing the same case three times in its three headline slots.

This is a text-similarity heuristic (a sibling of ingestion's `SemanticDedup`,
which runs a stricter title+summary Jaccard at fetch time but is money-blind
and so lets the same-case trio through). It is NOT ESG domain knowledge — no
weights/mappings that belong in the ontology. Thresholds are env-tunable.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from engine.ingestion.dedup import jaccard_similarity

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Generic news / legal / business vocabulary that recurs across MANY of a
# company's articles and so carries no story-distinguishing signal — it must
# NOT, on its own, anchor a "same case" match. Company-name tokens are stripped
# separately (see `_name_tokens`). Deliberately bounded — over-stripping would
# make DISTINCT stories look alike and over-merge the tier; under-stripping lets
# a topical word (e.g. "module", "regulatory") falsely merge two ₹-equal events.
_GENERIC = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "over",
    "after", "amid", "says", "said", "report", "reports", "update", "news",
    "case", "cases", "fraud", "scam", "probe", "court", "bank", "limited",
    "ltd", "inr", "crore", "lakh", "company", "firm", "india", "indian",
    "shares", "share", "stock", "price",
    # recurring business/topic words — distinct events often share one of these
    # alongside a coincidental ₹ figure, which must not read as "same case".
    "order", "orders", "supply", "module", "modules", "deal", "stake",
    "results", "plant", "regulatory", "compliance", "expansion", "project",
})

# A money amount = a number anchored to a crore/lakh/cr/bn/mn SCALE word
# (optionally prefixed by ₹/Rs). The scale word is required on purpose: a bare
# "Rs 13" in "Rs 13 accused" / "Rs 13% penalty" / "800 MW" is a count, a
# percentage or a unit — NOT a rupee figure — and capturing it as money was a
# false-positive that wrongly merged distinct stories. ESG amounts in real
# headlines always carry the scale word ("₹83 crore", "Rs 661 cr"). Two
# spellings of one figure ("₹83 crore", "Rs 83-crore") normalise to "83".
_MONEY_RE = re.compile(
    r"(?:₹|rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)\s*-?\s*"
    r"(?:crore|cr|lakh|lakhs|billion|million|bn|mn)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StorySignature:
    """The fingerprint of an article's underlying story: distinguishing title
    tokens + the set of money amounts mentioned in the title."""
    tokens: frozenset[str]
    money: frozenset[str]

    def is_empty(self) -> bool:
        return not self.tokens and not self.money


def _name_tokens(*names: str | None) -> frozenset[str]:
    out: set[str] = set()
    for n in names:
        if n:
            out.update(_TOKEN_RE.findall(n.lower()))
    return frozenset(out)


def _money_amounts(text: str) -> frozenset[str]:
    out: set[str] = set()
    for m in _MONEY_RE.finditer(text or ""):
        num = m.group(1)
        if not num:
            continue
        norm = num.replace(",", "")
        try:  # collapse "83.0" → "83" so spellings normalise together
            f = float(norm)
            norm = str(int(f)) if f.is_integer() else str(f)
        except ValueError:
            pass
        out.add(norm)
    return frozenset(out)


def story_signature(title: str, *company_names: str | None) -> StorySignature:
    """Build a StorySignature from an article title, stripping company-name
    and generic news tokens so only story-distinguishing vocabulary remains."""
    name = _name_tokens(*company_names)
    toks = {
        t for t in _TOKEN_RE.findall((title or "").lower())
        if len(t) >= 3 and t not in _GENERIC and t not in name
    }
    return StorySignature(tokens=frozenset(toks), money=_money_amounts(title or ""))


def _jaccard_threshold() -> float:
    """Read SNOWKAP_STORY_JACCARD, falling back to 0.45 on a malformed value.

    same_story runs on the deck-build hot path; a typo'd env value must not
    crash the whole build with an unhandled ValueError (it would 500 onboarding
    and the weekly refresh). We log the bad value (per the no-silent-except
    rule) and use the default rather than failing closed."""
    raw = os.environ.get("SNOWKAP_STORY_JACCARD", "0.45")
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("invalid SNOWKAP_STORY_JACCARD=%r; using default 0.45", raw)
        return 0.45


def same_story(
    a: StorySignature,
    b: StorySignature,
    *,
    jaccard_threshold: float | None = None,
) -> bool:
    """True when `a` and `b` cover the same underlying event/case.

    Two ways to qualify:

    * **Shared ₹ figure + a shared distinguishing word.** The same money
      amount (e.g. ₹83cr in both) is a strong same-case anchor, but to avoid
      two genuinely distinct ₹X events colliding on a coincidental figure we
      require at least one shared NON-amount token too (so "₹500cr solar
      order" and "₹500cr penalty" stay separate, while "₹83cr CREST bail" and
      "₹83cr CREST chargesheet" merge on the shared word "crest").
    * **High stand-alone title overlap** (token Jaccard ≥ threshold), which
      catches money-free retellings ("…dismisses bail plea of CREST director").
    """
    if a.is_empty() or b.is_empty():
        return False
    jac_thr = jaccard_threshold if jaccard_threshold is not None else _jaccard_threshold()

    if a.money and b.money and (a.money & b.money):
        shared_non_money = (a.tokens & b.tokens) - a.money - b.money
        if shared_non_money:
            return True
    # Pure title-overlap branch: require BOTH a high Jaccard AND at least two
    # shared distinguishing words, so two thin titles that collapse to a single
    # common token (e.g. "Q3 results" / "Q3 outlook") aren't merged on it.
    shared = a.tokens & b.tokens
    return len(shared) >= 2 and jaccard_similarity(a.tokens, b.tokens) >= jac_thr


def merge_signatures(a: StorySignature, b: StorySignature) -> StorySignature:
    """Union two same-story signatures into one cluster fingerprint.

    Used to accumulate a published critical's "cluster" as duplicates are
    demoted into it (single-linkage): a later candidate then matches the
    cluster via ANY member's identifying features, so the order in which the
    cluster's articles happen to rank can't let a straggler slip the guard
    (e.g. a money-free CREST headline ranking ahead of the ₹83cr one)."""
    return StorySignature(tokens=a.tokens | b.tokens, money=a.money | b.money)


def is_story_dedup_enabled() -> bool:
    """Critical-tier story dedup is ON by default; set SNOWKAP_DECK_STORY_DEDUP=0
    to fall back to the pre-Phase-54 behaviour (same story can fill all slots)."""
    return os.environ.get("SNOWKAP_DECK_STORY_DEDUP", "1").strip().lower() not in {
        "0", "false", "no", "off", "",
    }
