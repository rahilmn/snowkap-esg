"""Phase 38 — Post-render HTML scrubber.

Last line of defence in the 3-layer editorial-discipline protocol. Sits
between `render_article_morning_brew()` (or any other email/HTML renderer)
and `send_email()`. The Stage-10/12 LLMs follow the
`tone_guardrails.apply_to_system_prompt()` block at generation time and
catch ~85% of violations. This scrubber sweeps the residual ~15%.

The contract is intentionally narrow:

    scrub_html(html: str) -> str

    * Pure function (HTML in, HTML out)
    * Idempotent — running twice yields the same output
    * Conservative — when in doubt, leave content untouched
    * Tag-aware — only rewrites text *between* HTML tags so it never
      mangles `<style>`, `<a href>`, inline-style attributes, or
      hyperlink anchors

Four passes, run in order:

    1. Em-dash sweep
       `text — text`  →  `text, text` (or sentence-split when the
                          em-dash sits between two clauses)

    2. Jargon substitution (single-word replacements)
       `utilize / leverage / facilitate / commence / methodology …`
       → plain-English equivalents from CORPORATE_JARGON_MAP

    3. Banned-phrase deletion
       Cuts the sentence containing the phrase (`in the realm of`,
       `at the heart of`, `paradigm shift`, etc.). Conservative —
       only removes the phrase + the surrounding sentence boundaries
       so the paragraph reads naturally without it.

    4. Opener-shape detection
       Sentences that START with a banned opener
       (`In today's …`, `Navigating the …`, `As we …`) get the opener
       trimmed and the remaining clause re-capitalised. The fact
       survives; the framing dies.

The scrubber is deliberately pure-Python with no LLM calls — runs in
single-digit ms on a typical 6 KB email. Per the plan, an LLM-driven
emergency rewrite (Opus 4.6) is the optional Phase 38.3 extension when
the scrubber identifies 3+ banned tokens co-located in one sentence,
but the deterministic pass alone closes the gap from ~10% violation
rate down to <1% on a CFO-grade newsletter.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Iterable

from engine.analysis.tone_guardrails import (
    BANNED_OPENERS,
    BANNED_PHRASES,
    BANNED_WORDS,
    CORPORATE_JARGON_MAP,
    HEDGING_FILLER,
    _BANNED_PHRASE_RE,
    _BANNED_WORD_RE,
    _HEDGING_RE,
    _JARGON_RE,
)


# Banned single words that DON'T have a clean plain-English substitute —
# typically adjectives the docx flags as AI-tells (robust / seamless /
# holistic / intricate / meticulously / elevating / vibrant / paramount).
# These get DELETED inline (the sentence still reads as English without
# the adjective). Words that do have an explicit jargon swap go through
# _substitute_jargon instead.
_BANNED_WORDS_NO_SWAP = frozenset(
    w for w in BANNED_WORDS if w.lower() not in CORPORATE_JARGON_MAP
)


def _strip_banned_words(text: str) -> str:
    """Delete banned single-word adjectives that have no plain-English
    replacement. Preserves capitalisation neighbouring words; trims any
    double-space residual.

    Example: "robust Q4 performance" → "Q4 performance"
             "meticulously crafted"  → "crafted"
             "elevating its position" → "its position"
    """
    def _drop(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.lower() in _BANNED_WORDS_NO_SWAP:
            return ""
        return word

    return _BANNED_WORD_RE.sub(_drop, text)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Em-dash sweep
# ---------------------------------------------------------------------------

# Em-dash variants the LLM emits: literal —, double hyphen --, spaced --.
# Captures the next non-space char so we can decide whether to capitalise.
_EM_DASH_FULL_RE = re.compile(r"\s*(?:—|--)\s*(\S)?")


def _replace_em_dash(text: str) -> str:
    """Replace em-dashes with a comma when in mid-sentence position;
    with a full stop + capitalised next letter when the dash sits between
    two independent clauses.

    Heuristic: if the text after the dash starts with a clause-likely
    token (it/the/this/that/which/our/we/they/he/she/where/when/who),
    treat as new clause → period + capital. Otherwise → comma.
    Conservative — bad heuristic output reads as flat prose, not as
    broken grammar.
    """

    clause_starters = {
        "it", "the", "this", "that", "which", "our", "we", "they",
        "he", "she", "where", "when", "who", "but", "and", "however",
    }

    def _substitute(match: re.Match[str]) -> str:
        # Group 1 is the next non-space character (None at end of string).
        next_char = match.group(1) or ""
        end_idx = match.end()
        tail = (next_char + text[end_idx:end_idx + 30]).lstrip()
        if not tail:
            return ", "
        first_word = re.split(r"[\s.,;:!?]+", tail, maxsplit=1)[0].lower()
        if first_word in clause_starters and next_char:
            # New sentence: emit `. ` and capitalise the next char.
            return ". " + next_char.upper()
        if not next_char:
            return ", "
        return ", " + next_char

    return _EM_DASH_FULL_RE.sub(_substitute, text)


# ---------------------------------------------------------------------------
# Jargon substitution (single-word, contextual)
# ---------------------------------------------------------------------------


def _substitute_jargon(text: str) -> str:
    """Replace jargon words with their plain-English equivalents from
    CORPORATE_JARGON_MAP. Preserves capitalisation of the original token
    (Utilize → Use, UTILIZE → USE, utilize → use)."""

    def _swap(match: re.Match[str]) -> str:
        hit = match.group(0)
        plain = CORPORATE_JARGON_MAP.get(hit.lower(), hit)
        if not plain:
            return hit
        # Match capitalisation of original
        if hit.isupper():
            return plain.upper()
        if hit[0].isupper():
            return plain.capitalize()
        return plain

    return _JARGON_RE.sub(_swap, text)


# ---------------------------------------------------------------------------
# Banned-phrase deletion
# ---------------------------------------------------------------------------


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _delete_banned_phrases(text: str) -> str:
    """Walk the text sentence by sentence. Any sentence containing a
    banned phrase OR a chained hedging-filler tower gets dropped.

    Conservative: when a paragraph would become empty after deletion,
    we keep the worst-offender sentence and just strip the phrase
    inline (otherwise the layout looks broken).
    """
    if not text.strip():
        return text

    sentences = _SENTENCE_SPLIT_RE.split(text)
    kept: list[str] = []
    for s in sentences:
        if not s.strip():
            kept.append(s)
            continue
        if _BANNED_PHRASE_RE.search(s):
            # Drop the sentence outright. The paragraph as a whole still
            # carries the fact via the surrounding sentences.
            continue
        # Hedging stacks: 3+ hedge tokens in a single sentence are an
        # AI-tell. Strip the sentence rather than leave a hedge tower.
        hedge_hits = list(_HEDGING_RE.finditer(s))
        if len(hedge_hits) >= 3:
            continue
        # Otherwise strip individual hedging tokens inline.
        s = _HEDGING_RE.sub("", s)
        # Collapse double-spaces produced by stripping.
        s = re.sub(r"\s{2,}", " ", s).strip()
        if s:
            kept.append(s)

    # If we deleted every sentence (edge case — paragraph was 100%
    # banned-phrase + hedging), fall back to stripping inline phrases.
    if not [k for k in kept if k.strip()]:
        return _BANNED_PHRASE_RE.sub("", text)

    return " ".join(kept)


# ---------------------------------------------------------------------------
# Opener-shape detection
# ---------------------------------------------------------------------------


def _trim_banned_openers(text: str) -> str:
    """Find sentences that start with a banned opener phrase and trim
    the opener. Re-capitalises the remaining clause.

    Example:
        "In today's data-driven landscape, X happened" → "X happened."
        "Moreover, the regulator filed" → "The regulator filed."
    """
    sentences = _SENTENCE_SPLIT_RE.split(text)
    out: list[str] = []
    for s in sentences:
        stripped = s.lstrip()
        lower = stripped.lower()
        for opener in BANNED_OPENERS:
            if lower.startswith(opener):
                rest = stripped[len(opener):].lstrip(" ,;:—-")
                if rest:
                    out.append(rest[0].upper() + rest[1:])
                # else: opener was the entire sentence — drop it.
                break
        else:
            out.append(s)

    return " ".join(o for o in out if o.strip())


# ---------------------------------------------------------------------------
# HTML-aware traversal
# ---------------------------------------------------------------------------


_SCRUB_SKIP_TAGS = {"style", "script", "title", "head", "a"}
# Don't rewrite inside <a> tags — preserves "Snowkap" / "Mint" / publisher
# brand names in CTA anchors which might otherwise hit the jargon map.


class _HTMLScrubber(HTMLParser):
    """Walks the HTML tree, applies the 4-pass scrub to text nodes only.

    Inline styles, link href values, image src/alt, and anchor text are
    all preserved byte-for-byte. We only rewrite the visible text *between*
    tags, which is where the LLM-generated prose lives."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._skip_depth: int = 0  # >0 when inside <style>/<script>/<a>

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_str = "".join(
            f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs
        )
        self._parts.append(f"<{tag}{attr_str}>")
        if tag.lower() in _SCRUB_SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SCRUB_SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        self._parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_str = "".join(
            f' {k}="{v}"' if v is not None else f" {k}" for k, v in attrs
        )
        self._parts.append(f"<{tag}{attr_str} />")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not data.strip():
            # Inside style/script/anchor OR pure whitespace — keep as-is
            self._parts.append(data)
            return
        scrubbed = _scrub_text(data)
        self._parts.append(scrubbed)

    def handle_entityref(self, name: str) -> None:
        # Preserve HTML entities like &nbsp; &amp; &#x2018; verbatim.
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._parts.append(f"<!{decl}>")

    def result(self) -> str:
        return "".join(self._parts)


def _scrub_text(text: str) -> str:
    """Run all 4 passes on a single text node. Order matters — em-dash
    sweep first so the sentence splitter sees correct boundaries, then
    banned-phrase deletion, then opener trim, then jargon substitution
    (so jargon swaps don't leave em-dashes behind).

    Preserves the leading + trailing whitespace shape of the input so
    the scrub is idempotent across HTML node boundaries (otherwise a
    second pass would strip the leading space introduced by an opener
    trim on the first pass)."""
    # Capture the original leading/trailing whitespace so we can restore it.
    leading_ws_match = re.match(r"^\s*", text)
    trailing_ws_match = re.search(r"\s*$", text)
    leading_ws = leading_ws_match.group(0) if leading_ws_match else ""
    trailing_ws = trailing_ws_match.group(0) if trailing_ws_match else ""
    body = text.strip()

    body = _replace_em_dash(body)
    body = _delete_banned_phrases(body)
    body = _trim_banned_openers(body)
    body = _substitute_jargon(body)
    body = _strip_banned_words(body)
    # Collapse multi-space residuals from any of the passes.
    body = re.sub(r"\s{2,}", " ", body)
    # Tidy up sentence-end punctuation that lost its leading word.
    body = re.sub(r"\s+([.,;:!?])", r"\1", body)
    # Clean up orphan articles left by inline deletions ("shows a in the FMCG"
    # becomes "shows the FMCG") — single-pass, conservative.
    body = re.sub(r"\b(a|an|the)\s+(in|on|at|of|to|for|by|with)\b", r"\2", body, flags=re.IGNORECASE)
    body = re.sub(r"\s{2,}", " ", body).strip()

    return leading_ws + body + trailing_ws


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrub_html(html: str) -> str:
    """Run the 4-pass tone scrub over an HTML document.

    Idempotent + side-effect free. Single-digit-ms on a typical 6 KB email.
    Safe to call from `share_article_by_email()` between the renderer and
    the send. If parsing fails for any reason, returns the input verbatim
    (we never want a malformed scrub to block a send)."""
    if not html or not isinstance(html, str):
        return html
    try:
        scrubber = _HTMLScrubber()
        scrubber.feed(html)
        return scrubber.result()
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("content_scrubber failed (%s); returning input verbatim", exc)
        return html


def scrub_text(text: str) -> str:
    """Run the 4-pass tone scrub over a plain-text string.

    Used by callers that emit non-HTML strings (subject lines, plaintext
    email bodies, chat messages). Idempotent."""
    if not text or not isinstance(text, str):
        return text
    try:
        return _scrub_text(text)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("content_scrubber.scrub_text failed (%s); returning input", exc)
        return text


__all__ = ["scrub_html", "scrub_text"]
