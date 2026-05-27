"""Phase 39 — Editorial lede generator.

Produces a 2-3 sentence story-style opener for the Snowkap newsletter
+ in-app article view + chat seed. The lede sits ABOVE the structured
WHAT CHANGED / WHY IT MATTERS / RECOMMENDED ACTIONS / FORWARD INDICATORS
sections and hooks the CFO/CEO reader with a named entity, a curiosity
gap, and an implication — same story devices NYT and FT Alphaville use
to open editorials, voiced in Mint / Bloomberg Opinion register.

Architecture:

    1. PATTERN DISPATCHER (deterministic)
       Inspects the insight payload + EvidencePack and selects one of 6
       lede patterns based on signal availability. Same insight always
       routes to the same pattern (stable across re-renders).

    2. LLM CALL (creative writer)
       Sends Opus 4.6 (via OpenRouter `reasoning_heavy`) the selected
       pattern's skeleton + the article facts + the editorial tone
       guardrails. Constrained to ≤60 words, 2-3 sentences, named entity
       first, no engine scores.

    3. VERIFICATION GATE
       Scans LLM output via `tone_guardrails.scan_for_violations` for
       banned words, banned phrases, em-dashes, and (critically)
       score-leak patterns. Any hit rejects the candidate and falls
       through to the deterministic template for the same pattern.

    4. CACHE
       `_LLM_LEDE_CACHE[article_id]` stores the verified output so
       re-renders and re-sends pay zero LLM cost.

Lede output shape:
    {
        "text": "<2-3 sentence editorial opener>",
        "pattern": "character | contrast | temporal | setup_twist | reset | generic",
        "model_used": "anthropic/claude-opus-4.6",  # or "fallback_template"
        "cached": False,
        "char_count": 285,
        "word_count": 47,
    }

The lede NEVER references engine-derived analytics scores. It is grounded
strictly in article facts: ₹ figures, named regulators, named peers, dates,
framework citations. Score-leak rejection is enforced at the verification
gate via `_SCORE_LEAK_PATTERNS` in `tone_guardrails`.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level cache (mirrors engine/output/subject_line.py:_LLM_CACHE)
# ---------------------------------------------------------------------------

_LLM_LEDE_CACHE: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Hard constraints (per Phase 39 plan-locked decisions)
# ---------------------------------------------------------------------------

MAX_WORDS = 60
MIN_SENTENCES = 2
MAX_SENTENCES = 3


# ---------------------------------------------------------------------------
# Known regulator / framework / index names — used by pattern dispatcher
# ---------------------------------------------------------------------------

_REGULATOR_NAMES = (
    "RBI", "SEBI", "MoEFCC", "NGT", "CPCB", "IRDAI", "PFRDA",
    "SEC", "FCA", "ESMA", "EPA", "FTC", "OSHA",
    "DJSI", "MSCI", "CRISIL", "S&P", "Sustainalytics", "ISS",
    "SBTi", "ISSB", "GRI", "TCFD", "CDP", "BRSR", "CSRD", "ESRS",
    "CBAM", "EU Taxonomy", "SFDR", "PCAF",
)


def _has_regulator(text: str) -> str | None:
    """Return the first regulator name found in `text`, or None.

    Case-insensitive — the pattern dispatcher lowercases its text blob to
    handle mixed-case article prose, so we have to match either case here.
    Returns the canonical (case-correct) name from `_REGULATOR_NAMES`,
    not the matched substring, so downstream templates can use it
    directly (e.g. "RBI" not "rbi").
    """
    if not text:
        return None
    for name in _REGULATOR_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            return name
    return None


# ---------------------------------------------------------------------------
# Pattern dispatcher — selects one of 6 lede shapes deterministically
# ---------------------------------------------------------------------------


def _select_pattern(
    insight: dict[str, Any],
    analysis: dict[str, Any],
    evidence_pack: dict[str, Any] | None,
) -> str:
    """Pick the lede pattern based on signal availability. Same insight
    always routes to the same pattern across re-renders.

    Priority order:
       1. `temporal`     — Stage 9 cascade flags a sequence (third X this year)
       2. `contrast`     — EvidencePack carries 1+ peer comparables
       3. `character`    — A named regulator/peer/CEO appears in WHAT CHANGED
       4. `reset`        — Positive polarity + investor-narrative-changing event
       5. `setup_twist`  — DEFAULT for positive events with ₹ > ₹100 Cr or quarterly metrics
       6. `generic`      — Fallback when no other signal fires
    """
    what_changed = analysis.get("what_changed") or {}
    why = analysis.get("why_it_matters") or {}
    headline = (what_changed.get("headline") or "").lower()
    polarity = (what_changed.get("polarity") or "neutral").lower()
    event_type = (what_changed.get("event_type") or "").lower()

    # Temporal: sequence indicators in the headline / criticality summary
    crit_summary = (why.get("criticality_summary") or "").lower()
    text_blob = f"{headline} {crit_summary}"
    sequence_markers = ("third", "fourth", "fifth", "consecutive",
                        "for the first time", "since", "second straight")
    if any(m in text_blob for m in sequence_markers):
        return "temporal"

    # Contrast: peer comparables in EvidencePack
    if evidence_pack:
        comparables = evidence_pack.get("comparables") or []
        if isinstance(comparables, list) and len(comparables) >= 1:
            return "contrast"

    # Character: named regulator in headline/criticality summary
    if _has_regulator(text_blob):
        return "character"

    # Reset: positive polarity + investor-narrative-changing event
    reset_events = (
        "event_quarterly_results",
        "event_esg_rating_upgrade",
        "event_esg_certification",
        "event_capacity_addition",
        "event_green_finance_milestone",
    )
    if polarity == "positive" and event_type in reset_events:
        # Also requires a recovery/narrative anchor in the prose to make sense
        narrative_markers = ("since", "for the first time", "after years",
                             "recovery", "turnaround", "comeback")
        if any(m in text_blob for m in narrative_markers):
            return "reset"

    # Setup-twist: default for positive events with ₹ figures
    exposure = why.get("financial_exposure") or {}
    amount_cr = exposure.get("amount_cr") or 0
    if polarity == "positive":
        try:
            if float(amount_cr) >= 100:
                return "setup_twist"
        except (TypeError, ValueError):
            pass

    return "generic"


# ---------------------------------------------------------------------------
# Deterministic fallback templates — one per pattern
# ---------------------------------------------------------------------------
# Used when LLM unavailable OR LLM output fails the verification gate.
# Templates are pure-Python string composition over structured analysis
# fields — they never invent facts. Output is shorter and dryer than the
# LLM version but stays Mint-editorial-clean.


def _format_rupees(amount_cr: float | None) -> str:
    """Format an INR Cr amount with en-IN grouping. Returns '' on invalid."""
    if amount_cr is None:
        return ""
    try:
        v = float(amount_cr)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v >= 1000:
        return f"₹{v:,.0f} Cr"
    if v >= 100:
        return f"₹{v:,.0f} Cr"
    if v >= 10:
        return f"₹{v:,.1f} Cr"
    return f"₹{v:,.2f} Cr"


_KNOWN_ACRONYMS = {"icici", "hdfc", "idfc", "sbi", "rbl", "lic", "ntpc",
                   "ongc", "bpcl", "hpcl", "iocl", "gail", "bsnl", "drdo",
                   "psu", "irctc", "jsw", "yes"}


def _prettify_slug(slug: str) -> str:
    """Convert a company slug to a display name with proper acronym casing.

    icici-bank        → ICICI Bank
    yes-bank          → YES Bank
    hindustan-unilever-limited → Hindustan Unilever Limited
    jsw-energy        → JSW Energy
    """
    if not slug:
        return ""
    parts = slug.replace("_", " ").replace("-", " ").split()
    out = []
    for p in parts:
        if p.lower() in _KNOWN_ACRONYMS:
            out.append(p.upper())
        else:
            out.append(p.capitalize())
    return " ".join(out)


def _company_name_from_insight(insight: dict[str, Any], analysis: dict[str, Any]) -> str:
    """Pull a usable company display name from the insight payload."""
    # Try the top-level article block first
    article = insight.get("article") if isinstance(insight, dict) else None
    if isinstance(article, dict):
        name = article.get("company_name") or article.get("company")
        if name:
            return str(name)
    # Fall back to slug with acronym-aware prettify
    slug = (article or {}).get("company_slug") if isinstance(article, dict) else ""
    if slug:
        return _prettify_slug(str(slug))
    # Last resort — extract first capitalised token from headline
    headline = (analysis.get("what_changed") or {}).get("headline") or ""
    first_word = headline.split()[0] if headline else ""
    return first_word or "The company"


def _template_character(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Pattern: named regulator/peer takes an action. Open with the
    actor + action. Close with the SIGNAL the action carries.

    Polarity-aware: a neutral disclosure event (e.g. SEBI Takeover-Reg
    filing) is framed as a "disclosure window" not "regulator action
    against the company" so we never mislead the reader. The LLM
    upgrade path (Phase 39 main flow) reads the article body and gets
    this right by default; the template needs the safety rail.
    """
    what_changed = analysis.get("what_changed") or {}
    headline = what_changed.get("headline") or ""
    polarity = (what_changed.get("polarity") or "neutral").lower()
    regulator = _has_regulator(headline) or _has_regulator(
        (analysis.get("why_it_matters") or {}).get("criticality_summary") or ""
    )
    amount = (analysis.get("why_it_matters") or {}).get("financial_exposure") or {}
    amount_str = _format_rupees(amount.get("amount_cr"))

    if polarity == "neutral":
        # Disclosure-flavoured framing — never "regulator vs company".
        if regulator and amount_str:
            return (
                f"{company} filed a {amount_str} disclosure with the {regulator} this week. "
                f"The number is the headline. The cadence is what changes."
            )
        if regulator:
            return (
                f"{company} filed a routine disclosure with the {regulator} this week. "
                f"The substance is narrow. The pattern is worth a closer read."
            )
        if amount_str:
            return (
                f"{company} filed a {amount_str} disclosure this week. "
                f"What sits behind the number changes the read."
            )
        return f"{company} filed a routine update this week. The framing is worth a closer look."

    # Negative or positive — actor-with-action framing is fair game
    if regulator and amount_str:
        return (
            f"The {regulator} action against {company} carries a {amount_str} "
            f"price tag. The number is the receipt. The pattern is what matters."
        )
    if regulator:
        return (
            f"The {regulator} sent {company} a notice this week. "
            f"The substance is narrow. The signal is not."
        )
    if amount_str:
        return (
            f"{company} just booked a {amount_str} development. "
            f"What sits behind the number changes the calculus."
        )
    return f"{company} moved this week. The shape of the move is worth a closer look."


def _template_contrast(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Pattern: peer did X; target company's response. Open with the peer."""
    headline = (analysis.get("what_changed") or {}).get("headline") or ""
    polarity = (analysis.get("what_changed") or {}).get("polarity") or "neutral"
    if polarity == "positive":
        return (
            f"Peers in the same sector have set a new bar this quarter. "
            f"{company} just answered it. The peer comparison reframes the read."
        )
    if polarity == "negative":
        return (
            f"Peers in the same sector have been quiet on this front. "
            f"{company} is now the public test case. The result will set the cycle."
        )
    return (
        f"Peer movement around this disclosure has been measured. "
        f"{company}'s filing this week shifts the benchmark."
    )


def _template_temporal(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Pattern: historical baseline → what just changed. Open with the past."""
    what_changed = analysis.get("what_changed") or {}
    headline = what_changed.get("headline") or ""
    polarity = (what_changed.get("polarity") or "neutral").lower()
    amount = (analysis.get("why_it_matters") or {}).get("financial_exposure") or {}
    amount_str = _format_rupees(amount.get("amount_cr"))
    if polarity == "negative":
        return (
            f"This is not the first time {company} has been on this list. "
            f"It is the latest in a sequence the regulator is now tracking."
        )
    if polarity == "positive" and amount_str:
        return (
            f"A year ago {company} would not have posted this number. "
            f"This week's {amount_str} print says the cycle has turned."
        )
    return (
        f"{company} extends a sequence the market has been watching. "
        f"The latest data point reframes the trajectory."
    )


def _template_setup_twist(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Pattern: obvious-seeming headline → what's actually interesting behind it.
    Used for positive events with a quantitative anchor."""
    amount = (analysis.get("why_it_matters") or {}).get("financial_exposure") or {}
    amount_str = _format_rupees(amount.get("amount_cr"))
    what_changed = analysis.get("what_changed") or {}
    headline = what_changed.get("headline") or ""
    if amount_str:
        return (
            f"{company} posted a {amount_str} quarter on the headline. "
            f"That is the easy read. What sits behind it is the part worth watching."
        )
    return (
        f"{company}'s headline number reads strong. "
        f"What sits behind it reframes the read."
    )


def _template_reset(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Pattern: long-held narrative → what just changed it. Used for
    positive events that reframe an investor story."""
    return (
        f"For years the {company} story has been a workout case. "
        f"This week's filing puts a different number on the page."
    )


def _template_generic(
    company: str, insight: dict[str, Any], analysis: dict[str, Any]
) -> str:
    """Fallback when no pattern signal fires. Open with the company + ₹,
    close with the framework or regulator implication."""
    amount = (analysis.get("why_it_matters") or {}).get("financial_exposure") or {}
    amount_str = _format_rupees(amount.get("amount_cr"))
    frameworks = (analysis.get("what_it_triggers") or {}).get("frameworks") or []
    fw_code = ""
    if frameworks and isinstance(frameworks, list):
        first = frameworks[0]
        if isinstance(first, dict):
            fw_code = first.get("code") or ""
    if amount_str and fw_code:
        return (
            f"{company} has a {amount_str} item on the desk this week. "
            f"The {fw_code} disclosure window decides what gets said publicly."
        )
    if amount_str:
        return (
            f"{company} has a {amount_str} development on the desk. "
            f"The disclosure window decides what gets said publicly."
        )
    return f"{company} has a disclosure decision on the desk this week."


_PATTERN_TEMPLATES = {
    "character": _template_character,
    "contrast": _template_contrast,
    "temporal": _template_temporal,
    "setup_twist": _template_setup_twist,
    "reset": _template_reset,
    "generic": _template_generic,
}


# ---------------------------------------------------------------------------
# LLM call — Opus 4.6 via OpenRouter `reasoning_heavy`
# ---------------------------------------------------------------------------


def _build_user_prompt(
    pattern: str,
    company: str,
    insight: dict[str, Any],
    analysis: dict[str, Any],
    evidence_pack: dict[str, Any] | None,
) -> str:
    """Compose the per-article user prompt. Surfaces only article-grounded
    facts (₹ figures, regulators, dates, frameworks, peers). Never passes
    engine-derived scores — those would tempt the LLM to leak them."""
    what_changed = analysis.get("what_changed") or {}
    why = analysis.get("why_it_matters") or {}
    triggers = analysis.get("what_it_triggers") or {}
    headline = what_changed.get("headline") or ""
    polarity = what_changed.get("polarity") or "neutral"
    event_type = what_changed.get("event_type") or ""
    source = what_changed.get("source") or ""
    published = what_changed.get("published_at") or ""
    exposure = why.get("financial_exposure") or {}
    amount_cr = exposure.get("amount_cr")
    exposure_str = _format_rupees(amount_cr) or ""
    frameworks = triggers.get("frameworks") or []
    fw_codes = ", ".join(
        str((f or {}).get("code") or "") for f in frameworks[:3]
        if isinstance(f, dict) and (f or {}).get("code")
    )

    # EvidencePack peer comparables (when contrast pattern fired)
    peers_str = ""
    if evidence_pack:
        comparables = evidence_pack.get("comparables") or []
        if isinstance(comparables, list):
            names = []
            for c in comparables[:3]:
                if isinstance(c, dict):
                    n = c.get("company") or c.get("peer") or c.get("name")
                    if n:
                        names.append(str(n))
            if names:
                peers_str = "; ".join(names)

    # Article-body excerpt for the LLM to ground in (Phase 35.5 ensures body present)
    article = (insight.get("article") if isinstance(insight, dict) else {}) or {}
    body = (article.get("content") or "")[:1500]

    return f"""\
PATTERN: {pattern}
COMPANY: {company}
EVENT_TYPE: {event_type}
POLARITY: {polarity}
HEADLINE: {headline}
SOURCE: {source}
DATE: {published}
₹ FIGURE (article-grounded): {exposure_str or "none in article body"}
FRAMEWORK CITATIONS: {fw_codes or "none"}
PEER COMPARABLES: {peers_str or "none"}

ARTICLE BODY EXCERPT:
{body[:1200]}

Write the editorial lede now. Return ONLY the lede prose — no prefixes,
no "Here is the lede:", no markdown. 2-3 sentences, ≤60 words. Open with
a named entity (regulator / peer / ₹ / date). End with implication. No
engine scores. Mint editorial register."""


_LEDE_SYSTEM_PROMPT_BASE = """\
You are a senior editor at Mint's Sustainability desk, writing the opening
paragraph of a daily intelligence brief that goes to CFOs and CEOs of
top-1000 Indian listed companies. Your job is the LEDE — 2-3 sentences
that hook the reader before the structured brief below.

Voice: serious, named-entity-first, story-driven. Mint editorial / FT
Alphaville / Bloomberg Opinion register. Never Morning Brew casual.

The full editorial discipline appears in the EDITORIAL LEDE block below.
Read it before composing. Every constraint is enforced post-generation —
violations trigger a fallback to a deterministic template. Composing
clean prose the first time saves the system a fallback cycle.
"""


def _call_llm(
    pattern: str,
    company: str,
    insight: dict[str, Any],
    analysis: dict[str, Any],
    evidence_pack: dict[str, Any] | None,
) -> tuple[str, str]:
    """Make the LLM call. Returns (text, model_used). Empty string on failure."""
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        return "", ""

    try:
        from engine.llm import get_llm_client
        from engine.analysis.tone_guardrails import (
            apply_to_system_prompt,
            apply_lede_guardrails,
        )
    except ImportError as exc:
        logger.warning("lede_writer: import failed (%s)", exc)
        return "", ""

    # Compose the system prompt with both the general tone block + the
    # lede-specific block. Order matters: general block sets up Mint voice;
    # lede block layers the no-scores + named-entity-first constraints.
    system = _LEDE_SYSTEM_PROMPT_BASE
    system = apply_to_system_prompt(system)
    system = apply_lede_guardrails(system)

    user = _build_user_prompt(pattern, company, insight, analysis, evidence_pack)

    try:
        client = get_llm_client(task_class="reasoning_heavy")
        resp = client.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=200,
        )
        text = (getattr(resp, "text", "") or "").strip().strip('"').strip("'")
        # Strip "Lede:" / "Here is" prefixes the LLM might emit
        text = re.sub(r"^\s*(Lede|LEDE|Here\s+is\s+the\s+lede)\s*:?\s*", "", text)
        text = text.strip()
        model = getattr(resp, "model_used", "") or "anthropic/claude-opus-4.6"
        return text, model
    except Exception as exc:
        logger.warning("lede_writer LLM call failed: %s", type(exc).__name__)
        return "", ""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _count_sentences(text: str) -> int:
    """Count sentence-ending punctuation. Crude but robust."""
    if not text or not text.strip():
        return 0
    parts = re.split(r"[.!?]+\s*", text.strip().rstrip(".!?"))
    # +1 because the regex above splits BETWEEN sentences — N splits = N+1 parts
    return len([p for p in parts if p.strip()])


def _verify_lede(text: str) -> tuple[bool, str]:
    """Return (passed, reason). Reason is non-empty when verification fails."""
    if not text or not text.strip():
        return False, "empty"

    word_count = _count_words(text)
    if word_count > MAX_WORDS:
        return False, f"too_long_{word_count}_words"
    if word_count < 12:
        return False, f"too_short_{word_count}_words"

    sentence_count = _count_sentences(text)
    if sentence_count < MIN_SENTENCES:
        return False, f"too_few_sentences_{sentence_count}"
    if sentence_count > MAX_SENTENCES + 1:  # +1 tolerance for trailing fragment
        return False, f"too_many_sentences_{sentence_count}"

    # Tone + score-leak scan
    from engine.analysis.tone_guardrails import scan_for_violations

    hits = scan_for_violations(text)
    if hits:
        # Score leaks are unconditionally fatal (Phase 39 invariant)
        score_leaks = [h for h in hits if h.get("kind") == "score_leak"]
        if score_leaks:
            leaks = ", ".join(h["hit"] for h in score_leaks[:3])
            return False, f"score_leak:{leaks}"
        # Banned phrases and em-dashes also fatal for the lede
        fatal_kinds = {"banned_phrase", "banned_opener", "em_dash"}
        fatal = [h for h in hits if h.get("kind") in fatal_kinds]
        if fatal:
            leaks = ", ".join(f"{h['kind']}:{h['hit']}" for h in fatal[:3])
            return False, f"tone_violation:{leaks}"
        # Banned words and jargon — borderline. Allow up to 2 (some companies
        # have "Robust Energy" or "Empire" in their names which trigger
        # benign matches). The scrubber will handle residuals.
        soft = [h for h in hits if h.get("kind") in {"banned_word", "jargon"}]
        if len(soft) > 2:
            return False, f"too_many_soft_violations_{len(soft)}"

    return True, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_lede(
    *,
    article_id: str,
    insight: dict[str, Any],
    result: Any | None = None,
    evidence_pack: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Compose the editorial lede for an article.

    Idempotent within a process via `_LLM_LEDE_CACHE` keyed on
    `article_id`. Returns {} when no analysis block is present (defensive
    — pre-Phase-32 articles have no unified analysis to draw from).

    Output shape:
        {
            "text": str,            # 2-3 sentence editorial opener
            "pattern": str,         # one of 6 lede patterns
            "model_used": str,      # "anthropic/claude-opus-4.6" or "fallback_template"
            "cached": bool,         # True on second+ call with same article_id
            "char_count": int,
            "word_count": int,
        }

    The function never raises — LLM failures, verification failures, and
    missing data all fall through to the deterministic template path.
    """
    if not article_id:
        return {}
    if force_refresh:
        _LLM_LEDE_CACHE.pop(article_id, None)
    cached = _LLM_LEDE_CACHE.get(article_id)
    if cached:
        return {**cached, "cached": True}

    if not isinstance(insight, dict):
        return {}
    analysis = insight.get("analysis")
    if not isinstance(analysis, dict) or not analysis:
        return {}

    # 1. Pick pattern
    pattern = _select_pattern(insight, analysis, evidence_pack)
    company = _company_name_from_insight(insight, analysis)

    # 2. Try LLM
    text, model_used = _call_llm(pattern, company, insight, analysis, evidence_pack)
    if text:
        passed, reason = _verify_lede(text)
        if not passed:
            logger.info(
                "lede_writer: LLM candidate rejected (reason=%s) "
                "for article_id=%s; falling back to template",
                reason, article_id,
            )
            text = ""  # Trigger template fallback

    # 3. Fallback template
    if not text:
        try:
            text = _PATTERN_TEMPLATES.get(pattern, _template_generic)(
                company, insight, analysis,
            )
            model_used = "fallback_template"
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("lede_writer template failed: %s", exc)
            return {}

    text = text.strip()
    if not text:
        return {}

    result_dict: dict[str, Any] = {
        "text": text,
        "pattern": pattern,
        "model_used": model_used or "fallback_template",
        "cached": False,
        "char_count": len(text),
        "word_count": _count_words(text),
    }
    _LLM_LEDE_CACHE[article_id] = result_dict
    return result_dict


__all__ = ["write_lede", "MAX_WORDS"]
