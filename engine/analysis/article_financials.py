"""Phase 50 — article ₹-figure grounding.

The approval gate rejects most CRITICAL articles because their lede /
what-changed prose cites a ₹ figure that is NOT in the source article — the
synthetic primitive-cascade estimate ("₹17 Cr earnings upside", "₹100.5 Cr
supply-chain risk") leaks into the editorial prose as if it were an article
fact. The lede verifier (Phase 40.A) checks proper nouns but NOT monetary
figures, so an invented ₹ sails through and the whole critical gets demoted.

This module extracts the monetary figures actually present in a piece of text
(₹/Rs/INR/$/€ + crore/lakh/billion/million) and normalises them to comparable
tokens, so callers can answer: "is every ₹ figure in this lede grounded in the
article body?". A figure is grounded when the SAME number+unit appears in the
body (currency-symbol- and separator-insensitive: "Rs. 503 crores" grounds
"₹503 crore"). Percentages and bare numbers (dates, counts) are ignored — only
genuine money expressions are checked.
"""
from __future__ import annotations

import re

# number (with thousands separators / decimals) immediately followed by a
# money unit, optionally prefixed by a currency marker. The unit is REQUIRED so
# we don't treat "48% in 2026" or "503 employees" as money.
# Phase 50.1 — order matters: the "lakh crore"/"lakh cr" COMPOUND units must be
# tried before bare "lakh", else "₹6.5 Lakh Cr" matches only "lakh" (dropping
# the "Cr") and normalises to a different token than "₹6.5 lakh crore" — a false
# mismatch that made grounding fail and the strip mangle "₹6.5 Lakh Cr" into
# "Cr". "lakh crore" == "lakh cr" == one unit (= 100,000 crore).
_UNIT = r"(?:lakh\s+crores?|lakh\s+cr|lakh|crores?|cr|billion|bn|million|mn|trillion|tn)"
_MONEY_RE = re.compile(
    rf"(?:₹|rs\.?|inr|\$|usd|us\$|€|eur)?\s*"
    rf"(\d[\d,]*(?:\.\d+)?)\s*({_UNIT})\b",
    re.IGNORECASE,
)

_UNIT_CANON = {
    "lakh crore": "lakhcr", "lakh crores": "lakhcr", "lakh cr": "lakhcr",
    "lakh": "lakh", "crore": "cr", "crores": "cr", "cr": "cr",
    "billion": "billion", "bn": "billion", "million": "million",
    "mn": "million", "trillion": "trillion", "tn": "trillion",
}


def _canon_unit(raw: str) -> str:
    return _UNIT_CANON.get(re.sub(r"\s+", " ", raw.strip().lower()), raw.strip().lower())


def extract_money_tokens(text: str) -> set[str]:
    """Return normalised ``{number}{unit}`` money tokens present in ``text``.

    "₹17 Cr" → {"17cr"}; "Rs. 503 crores" → {"503cr"}; "$1.33 billion" →
    {"1.33billion"}; "₹6.5 Lakh Cr" → {"6.5lakhcr"}. Separator- and
    currency-symbol-insensitive so the same figure formatted differently in the
    lede vs the body still matches.
    """
    out: set[str] = set()
    for m in _MONEY_RE.finditer(text or ""):
        num = m.group(1).replace(",", "").rstrip(".")
        unit = _canon_unit(m.group(2))
        if num:
            out.add(f"{num}{unit}")
    return out


def extract_money_phrases(text: str, limit: int = 8) -> list[str]:
    """Return the raw money expressions present in ``text`` (human-readable).

    e.g. ["Rs. 503 crores", "$1.33 billion"]. Used to tell the lede LLM exactly
    which monetary figures it MAY cite (the only article-grounded ones), so it
    can write a rich grounded lede instead of falling to the dry template.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _MONEY_RE.finditer(text or ""):
        phrase = re.sub(r"\s+", " ", m.group(0).strip())
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


# Rough ₹-Crore magnitude per canonical unit (Indian numbering): 1 crore = 100
# lakh; 1 lakh crore = 100,000 crore; 1 billion ≈ 100 crore; 1 million ≈ 0.1
# crore. Used only for an order-of-magnitude sanity check (cascade estimate vs
# the article's own figures), never as a displayed number — so currency
# approximations are acceptable.
_TOKEN_CR = {"cr": 1.0, "lakh": 0.01, "lakhcr": 100000.0,
             "billion": 100.0, "million": 0.1, "trillion": 100000.0}


def max_article_cr(text: str) -> float:
    """Largest ₹-figure in ``text`` expressed in Crore (rough). 0.0 if none.

    Lets a caller detect when a tiny cascade estimate (e.g. ₹2 Cr) is being
    shown next to an article that quotes a vastly larger figure (₹1.5 lakh
    crore) — a gross magnitude error worth suppressing.
    """
    best = 0.0
    for m in _MONEY_RE.finditer(text or ""):
        try:
            num = float(m.group(1).replace(",", "").rstrip(".") or 0)
        except ValueError:
            continue
        mult = _TOKEN_CR.get(_canon_unit(m.group(2)))
        if mult:
            best = max(best, num * mult)
    return best


def money_grounded(claim_text: str, article_body: str) -> tuple[bool, list[str]]:
    """Is every ₹ figure in ``claim_text`` present in ``article_body``?

    Returns ``(grounded, ungrounded_tokens)``. When the body is empty we cannot
    verify, so we pass (callers treat a missing body as "can't check"). A claim
    with no money figures is trivially grounded.
    """
    claim_tokens = extract_money_tokens(claim_text)
    if not claim_tokens:
        return True, []
    if not (article_body or "").strip():
        return True, []
    body_tokens = extract_money_tokens(article_body)
    ungrounded = [t for t in claim_tokens if t not in body_tokens]
    return (len(ungrounded) == 0), ungrounded


def strip_money_clauses(text: str) -> str:
    """Remove money expressions (and a small surrounding clause) from ``text``.

    Used to salvage a headline whose only ungrounded element is a synthetic ₹
    figure — drop the ₹ clause, keep the rest. Conservative: removes the money
    token plus an immediately-attached "₹"/"Rs" prefix and a trailing connector.
    """
    if not text:
        return ""
    cleaned = re.sub(
        rf"[—\-,:;(]?\s*(?:₹|rs\.?|inr|\$|usd|us\$|€|eur)?\s*\d[\d,]*(?:\.\d+)?\s*{_UNIT}\b\)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -—,:;.")
    return cleaned


__all__ = ["extract_money_tokens", "money_grounded", "strip_money_clauses"]
