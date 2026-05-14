"""Phase 4 §6.3 — email insight bullet verifier.

Every bullet in the outbound email must satisfy at least ONE of:

  1. Contains a number (₹, %, bps, count).
  2. Contains a date or comparable peer name.
  3. Contains a named-entity action verb (filed, sanctioned, acquired,
     divested, upgraded).

And must NOT match any reject pattern:
  * "Risk that [positive event] does not recur" — tautology
  * Sentences > 35 words — ramble
  * Hedging stacks (3+ hedge tokens in one sentence) — non-committal

Pure detector module — no LLM, no I/O. Returns BulletVerdict with a
``passed: bool`` and a ``reasons: list[str]`` for the audit log.

Email composer wires this in: when bullets fail, the composer either
regenerates them or drops them. Bullets that pass are accepted as-is.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


MAX_WORDS_PER_BULLET = 35
MAX_HEDGE_TOKENS = 2  # 3rd hedge token triggers fail

_NUMBER_RE = re.compile(
    r"(?:₹|Rs\.?|INR|\$|€|£)\s?\d|[\d,]+(?:\.\d+)?\s?(?:%|bps|Cr|crore|lakh|million|billion|tn|bn|mn)|\b\d{2,}\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s?\d{0,4}|"
    r"\bFY\s?\d{2,4}|"
    r"\bQ[1-4]\s?(?:FY)?\d{0,4}|"
    r"\b\d{4}-\d{2}-\d{2}|"
    r"\b\d{1,2}/\d{1,2}/\d{2,4}",
    re.IGNORECASE,
)
_ACTION_VERBS = frozenset({
    "filed", "sanctioned", "acquired", "divested", "upgraded", "downgraded",
    "fined", "penalised", "penalized", "ordered", "issued", "imposed",
    "won", "lost", "secured", "commissioned", "decommissioned", "merged",
    "spun off", "delisted", "listed", "raised", "redeemed", "refinanced",
    "approved", "rejected", "blocked", "halted", "resumed", "shut",
    "expanded", "exited", "launched", "discontinued",
})

_HEDGE_TOKENS = frozenset({
    "may", "might", "could", "possibly", "potentially", "perhaps",
    "likely", "unlikely", "presumably", "supposedly", "arguably",
    "appears", "seems",
})

# Tautology pattern from the plan: "Risk that [positive event] does not recur"
# (or "fails to recur", "may not be repeated", etc.)
_TAUTOLOGY_RES = (
    re.compile(r"\brisk\s+that\s+\w+(?:\s+\w+){0,4}\s+(?:does\s+not|won't|will\s+not|may\s+not|fails?\s+to)\s+recur\b", re.IGNORECASE),
    re.compile(r"\brisk\s+(?:of|that)\s+(?:non[-\s]?recurrence|not\s+repeating)\b", re.IGNORECASE),
)


# Common peer / company name shortcuts likely to appear in our universe.
# Not exhaustive — caller can extend via `peer_names` arg.
_DEFAULT_PEERS: frozenset[str] = frozenset({
    "tata power", "tata steel", "tata motors", "adani", "reliance", "infosys",
    "tcs", "wipro", "icici", "hdfc", "sbi", "kotak", "axis bank", "yes bank",
    "ntpc", "powergrid", "coal india", "bhel", "jsw energy", "jsw steel",
    "renew", "vedanta", "mahindra", "ultratech", "l&t", "larsen",
    "siemens", "abb", "schneider", "ge", "honeywell",
    "bp", "shell", "total", "exxon", "chevron",
    "blackrock", "vanguard", "calpers", "nbim",
    "msci", "sustainalytics", "iss", "glass lewis",
    "sebi", "rbi", "moefcc", "ngt", "cpcb", "sec", "fca", "esma",
    "brsr", "gri", "tcfd", "csrd", "esrs", "sasb", "cdp", "issb",
})


@dataclass
class BulletVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    word_count: int = 0
    hedge_count: int = 0
    has_number: bool = False
    has_date: bool = False
    has_peer: bool = False
    has_action_verb: bool = False


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _has_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(text))


def _has_date(text: str) -> bool:
    return bool(_DATE_RE.search(text))


def _has_peer(text: str, peers: frozenset[str]) -> bool:
    lower = text.lower()
    return any(name in lower for name in peers)


def _count_hedges(text: str) -> int:
    lower = text.lower()
    n = 0
    for token in _HEDGE_TOKENS:
        n += len(re.findall(rf"\b{re.escape(token)}\b", lower))
    return n


def _has_action_verb(text: str) -> bool:
    lower = text.lower()
    for verb in _ACTION_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return True
    return False


def _is_tautology(text: str) -> bool:
    return any(p.search(text) for p in _TAUTOLOGY_RES)


def verify_bullet(
    text: str,
    peer_names: frozenset[str] | None = None,
) -> BulletVerdict:
    """Apply the §6.3 quality gate to a single email-bullet string.

    `peer_names` extends the default peer dictionary if the caller has
    article-specific context (e.g. competitor list from the ontology).
    """
    peers = (peer_names or frozenset()) | _DEFAULT_PEERS

    if not text or not text.strip():
        return BulletVerdict(passed=False, reasons=["empty bullet"])

    text = text.strip()
    word_count = _word_count(text)
    hedge_count = _count_hedges(text)
    has_number = _has_number(text)
    has_date = _has_date(text)
    has_peer = _has_peer(text, peers)
    has_action_verb = _has_action_verb(text)

    reasons: list[str] = []

    # Hard fails
    if _is_tautology(text):
        reasons.append("tautology: 'risk that [event] does not recur'")
    if word_count > MAX_WORDS_PER_BULLET:
        reasons.append(
            f"too long: {word_count} words (cap {MAX_WORDS_PER_BULLET})"
        )
    if hedge_count > MAX_HEDGE_TOKENS:
        reasons.append(
            f"hedging stack: {hedge_count} hedge tokens (cap {MAX_HEDGE_TOKENS})"
        )

    # Must satisfy at least one positive criterion
    has_concrete = has_number or has_date or has_peer or has_action_verb
    if not has_concrete:
        reasons.append(
            "no concrete signal: needs number, date, peer name, or action verb"
        )

    passed = len(reasons) == 0
    return BulletVerdict(
        passed=passed,
        reasons=reasons,
        word_count=word_count,
        hedge_count=hedge_count,
        has_number=has_number,
        has_date=has_date,
        has_peer=has_peer,
        has_action_verb=has_action_verb,
    )


def verify_bullets(
    bullets: Iterable[str],
    peer_names: frozenset[str] | None = None,
) -> list[BulletVerdict]:
    """Verify a list of bullets. Returns one verdict per input bullet
    (preserves order). Caller is responsible for deciding what to do
    with failures (regenerate / drop / hold for review)."""
    return [verify_bullet(b, peer_names=peer_names) for b in bullets]


# ---------------------------------------------------------------------------
# Subject-line verifier (§6.2)
# ---------------------------------------------------------------------------


SUBJECT_MAX_LEN = 90  # iPhone preview cap
_COMPETITIVE_VERBS = frozenset({
    "surges", "compresses", "triggers", "overtakes", "lags", "fines",
    "sanctions", "wins", "loses", "leads", "trails", "outpaces",
    "blocks", "halts", "shuts", "issues", "files", "approves",
})
_PROVENANCE_NOISE_RE = re.compile(
    r"\((?:engine\s+estimate|from\s+article)\)",
    re.IGNORECASE,
)


@dataclass
class SubjectVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    char_count: int = 0
    has_rupee: bool = False
    has_competitive_verb: bool = False


def verify_subject(subject: str) -> SubjectVerdict:
    """Apply the §6.2 quality gate to a subject string.

    Pass conditions: ≤90 chars, no provenance noise, AND (₹ figure OR
    a competitive verb from the curated list).
    """
    if not subject:
        return SubjectVerdict(passed=False, reasons=["empty subject"])

    subject = subject.strip()
    char_count = len(subject)
    has_rupee = "₹" in subject or bool(
        re.search(r"\b(?:Rs\.?|INR)\b", subject, re.IGNORECASE),
    )
    lower = subject.lower()
    has_competitive_verb = any(
        re.search(rf"\b{re.escape(v)}\b", lower) for v in _COMPETITIVE_VERBS
    )

    reasons: list[str] = []
    if char_count > SUBJECT_MAX_LEN:
        reasons.append(f"too long: {char_count} chars (cap {SUBJECT_MAX_LEN})")
    if _PROVENANCE_NOISE_RE.search(subject):
        reasons.append("contains provenance noise '(engine estimate)' / '(from article)'")
    if not (has_rupee or has_competitive_verb):
        reasons.append(
            "missing concrete hook: needs ₹ figure OR competitive verb "
            "(surges, compresses, triggers, fines, wins, ...)"
        )

    passed = len(reasons) == 0
    return SubjectVerdict(
        passed=passed,
        reasons=reasons,
        char_count=char_count,
        has_rupee=has_rupee,
        has_competitive_verb=has_competitive_verb,
    )
