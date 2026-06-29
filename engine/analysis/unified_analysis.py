"""Phase 32 — Single unified analysis composer.

Replaces the per-role split (CFO / CEO / ESG Analyst) with one
horizontally-consumable brief built from four news-flow bullets:

  1. **what_changed**      — the event itself
  2. **why_it_matters**    — industry/company materiality + ₹ stakes
  3. **what_it_triggers**  — concrete obligations (frameworks, deadlines, actions)
  4. **what_to_watch**     — forward signal (trajectory, lead indicators, benchmarks)

Plus a `methodology` block that the per-bullet `(i)` icon scopes into.

Pure-Python. Composes already-computed engine outputs (PipelineResult +
DeepInsight + perspectives + RecommendationResult). Never calls the LLM
— every field is derived from existing pipeline state.

Wired into `engine/output/writer.py::write_insight()`. Result is stamped
on the persisted insight payload at ``insight.analysis`` and the
schema_version bumps to ``3.2-template-hardened`` so existing on-disk
files re-enrich on next view via the on_demand path.
"""

from __future__ import annotations

from engine.analysis.text_budget import ARTICLE_CEILING

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bullet 1 — what_changed
# ---------------------------------------------------------------------------


def _build_what_changed(result: Any, insight: Any) -> dict[str, Any]:
    """The event itself — pulled from DeepInsight.headline + Stage-3 event."""
    headline = ""
    polarity = "neutral"
    event_type = ""

    if insight is not None:
        headline = (getattr(insight, "headline", "") or "")[:240]
        polarity = getattr(insight, "event_polarity", "neutral") or "neutral"

    if result is not None and getattr(result, "event", None) is not None:
        event = result.event
        event_type = getattr(event, "event_id", "") or getattr(event, "event_type", "") or ""

    # Fall back to article title if no LLM headline (e.g. SECONDARY tier with
    # only Stages 1–9 run).
    if not headline and result is not None:
        headline = (getattr(result, "title", "") or "")[:240]

    # Phase 50 — ₹-ground the headline. The Stage-10 LLM sometimes injects a
    # synthetic cascade ₹ ("ICICI Bank: ₹17 Cr earnings upside") into the
    # headline that the article never quotes — the approval gate then rejects
    # the whole critical citing "the what-changed section attributes a ₹ figure
    # not present in the article". When the LLM headline carries an ungrounded ₹,
    # fall back to the genuine article TITLE (grounded by definition); if the
    # title itself is unavailable, strip the ₹ clause.
    if headline and result is not None:
        body = (getattr(result, "article_content", "") or getattr(result, "content", "") or "")
        if body:
            try:
                from engine.analysis.article_financials import money_grounded, strip_money_clauses
                ok, _ung = money_grounded(headline, body)
                if not ok:
                    title = (getattr(result, "title", "") or "").strip()
                    title_ok = money_grounded(title, body)[0] if title else False
                    headline = title[:240] if (title and title_ok) else (strip_money_clauses(headline)[:240] or title[:240])
            except Exception:  # noqa: BLE001 — grounding must never break the composer
                pass

    return {
        "headline": headline,
        "event_type": event_type,
        "polarity": polarity,
        "source": getattr(result, "source", "") if result else "",
        "published_at": getattr(result, "published_at", "") if result else "",
        "url": getattr(result, "url", "") if result else "",
    }


# ---------------------------------------------------------------------------
# Bullet 2 — why_it_matters
# ---------------------------------------------------------------------------


_COMPONENT_LABELS = {
    "materiality": "industry materiality",
    "financial_magnitude": "rupee impact size",
    "actionability": "deadline urgency",
    "painpoint_match": "painpoint match",
    "recency": "freshness",
    "source_authority": "source authority",
    "sentiment_trajectory": "sentiment trajectory",
}


def _dominant_signal(components: dict[str, Any]) -> str:
    """Return the highest-scoring criticality component name."""
    best_name, best_val = "", -1.0
    for k in _COMPONENT_LABELS:
        v = components.get(k)
        if isinstance(v, (int, float)) and float(v) > best_val:
            best_name, best_val = k, float(v)
    return best_name


# Phase 47.R — financial-signal detection.
#
# Earlier behaviour: every article got an engine-estimate ₹ figure even
# when the source article body had no financial content. A MAHLE
# distributor-expansion article (zero ₹/€/$/revenue/profit/margin/budget
# terms) surfaced "₹16.9 Cr total potential upside (engine estimate)" in
# the hero band. The "(engine estimate)" tag is honest but the placement
# implies the figure is a fact from the story. It isn't — it's a
# primitive-cascade extrapolation that should be suppressed when the
# article isn't a financial story.
#
# Heuristic: if the article body contains ZERO concrete monetary tokens
# (₹/€/$/£ adjacent to digits, "Cr/crore/million/billion", or any of a
# small set of strong financial-event words like revenue/profit/EBIT/
# margin/cost/capex/budget/investment/funding), the article is treated
# as a non-financial event and the engine ₹ block is dropped to {}.
# Stage 10's LLM ₹ figures (decision_summary.financial_exposure) survive
# IFF they were extracted from the article — they're the LLM's reading
# of the actual body text, not a cascade synthesis.
_FINANCIAL_SIGNAL_RE = None


_BOUNDARY_RE = None


def _article_main_body(text: str) -> str:
    """Return only the main-article portion of `text`, dropping the
    publisher sidebar / related-posts / footer that trafilatura
    sometimes concatenates onto the end. A MAHLE distributor article
    that ended in an AutoIndustriya "Related Posts" sidebar containing
    'BMW X5 xDrive50e xLine PHEV revealed for PHP 5.498 million' was
    accidentally tagged as a financial story because the sidebar
    figure matched a generic "million" regex.

    Heuristic: truncate at the first boundary marker. If no marker,
    truncate at 70% of body length (which keeps the main story in 95%
    of cases tested).
    """
    import re
    global _BOUNDARY_RE
    if _BOUNDARY_RE is None:
        _BOUNDARY_RE = re.compile(
            r"\b(?:Related\s+Posts|Related\s+Articles|Related\s+Stories|"
            r"More\s+from|Read\s+also|Read\s+more|You\s+may\s+also\s+like|"
            r"Recommended\s+for\s+you|Trending|Popular\s+posts|Latest\s+news|"
            r"Comments|Tags?:|Filed\s+under)\b",
            re.IGNORECASE,
        )
    if not text:
        return ""
    m = _BOUNDARY_RE.search(text)
    if m:
        return text[: m.start()]
    # No explicit boundary → fall back to 70% truncation
    return text[: int(len(text) * 0.7)]


def _article_has_financial_signal(result: Any) -> bool:
    """Return True when the article's MAIN body region contains concrete
    monetary content. Excludes sidebar / related-posts content.

    A signal counts when EITHER:
    * a currency-adjacent digit appears in the main body, OR
    * at least 2 distinct financial-event words appear in the main body.

    A single generic "million" / "investment" mention is not enough —
    that catches noisy sidebar references and over-fires the suppress.
    """
    import re
    global _FINANCIAL_SIGNAL_RE
    if _FINANCIAL_SIGNAL_RE is None:
        # Group A: hard signal — currency symbol immediately adjacent to a digit
        # Group B: hard signal — digit followed by a clear monetary unit
        # Group C: soft signal — explicit financial-event words (need 2+)
        _FINANCIAL_SIGNAL_RE = re.compile(
            r"(?P<hard>[₹€$£¥]\s*[\d,]+(?:\.\d+)?|"
            r"\b\d[\d,.]*\s*(?:Cr|crore|lakh|billion|bn)\b)|"
            r"(?P<soft>\b(?:revenue|profit|EBIT|EBITDA|margin|capex|"
            r"capital expenditure|budget|funding|investment|valuation|"
            r"turnover|order\s+book|orderbook|topline|bottom\s*line|"
            r"cost\s+of\s+capital|cash\s+flow|million|million\s+dollars|"
            r"million\s+euros|million\s+rupees)\b)",
            re.IGNORECASE,
        )
    body = getattr(result, "article_content", "") or ""
    if not body:
        return False
    main = _article_main_body(body[:ARTICLE_CEILING])
    if not main:
        return False
    hard_hits = 0
    soft_hits = 0
    for m in _FINANCIAL_SIGNAL_RE.finditer(main):
        if m.group("hard"):
            hard_hits += 1
        elif m.group("soft"):
            soft_hits += 1
    return hard_hits >= 1 or soft_hits >= 2


def _cascade_dwarfed_by_article(amount_cr: float, result: Any) -> bool:
    """Phase 50.1 (Fix D) — True when the engine's cascade ₹ estimate is at
    least 50x SMALLER than the largest ₹ figure the article itself quotes
    (e.g. ~₹2 Cr cascade vs a ₹1.5 lakh crore article = a gross magnitude
    error). Such a chip is misleading next to the grounded summary figure, so
    the caller suppresses it. Conservative: only the gross-mismatch case fires;
    a comparable cascade (IDFC ₹503 Cr vs an article ₹503 cr) is untouched."""
    if result is None or not amount_cr or amount_cr <= 0:
        return False
    # Grounding source = TITLE + body (a headline figure is the article's own).
    _t = getattr(result, "title", "") or ""
    body = (_t + "\n" + (getattr(result, "article_content", "")
            or getattr(result, "content", "") or "")).strip()
    if not body:
        return False
    try:
        from engine.analysis.article_financials import max_article_cr, money_grounded
        # If the chip's OWN figure IS quoted in the article (e.g. IDFC's ₹503 Cr
        # PAT), it is accurate — keep it, even if the article also mentions a
        # bigger unrelated number. Only suppress an UNGROUNDED cascade figure
        # (e.g. ₹2 Cr) that the article never quotes AND that is dwarfed by the
        # article's real figure.
        chip = f"₹{float(amount_cr):.0f} Cr"
        if money_grounded(chip, body)[0]:
            return False  # chip figure is in the article → accurate, keep
        return max_article_cr(body) >= 50.0 * float(amount_cr)
    except Exception:  # noqa: BLE001
        return False


# Phase 55 — markers that a money figure is REPORTED in the source (the LLM is
# prompted to tag article figures "(from article)") and that the matter is
# sub-judice / unproven (so the qualifier is "alleged", not "reported").
_REPORTED_MARKERS = (
    "from article", "from the article", "reported", "as reported",
    "per the article", "article states", "quoted in", "stated amount",
)
_ALLEGED_MARKERS = (
    "alleged", "allegation", "denied bail", "bail plea", "granted bail",
    "secured bail", "fir", "chargesheet", "charge sheet", "sub judice",
    "accused", "probe", "scam fund", "siphon",
)


def _is_alleged(exposure_text: Any, result: Any) -> bool:
    """True when the matter is sub-judice / unproven, so a sourced ₹ figure
    should be labelled '(alleged)' rather than '(reported)'."""
    blob = (str(exposure_text or "")).lower()
    if result is not None:
        blob += " " + (str(getattr(result, "title", "") or "")).lower()
    return any(m in blob for m in _ALLEGED_MARKERS)


def _amount_in_article(amount: float | None, result: Any) -> bool:
    """DETERMINISTIC grounding: True when the ₹ figure is actually quoted in the
    article title/body. This does NOT depend on the LLM remembering to tag the
    figure '(from article)' — the model is unreliable about that — so a headline
    number (e.g. '₹83 crore' in the title) is always recognised as reported."""
    if result is None or not amount or amount <= 0:
        return False
    body = ((str(getattr(result, "title", "") or "")) + "\n"
            + (str(getattr(result, "article_content", "")
                   or getattr(result, "content", "") or ""))).strip()
    if not body:
        return False
    try:
        from engine.analysis.article_financials import money_grounded
        return bool(money_grounded(f"₹{float(amount):.0f} Cr", body)[0])
    except Exception:  # noqa: BLE001
        return False


def _financial_exposure_block(insight: Any, result: Any = None) -> dict[str, Any]:
    """Extract the canonical ₹ exposure from decision_summary + financial_timeline.

    Returns a dict with ``amount_cr``, ``kind``, ``source``. Empty when no
    figure is available OR when the article body has no financial signal
    (Phase 47.R — engine-estimate figures suppressed on non-financial
    stories like distributor partnerships, product launches, milestones).
    """
    if insight is None:
        return {}

    # Phase 47.R — non-financial article gate.
    # When the article body has no concrete monetary content, do not
    # surface any engine-estimate or primitive-cascade ₹ figure. The
    # LLM's `decision_summary.financial_exposure` is allowed through
    # ONLY when it appears to quote the article (parseable + the
    # body contains at least one matching currency token).
    body_has_money = _article_has_financial_signal(result) if result is not None else True

    decision = getattr(insight, "decision_summary", None) or {}
    if isinstance(decision, dict):
        exposure = decision.get("financial_exposure")
        if exposure and str(exposure).strip().lower() not in {"n/a", "none", "null", ""}:
            amount = _parse_inr_cr(str(exposure))
            # If the LLM produced a ₹ figure but the article body has
            # zero money signal, treat it as an extrapolation and drop.
            if not body_has_money and amount is not None:
                return {
                    "kind": "non_financial_event",
                    "source": "suppressed",
                    "label": "Article does not quote ₹ exposure; engine extrapolation suppressed.",
                }
            # Phase 50.1 (Fix D) — suppress a cascade chip that is grossly
            # smaller than the article's OWN quoted figure (e.g. ~₹2 Cr beside a
            # ₹1.5 lakh crore nuclear story). The grounded figure is already in
            # the summary; a tiny estimate next to it reads as an error.
            if amount is not None and amount > 0 and _cascade_dwarfed_by_article(amount, result):
                return {
                    "kind": "exposure_uncomputed",
                    "source": "suppressed",
                    "label": "Engine estimate not shown — the article quotes a much larger figure (see summary).",
                }
            # Phase 48 — CLEAN label. The old `str(exposure)[:140]` cut the
            # verbose cascade description mid-word ("...₹3000 Cr cano"),
            # which the approval gate (correctly) rejected as garbled prose.
            # Emit a short, complete label instead.
            # Phase 55 — GROUND the chip. When the LLM tagged the figure as
            # coming from the source ("(from article)" / "reported"), it is a
            # SOURCED number, not an engine estimate. Label it reported — or
            # "alleged" for a sub-judice matter — and mark source=from_article,
            # so the card never stamps "(engine estimate)" on a figure that is
            # literally in the headline. (The modelled cascade total stays an
            # engine estimate, but it is NOT what this chip shows.)
            exp_l = str(exposure).lower()
            tagged = any(m in exp_l for m in _REPORTED_MARKERS)
            if amount is not None and amount > 0 and (tagged or _amount_in_article(amount, result)):
                q = "alleged" if _is_alleged(exposure, result) else "reported"
                return {
                    "amount_cr": amount,
                    "kind": "exposure",
                    "source": "from_article",
                    "label": (f"₹{amount:,.0f} Cr ({q})" if amount >= 1
                              else f"₹{amount:.1f} Cr ({q})"),
                }
            if amount is not None and amount > 0:
                clean_label = (
                    f"~₹{amount:,.0f} Cr (engine estimate)" if amount >= 1
                    else f"~₹{amount:.1f} Cr (engine estimate)"
                )
            else:
                clean_label = "Engine-estimated exposure (see methodology)"
            return {
                "amount_cr": amount,
                "kind": "exposure",
                "source": "engine_estimate",
                "label": clean_label,
            }

    # Fall back to financial_timeline.immediate.inr_cr (deterministic from the
    # primitive engine, computed BEFORE the LLM ran).
    ft = getattr(insight, "financial_timeline", None) or {}
    if isinstance(ft, dict):
        immediate = ft.get("immediate") or {}
        if isinstance(immediate, dict):
            inr = immediate.get("inr_cr")
            if isinstance(inr, (int, float)) and inr > 0:
                # Same suppression: cascade-only ₹ on a non-financial
                # article body is a model artefact, not a fact.
                if not body_has_money:
                    return {
                        "kind": "non_financial_event",
                        "source": "suppressed",
                        "label": "Article does not quote ₹ exposure; engine extrapolation suppressed.",
                    }
                # Phase 50.1 (Fix D) — same magnitude-mismatch suppression.
                if _cascade_dwarfed_by_article(float(inr), result):
                    return {
                        "kind": "exposure_uncomputed",
                        "source": "suppressed",
                        "label": "Engine estimate not shown — the article quotes a much larger figure (see summary).",
                    }
                return {
                    "amount_cr": float(inr),
                    "kind": "immediate",
                    "source": "primitive_engine",
                    # Clean, never-truncated label (Phase 48).
                    "label": (
                        f"~₹{inr:,.0f} Cr (engine estimate)" if inr >= 1
                        else f"~₹{inr:.1f} Cr (engine estimate)"
                    ),
                }

    return {}


_INR_PATTERN_CR = None


def _parse_inr_cr(text: str) -> float | None:
    """Best-effort: extract the first ₹X Cr figure from a free-form string.

    Tolerates ranges ("₹100-150 Cr" → 150), commas, "lakh crore" notations.
    Returns ``None`` when nothing parseable is present.
    """
    global _INR_PATTERN_CR
    if _INR_PATTERN_CR is None:
        import re
        _INR_PATTERN_CR = re.compile(
            r"₹\s*([\d,]+(?:\.\d+)?)\s*(?:[\-–to]+\s*([\d,]+(?:\.\d+)?))?\s*(?:lakh\s+)?(?:Cr|crore)",
            re.IGNORECASE,
        )
    if not text:
        return None
    m = _INR_PATTERN_CR.search(text)
    if not m:
        return None
    try:
        # Take the upper bound when a range is present (conservative for risk).
        upper = m.group(2) or m.group(1)
        return float(upper.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _stakes_text(insight: Any) -> str:
    """Plain-English stakes paragraph from personal_stakes_generator."""
    if insight is None:
        return ""
    stakes = getattr(insight, "stakes_for_company", None) or {}
    if not isinstance(stakes, dict):
        return ""
    para = str(stakes.get("personal_stakes_paragraph") or "")
    # Phase 48 — truncate at a SENTENCE boundary, never mid-word. The old
    # `[:480]` cut produced garbled tails ("...optimize treasury and funding
    # costs to") that the approval gate rejected. Keep whole sentences up to
    # ~480 chars; if the first sentence alone exceeds that, cut at the last
    # space before 480 and add an ellipsis so it reads as deliberate.
    if len(para) <= 480:
        return para
    cut = para[:480]
    last_stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_stop >= 200:
        return cut[: last_stop + 1]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 0 else cut).rstrip() + "…"


_GARBLED_PATTERNS = None


def _looks_garbled(text: str) -> bool:
    """Detect truncated / incomplete prose the LLM sometimes emits when it
    hits a token cap or a malformed template, e.g.:
        "Your company is exposed to a. The incremental."
        "...opens a window to issue a."
        "₹3,000–"   (dangling range)
        "realize these b"
    Returns True when the text should be discarded for the deterministic
    fallback. Conservative — only fires on clear truncation signals.
    """
    import re
    global _GARBLED_PATTERNS
    if _GARBLED_PATTERNS is None:
        _GARBLED_PATTERNS = [
            re.compile(r"\b(?:to|of|a|the|in|on|at|for|with|and|or)\s*\.\s"),  # "exposed to a. "
            re.compile(r"\b(?:to|of|a|the|in|on|at|for|with|and|or)\s*\.$"),   # "...to a." end
            re.compile(r"₹\s*[\d,]+(?:\.\d+)?\s*[–-]\s*(?:[^\d]|$)"),           # "₹3,000–" dangling
            # "~₹180." — a rupee figure cut off with a bare period, no unit
            # (Cr/crore/lakh). The number is directly followed by "." so this
            # never matches a complete "₹180 Cr." (the unit sits between).
            re.compile(r"₹\s*[\d,]+\s*\.(?:\s|$)"),
            # "...a modeled." / "...sector-wide." — a hanging adjective from the
            # exposure cascade with its noun cut off (these words never end a
            # real sentence, so this is safe).
            re.compile(r"\b(?:modeled|sector-wide|combined|estimated|incremental)\s*\.\s*$", re.I),
            re.compile(r"\b[a-z]\s*$"),                                          # ends "...these b"
            re.compile(r"\.\s*\."),                                              # ".  ." double-stop gap
        ]
    t = (text or "").strip()
    if not t:
        return False
    for pat in _GARBLED_PATTERNS:
        if pat.search(t):
            return True
    # Ends mid-word/clause without terminal punctuation AND is short-ish
    if not t.endswith((".", "!", "?")) and len(t) < 120:
        return True
    return False


def _build_why_it_matters(
    result: Any, insight: Any, sasb_warning: str | None = None,
) -> dict[str, Any]:
    """Industry/company materiality bullet."""
    criticality = getattr(insight, "criticality", None) or {} if insight else {}
    components = criticality.get("components") or {}
    band = (criticality.get("band") or "MEDIUM").upper()
    dominant = _dominant_signal(components)

    # Phase 47.P — LLM band escalation (mirrors writer.write_insight).
    # When Stage 10's decision_summary.materiality rates higher than the
    # deterministic engine band, escalate so the reader's UI shows the
    # LLM's view. The LLM has seen article body + company context;
    # engine penalties (staleness > 30 days, polarity drift on
    # positive transition events) can mute the score artificially.
    _LLM_TO_ENGINE = {
        "CRITICAL": "CRITICAL", "HIGH": "HIGH",
        "MODERATE": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW",
    }
    _BAND_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    if insight is not None:
        ds = getattr(insight, "decision_summary", None)
        if isinstance(ds, dict):
            llm_mat = (ds.get("materiality") or "").strip().upper()
            llm_band = _LLM_TO_ENGINE.get(llm_mat)
            if llm_band and _BAND_RANK.get(llm_band, 0) > _BAND_RANK.get(band, 0):
                band = llm_band

    # Materiality weight is the float 0.0–1.0 the ontology returns from
    # `query_materiality_weight(topic, industry)`. The old code read
    # `esg_correlation` (an int 0–2 across 5 dimensions), which produced
    # values like 2.0 that violated the [0, 1] contract.
    materiality_weight: float | None = None
    if result is not None and getattr(result, "relevance", None) is not None:
        rel = result.relevance
        raw = getattr(rel, "materiality_weight", None)
        if isinstance(raw, (int, float)):
            materiality_weight = float(raw)
    if materiality_weight is None:
        m = components.get("materiality")
        if isinstance(m, (int, float)):
            materiality_weight = float(m)
    # Defensive clamp — neither upstream caller should ever exceed 1.0,
    # but a clamp here makes downstream renderers safe.
    if materiality_weight is not None:
        materiality_weight = max(0.0, min(1.0, materiality_weight))

    summary = (getattr(insight, "criticality_summary", "") if insight else "") or ""
    exposure = _financial_exposure_block(insight, result)
    stakes = _stakes_text(insight)

    # Phase 47.R + Phase 49 — strip the SYNTHETIC ₹ from the reader-facing
    # prose (criticality_summary + stakes). Two triggers:
    #   * non_financial_event — the article has no monetary content at all.
    #   * engine_estimate / primitive_engine — the ₹ is a cascade model
    #     artefact, NOT an article-quoted figure. On bank quarterly-results
    #     articles the cascade ₹ (e.g. "₹146 Cr") contradicted the article's
    #     real number (e.g. "₹13,700 Cr PAT"), and the approval gate
    #     (correctly) rejected the mismatch — demoting every bank critical to
    #     the light tier. Keeping the synthetic ₹ ONLY in the clearly-labelled
    #     financial_exposure chip (not in the prose) removes the contradiction
    #     so grounded bank criticals pass. The chip still shows
    #     "~₹146 Cr (engine estimate)" for the model's view.
    _exp_kind = (exposure or {}).get("kind")
    _exp_source = (exposure or {}).get("source", "")
    # Phase 51.K — widened so EVERY synthetic / non-article exposure strips its
    # ₹ from the reader-facing prose (engine estimate, suppressed extrapolation,
    # uncomputed). The labelled chip still shows the engine's figure, but the
    # prose must never state an engine number as a fact ("secures ~₹13,000 Cr").
    # Article-quoted exposures (source not in this set) keep their ₹ — that
    # figure IS grounded in the article.
    _strip_cascade = (
        _exp_kind in ("non_financial_event", "exposure_uncomputed")
        or _exp_source in ("engine_estimate", "primitive_engine", "suppressed")
    )
    if _strip_cascade:
        import re as _re

        def _strip_rupee_clauses(text: str) -> str:
            if not text:
                return ""
            for _ in range(6):
                new = _re.sub(
                    r"[—\-,:;]?\s*₹\s*[\d,]+(?:\.\d+)?\s*Cr\b[^\.\n]*",
                    "",
                    text,
                )
                if new == text:
                    break
                text = new
            text = _re.sub(r"\b\d[\d,.]*\s*Cr\b[^\.\n]*", "", text)
            text = _re.sub(r"\s{2,}", " ", text).strip(" -—,:;.")
            return text

        band_prefix = {
            "CRITICAL": "Critical",
            "HIGH": "High priority",
            "MEDIUM": "Worth reviewing",
            "LOW": "Low priority",
        }.get(band, "Worth reviewing")
        cleaned = _strip_rupee_clauses(summary)
        # If stripping left only the band prefix (or nothing), rebuild a
        # grounded sentence from the article headline topic — never a stub.
        # Phase 49.2 — the collapse check must catch the FULL band prefix
        # ("Low priority", "Worth reviewing"), not just a single word of it.
        # Stripping IDFC/SBI's synthetic cascade ₹ left exactly "Low priority"
        # (two words), which the single-word check missed — so the card shipped
        # a bare-band summary instead of rebuilding from the headline.
        _cleaned_norm = cleaned.strip().lower()
        _band_words = {p.lower() for p in band_prefix.split()}
        if (not cleaned
                or _cleaned_norm == band_prefix.lower()
                or _cleaned_norm in _band_words):
            topic = ""
            if insight is not None:
                topic = (getattr(insight, "headline", "") or "").strip()
            if not topic and result is not None:
                topic = (getattr(result, "title", "") or "").strip()
            for sep in (" — ", " - ", " | ", " : ", ": "):
                if sep in topic:
                    topic = topic.split(sep, 1)[0]
            topic = topic.strip().rstrip(".")[:100]
            if _exp_kind == "non_financial_event":
                cleaned = f"{band_prefix} — non-financial event; no ₹ exposure quoted in the article."
            elif topic:
                cleaned = f"{band_prefix} — {topic}."
            else:
                cleaned = f"{band_prefix} — material ESG development for your company."
        summary = cleaned
        if stakes:
            stakes = _strip_rupee_clauses(stakes) or stakes
    # Phase 45.H — defensive fallback when insight_generator's role_explainer
    # block silently failed (caught by its try/except, leaving the
    # criticality_summary field as the dataclass default ""). The reader
    # MUST see a non-empty sentence here — it's the first thing they
    # read after the headline. Recompute inline from the criticality
    # block + exposure + band using the same logic build_criticality_summary
    # uses. Defensive — only runs when the upstream stamp is missing.
    if not summary:
        try:
            from engine.analysis.role_explainer import build_criticality_summary
            # Build the dict shape build_criticality_summary expects: it
            # reads .criticality.components, .decision_summary, .event_polarity.
            decision_for_summary: dict[str, Any] = {}
            if insight is not None:
                ds = getattr(insight, "decision_summary", None)
                if isinstance(ds, dict):
                    decision_for_summary = ds
            # Phase 49.2 — pass the real article headline so the fallback
            # anchors on the actual story, never the generic "this story"
            # stub. The Adani card leaked "Worth reviewing — developing
            # story: this story." precisely because this dict omitted the
            # headline, so build_criticality_summary fell to its empty-topic
            # default.
            _headline = ""
            if insight is not None:
                _headline = (getattr(insight, "headline", "") or "").strip()
            if not _headline and result is not None:
                _headline = (getattr(result, "title", "") or "").strip()
            recovered = build_criticality_summary({
                "criticality": criticality,
                "decision_summary": decision_for_summary,
                "event_polarity": getattr(insight, "event_polarity", "") if insight else "",
                "headline": _headline,
            })
            if recovered:
                summary = recovered
        except Exception as exc:  # noqa: BLE001
            logger.warning("criticality_summary fallback failed (%s)", exc)
            # Last-resort literal: never let the field be empty so the
            # downstream UI + validation contract holds.
            band_prefix = {
                "CRITICAL": "Critical",
                "HIGH": "High priority",
                "MEDIUM": "Worth reviewing",
                "LOW": "Low priority",
            }.get(band, "Worth reviewing")
            summary = f"{band_prefix} — multiple signals point to ESG materiality for this article."
    # Fall back to a deterministic stakes sentence when the LLM stamp is
    # empty OR garbled. The "For you · " paragraph on the article sheet is
    # one of the three things the reader actually scans; never leave it
    # blank or broken. Phase 48 — the approval gate caught truncated stakes
    # like "Your company is exposed to a. The incremental." and
    # "₹3,000–" (dangling range). Detect those and replace deterministically.
    _stakes_is_fallback = False
    if not stakes or _looks_garbled(stakes):
        stakes = _fallback_stakes(insight, exposure, band)
        _stakes_is_fallback = True

    # Phase 50.1 — FINAL summary guards, applied regardless of how `summary`
    # was composed above:
    #   Fix A: drop any ₹ figure NOT in the article body — kills absurd engine
    #          estimates like "~₹16 Cr" surfaced for "15.59% of turnover".
    #   Fix C: if the summary is empty or a bare band prefix (incl. a STALE
    #          "Low priority" left on a now-MEDIUM card), rebuild a grounded
    #          one-liner from the article headline using the CURRENT band.
    _band_prefix_final = {
        "CRITICAL": "Critical", "HIGH": "High priority",
        "MEDIUM": "Worth reviewing", "LOW": "Low priority",
    }.get(band, "Worth reviewing")
    _body_final = ""
    if result is not None:
        # Grounding source = TITLE + body. Bank PAT/penalty figures (e.g. IDFC's
        # "₹503 crore") often live in the headline, not the body — the title is
        # the article's own words, so a title figure is grounded.
        _t = getattr(result, "title", "") or ""
        _body_final = (_t + "\n" + (getattr(result, "article_content", "")
                       or getattr(result, "content", "") or "")).strip()
    # Phase 54.1 — a truncated/garbled summary ("~₹180." cut mid-figure,
    # "exposed to a modeled.") must not ship; blank it so the headline-grounded
    # rebuild below fires. why_it_matters wasn't covered by the stakes guard.
    if summary and _looks_garbled(summary):
        summary = ""
    if summary and _body_final:
        try:
            from engine.analysis.article_financials import money_grounded
            if not money_grounded(summary, _body_final)[0]:
                summary = ""  # ungrounded ₹ → rebuild cleanly from headline below
        except Exception:  # noqa: BLE001
            pass
    _norm = (summary or "").strip().lower().rstrip(".")
    _ALL_BAND = {"critical", "high priority", "worth reviewing", "low priority"}
    if (not _norm) or (_norm in _ALL_BAND) or (_norm in {w for p in _ALL_BAND for w in p.split()}):
        _topic = (getattr(insight, "headline", "") or "").strip() if insight else ""
        if not _topic and result is not None:
            _topic = (getattr(result, "title", "") or "").strip()
        # Pipe delimits a source/section ("Business News | IDFC ...") — take the
        # LONGEST segment (the headline), not the short source prefix.
        if " | " in _topic:
            _topic = max((s.strip() for s in _topic.split(" | ")), key=len)
        for _sep in (" — ", " - ", " : ", ": "):
            if _sep in _topic:
                _topic = _topic.split(_sep, 1)[0]
        _topic = _topic.strip().rstrip(".")[:110]
        summary = (f"{_band_prefix_final} — {_topic}." if _topic
                   else f"{_band_prefix_final} — material ESG development for your company.")

    return {
        "materiality_band": band,
        "materiality_weight": (
            round(float(materiality_weight), 3) if materiality_weight is not None else None
        ),
        "dominant_signal": dominant,
        "criticality_summary": _truncate_prose(summary, 280),
        "stakes_for_company": stakes,
        # Phase 51.K — lets the UI badge a degraded panel: "deterministic_fallback"
        # means the LLM personal-stakes call returned empty and this is the
        # measured fallback sentence, not a model-generated take.
        "stakes_source": "deterministic_fallback" if _stakes_is_fallback else "llm",
        "financial_exposure": exposure,
        "warning": sasb_warning,  # Phase 3 sets "sasb_unmapped" when applicable
    }


def _truncate_prose(text: str, cap: int = 280) -> str:
    """Truncate prose at a sentence (or word) boundary — never mid-word.

    Keeps whole sentences up to ``cap`` chars; if the first sentence alone
    exceeds ``cap``, cut at the last space before ``cap`` and append an ellipsis
    so the cut reads as deliberate. Replaces the ad-hoc ``summary[:280]`` slice
    that produced garbled tails like "…long-term PPAs and capacity expan".
    (Mirrors the boundary logic already used in ``_stakes_text``.)
    """
    if not text:
        return ""
    if len(text) <= cap:
        return text
    cut = text[:cap]
    last_stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_stop >= int(cap * 0.6):
        return cut[: last_stop + 1].rstrip()
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > 0 else cut).rstrip(" -—,:;") + "…"


def _fallback_stakes(insight: Any, exposure: dict[str, Any], band: str) -> str:
    """Deterministic one-sentence stakes paragraph used when the LLM
    didn't stamp `personal_stakes_paragraph`. Anchors on the polarity
    + financial exposure + band so it reads as actionable, not generic.
    """
    if insight is None:
        return ""
    polarity = (getattr(insight, "event_polarity", "") or "neutral").lower()
    amount = (exposure or {}).get("amount_cr")
    # Phase 49 — never inject a SYNTHETIC cascade ₹ into the stakes prose.
    # Engine-estimate / primitive figures stay only in the labelled exposure
    # chip; the stakes sentence stays qualitative so it can't contradict the
    # article's real numbers (the bank-critical rejection driver).
    _src = (exposure or {}).get("source", "")
    amount_str = ""
    if (_src not in ("engine_estimate", "primitive_engine", "suppressed", "not_computed")
            and isinstance(amount, (int, float)) and amount > 0):
        if amount >= 100:
            amount_str = f"₹{amount:,.0f} Cr"
        else:
            amount_str = f"₹{amount:.1f} Cr"

    # Phase 51.K — this is a DETERMINISTIC fallback (the LLM personal-stakes
    # call returned empty/garbled). It must read as honest and measured, NOT
    # manufacture a confident board-narrative mandate — the old positive literal
    # ("…surface it in the next investor touchpoint and the board narrative")
    # was boilerplate presented as a grounded take.
    if polarity == "positive":
        if amount_str:
            return (
                f"Likely a tailwind worth roughly {amount_str} for your company — "
                f"worth noting in your next investor update."
            )
        return (
            "Likely a tailwind for your company — worth noting, though not yet a "
            "disclosure-grade action item."
        )
    if polarity == "negative":
        if amount_str:
            return (
                f"Potential downside of roughly {amount_str} for your company — "
                f"worth reviewing before the next reporting cycle."
            )
        return (
            "Carries potential downside for your company — worth reviewing "
            "before the next reporting cycle."
        )
    # neutral / disclosure event
    if band in ("CRITICAL", "HIGH"):
        return (
            "This is a disclosure-grade event to address in your next reporting "
            "cycle — verify your framework mapping and assign an owner."
        )
    return (
        "Worth tracking for your watchlist — no immediate action required, "
        "but the underlying topic is material to your industry."
    )


# ---------------------------------------------------------------------------
# Bullet 3 — what_it_triggers
# ---------------------------------------------------------------------------


def _sanitize_framework_hit(raw: Any) -> dict[str, Any] | None:
    """Phase 56.D — normalize a rec's ``framework_hit`` for the API payload.

    Returns a trimmed dict (string fields capped, ``mandatory`` coerced bool)
    or ``None`` when absent/malformed. The facts (framework/principle/mandatory)
    are ontology-anchored upstream; this only shapes them for transport.
    """
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("principle_code", "") or "")[:24]
    framework = str(raw.get("framework", "") or "")[:24]
    # Need at least a framework to render anything meaningful.
    if not framework and not code:
        return None
    return {
        "framework": framework,
        "principle_code": code,
        "principle_title": str(raw.get("principle_title", "") or "")[:80],
        "mandatory": bool(raw.get("mandatory", False)),
        "region": str(raw.get("region", "") or "")[:16],
        "interpretation": str(raw.get("interpretation", "") or "")[:600],
    }


def _build_what_it_triggers(
    result: Any, recommendations: Any,
) -> dict[str, Any]:
    """Concrete obligations: top frameworks + top 3 recommended actions."""
    frameworks_out: list[dict[str, Any]] = []
    if result is not None:
        for fm in (getattr(result, "frameworks", []) or [])[:3]:
            entry: dict[str, Any] = {
                "code": getattr(fm, "framework_id", "") or getattr(fm, "code", "") or "",
                "section": "",
                "is_mandatory": bool(getattr(fm, "is_mandatory", False)),
            }
            # Triggered sections come as a list — pick the first as the headline.
            sections = (
                getattr(fm, "triggered_sections", None)
                or getattr(fm, "sections", None)
                or []
            )
            if isinstance(sections, list) and sections:
                first = sections[0]
                entry["section"] = (
                    first.get("code") if isinstance(first, dict)
                    else str(first)
                )[:48]
            deadline_days = getattr(fm, "deadline_days", None)
            if isinstance(deadline_days, (int, float)) and deadline_days > 0:
                entry["deadline_days"] = int(deadline_days)
            frameworks_out.append(entry)

    actions_out: list[dict[str, Any]] = []
    if recommendations is not None:
        recs = getattr(recommendations, "recommendations", None) or []
        for r in recs[:3]:
            raw_title = (getattr(r, "title", "") or "")[:140]
            deadline = (getattr(r, "deadline", "") or getattr(r, "timeline", "") or "")[:80]
            # Strip any "by YYYY-MM-DD" / "by FY2X" / "by Q3 2026" suffix
            # that the LLM tucked into the title — otherwise the frontend
            # renders the deadline twice ("Title by 2026-06-30 (by 2026-06-30)").
            cleaned_title = _strip_inline_deadline(raw_title)
            # Phase 35 — surface richer rec metadata so the email + UI can
            # show cost band + payback + ROI inline. The payload already
            # carried these fields from the recommendation engine; we just
            # weren't passing them through to the unified-analysis block.
            actions_out.append({
                "title": cleaned_title,
                "deadline": deadline,
                "owner": (getattr(r, "owner", "") or getattr(r, "responsible_party", "") or "")[:60],
                "budget": getattr(r, "estimated_budget", None) or getattr(r, "budget", None),
                "framework_section": (getattr(r, "framework_section", "") or "")[:48],
                "type": getattr(r, "type", "") or "",
                # Phase 35 additions:
                "payback_months": getattr(r, "payback_months", None),
                "roi_pct": getattr(r, "roi_percentage", None),
                "estimated_impact": (getattr(r, "estimated_impact", "") or "")[:24],
                # Phase 56.D — anchored framework-hit interpretation for the
                # swipe-up "how this hits your framework" block. Ontology facts
                # (framework/principle/mandatory) + one LLM interpretation line.
                "framework_hit": _sanitize_framework_hit(
                    getattr(r, "framework_hit", None)
                ),
            })

    return {
        "frameworks": frameworks_out,
        "recommended_actions": actions_out,
        # Phase 56.F — ARTICLE-LEVEL framework hit, shown on the swipe-up even
        # when there are NO actionable recommendations (positive ESG news →
        # "monitor"). The rec-level framework_hit rides on a recommendation; this
        # guarantees every critical surfaces its BRSR principle.
        "framework_hit": _article_framework_hit(result, actions_out),
        # Phase 56.L — the OTHER frameworks the story impacts (beyond the primary
        # BRSR hit): GRI, CSRD/ESRS, IFRS S1/S2 (ISSB). Rendered as the
        # "Other frameworks this impacts" dropdown. Built from the ontology-
        # derived matches; curated cards can override via the ingest payload.
        "other_frameworks": _other_framework_hits(result),
    }


# Display labels for the "other frameworks" dropdown. PRESENTATION only — the
# match + section codes come from the ontology (result.frameworks). TCFD is
# folded into IFRS S1/S2 (ISSB consolidated TCFD).
_OTHER_FW_LABELS = {
    "GRI": "GRI", "CSRD": "CSRD (ESRS)", "ESRS": "CSRD (ESRS)",
    "ISSB": "IFRS S1/S2", "TCFD": "IFRS S1/S2",
}
_OTHER_FW_ORDER = {"GRI": 0, "CSRD (ESRS)": 1, "IFRS S1/S2": 2}


def _clean_section_label(label: str, raw: str) -> str:
    raw = (raw or "").strip()
    if label == "IFRS S1/S2":
        return "IFRS S2"  # the climate standard (ISSB)
    if label == "CSRD (ESRS)":
        return raw.replace("ESRS:", "ESRS ") if raw.upper().startswith("ESRS") else "ESRS E1"
    if label == "GRI":
        return raw.replace("GRI:", "GRI ") if raw.upper().startswith("GRI") else "GRI Standards"
    return raw


def _other_framework_hits(result: Any) -> list[dict[str, Any]]:
    """Phase 56.L — frameworks a story impacts BEYOND the primary BRSR hit, for
    the 'Other frameworks this impacts' dropdown. Surfaces GRI, CSRD/ESRS and
    IFRS S1/S2, deduped, with the ontology-matched section + a plain interpretation."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    theme = (getattr(getattr(result, "themes", None), "primary_theme", "") or "").strip() or "this development"
    for fm in (getattr(result, "frameworks", None) or []):
        fid = (getattr(fm, "framework_id", "") or "").upper()
        label = _OTHER_FW_LABELS.get(fid)
        if not label or label in seen:
            continue
        secs = getattr(fm, "triggered_sections", None) or []
        raw = ""
        if isinstance(secs, list) and secs:
            first = secs[0]
            raw = (first.get("code") if isinstance(first, dict) else str(first)) or ""
        section = _clean_section_label(label, raw)
        if not section:
            continue
        seen.add(label)
        out.append({
            "framework": label,
            "section_code": section,
            "mandatory": bool(getattr(fm, "is_mandatory", False)),
            "interpretation": (
                f"This {theme} development also reports under {label} {section} — "
                f"a voluntary global disclosure standard that investors increasingly expect."
            ),
        })
    out.sort(key=lambda h: _OTHER_FW_ORDER.get(h["framework"], 9))
    return out[:3]


def _clean_framework_prose(
    anchor: dict[str, Any], theme: str, company_name: str,
) -> str:
    """Phase 56.F — clean, deterministic interpretation for the article-level
    framework hit (the LLM rec-level line is preferred when present)."""
    fw = anchor.get("framework", "BRSR")
    title = anchor.get("principle_title") or anchor.get("principle_code", "")
    obligation = "mandatory" if anchor.get("mandatory") else "voluntary"
    topic = (theme or "").strip() or "this development"
    cname = company_name or "the company"
    return (
        f"This {topic} development is reportable under {fw} {title} "
        f"({obligation} for {cname}). Capture it in the next annual {fw} "
        f"disclosure cycle — update the principle's metrics, targets and narrative."
    )


def _article_framework_hit(
    result: Any, actions_out: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Phase 56.F — the article's BRSR principle hit, independent of recs.

    Derives the deterministic principle from the article theme (same anchor the
    recommendation engine uses) so EVERY critical shows its
    ``BRSR · Principle · MANDATORY`` chip. Interpretation prefers a rec's
    (LLM-written) line for the same principle; otherwise a clean deterministic
    line. Returns ``None`` when no principle applies (non-India / unmapped theme).
    """
    try:
        from engine.analysis.recommendation_engine import _framework_hit_anchor
        from engine.config import get_company

        company = get_company(getattr(result, "company_slug", ""))
        anchor = _framework_hit_anchor(result, company)
        if not anchor:
            return None
        # Reuse a substantive rec-level interpretation for the same principle.
        interp = ""
        for a in actions_out or []:
            fh = a.get("framework_hit") or {}
            if (fh.get("principle_code") == anchor.get("principle_code")
                    and len((fh.get("interpretation") or "").strip()) >= 25):
                interp = fh["interpretation"]
                break
        if not interp:
            theme = (getattr(getattr(result, "themes", None), "primary_theme", "") or "").strip()
            interp = _clean_framework_prose(anchor, theme, getattr(company, "name", ""))
        return {
            "framework": anchor.get("framework", ""),
            "principle_code": anchor.get("principle_code", ""),
            "principle_title": anchor.get("principle_title", ""),
            "mandatory": bool(anchor.get("mandatory")),
            "region": anchor.get("region", ""),
            "interpretation": interp[:600],
        }
    except Exception:  # noqa: BLE001 — additive, never break the composer
        return None


_INLINE_DEADLINE_PATTERN = None


def _strip_inline_deadline(title: str) -> str:
    """Remove a trailing 'by <deadline>' clause from a recommendation title.

    Catches the common LLM patterns: ISO dates, fiscal-year codes, quarter
    refs, month-name dates. Leaves the title untouched if nothing matches.
    """
    global _INLINE_DEADLINE_PATTERN
    if _INLINE_DEADLINE_PATTERN is None:
        import re
        _INLINE_DEADLINE_PATTERN = re.compile(
            r"\s+by\s+("
            r"\d{4}-\d{2}-\d{2}"                          # ISO date 2026-06-30
            r"|FY\s*\d{2,4}"                              # FY26 / FY2026
            r"|Q[1-4]\s*(?:FY)?\s*\d{2,4}"                # Q3 2026 / Q3 FY26
            r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,4}(?:[,\s]+\d{4})?"
            r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
            r"|end\s+of\s+\d{4}"                          # end of 2026
            r")\s*\.?\s*$",
            re.IGNORECASE,
        )
    if not title:
        return ""
    return _INLINE_DEADLINE_PATTERN.sub("", title).rstrip(" -—,;:.").strip()


# ---------------------------------------------------------------------------
# Bullet 4 — what_to_watch
# ---------------------------------------------------------------------------


def _build_what_to_watch(
    result: Any, insight: Any, benchmarks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Forward signal: trajectory + top risk categories + benchmarks."""
    trajectory_block: dict[str, Any] = {}
    if insight is not None:
        traj = getattr(insight, "sentiment_trajectory", None) or {}
        if isinstance(traj, dict):
            horizons = traj.get("horizons") or {}
            h3 = _horizon_direction(horizons.get("3m"))
            h6 = _horizon_direction(horizons.get("6m"))
            h12 = _horizon_direction(horizons.get("12m"))
            confidence = _horizon_confidence(horizons)
            # Only ship the block when at least one horizon has a real
            # direction. Empty-string fields downstream confuse the UI
            # ("flat" is a real signal; "" is missing data).
            if any([h3, h6, h12]):
                trajectory_block = {
                    "horizon_3m": h3,
                    "horizon_6m": h6,
                    "horizon_12m": h12,
                    "confidence": confidence,
                }

    # Top TEMPLES risk categories by score — but ONLY surface them when
    # the event is risk-flavoured. For positive events (contract wins,
    # rating upgrades, ESG certifications, partnerships) the TEMPLES
    # fallback returns generic Legal/Political/Social tags that have no
    # contextual relationship to the story — better to surface nothing
    # than to surface noise.
    top_risks: list[str] = []
    polarity = (
        getattr(insight, "event_polarity", "") or "neutral"
    ).lower() if insight is not None else "neutral"
    if polarity != "positive" and result is not None and getattr(result, "risk", None) is not None:
        risk_obj = result.risk
        temples = getattr(risk_obj, "temples_risks", None) or []
        try:
            sorted_temples = sorted(
                temples,
                key=lambda r: float(getattr(r, "score", 0) or 0),
                reverse=True,
            )
            # Filter to risks that actually scored above the noise floor
            # (probability × exposure > 0). The fallback Legal/Political/
            # Social trio is what slips through with score 0.
            top_risks = [
                (getattr(r, "category", "") or "")
                for r in sorted_temples[:3]
                if getattr(r, "category", None)
                and float(getattr(r, "score", 0) or 0) > 0
            ]
        except (TypeError, ValueError):
            top_risks = []

    # Lead indicators come from the ontology via risk_assessor; for now
    # surface the first 2 risk descriptions as proxies. Same polarity
    # gate as top_risks so we don't ship generic indicators on positive
    # events.
    lead_indicators: list[str] = []
    if polarity != "positive" and result is not None and getattr(result, "risk", None) is not None:
        temples = getattr(result.risk, "temples_risks", None) or []
        for r in temples[:2]:
            score_v = float(getattr(r, "score", 0) or 0)
            if score_v <= 0:
                continue
            desc = getattr(r, "description", "") or getattr(r, "lead_indicator", "")
            if desc:
                lead_indicators.append(str(desc)[:140])

    # Next decision window — should hold a label like "BRSR FY24 filing"
    # or "Board review" + a real by_date, NOT the financial exposure
    # headline (that lives on `why_it_matters.financial_exposure`).
    # When no real window is computable, leave the block empty so the
    # UI can hide it.
    next_window = _build_next_decision_window(insight)

    return {
        "sentiment_trajectory": trajectory_block,
        "top_risk_categories": top_risks,
        "lead_indicators": lead_indicators,
        # Phase 4 wires this up via engine.analysis.benchmarks.get_benchmarks_for_company.
        # Hidden in the UI when empty (DECISION 4.1).
        "benchmarks": benchmarks or [],
        "next_decision_window": next_window,
    }


def _build_next_decision_window(insight: Any) -> dict[str, Any]:
    """Pull a real decision window from the recommendations or framework
    deadlines. Never returns the financial-exposure headline.
    """
    if insight is None:
        return {}
    # Prefer the earliest-deadline recommendation as the "next decision".
    recs = getattr(insight, "rereact_recommendations", None) or []
    if not recs:
        # Some pipelines stamp it under `recommendations`.
        recs = getattr(insight, "recommendations", None) or []
    if isinstance(recs, dict):
        recs = recs.get("recommendations") or []
    if isinstance(recs, list) and recs:
        # Find the earliest dated rec.
        best_label = ""
        best_date = ""
        for r in recs:
            if not isinstance(r, dict):
                continue
            d = (r.get("deadline") or r.get("timeline") or "").strip()
            t = (r.get("title") or "").strip()
            if not t:
                continue
            if d and (not best_date or d < best_date):
                best_label = t[:120]
                best_date = d[:32]
            elif not best_label:
                best_label = t[:120]
        if best_label:
            return {"label": best_label, "by_date": best_date}
    return {}


def _horizon_direction(horizon: Any) -> str:
    if not isinstance(horizon, dict):
        return ""
    return str(horizon.get("direction") or "")[:20]


def _horizon_confidence(horizons: dict[str, Any]) -> str:
    """Aggregate confidence across the 3 horizons (pick the dominant level)."""
    if not isinstance(horizons, dict):
        return ""
    levels = []
    for k in ("3m", "6m", "12m"):
        h = horizons.get(k)
        if isinstance(h, dict):
            c = h.get("confidence")
            if c:
                levels.append(str(c).lower())
    if not levels:
        return ""
    # Most-common wins; ties broken by "high" > "medium" > "low".
    from collections import Counter
    counts = Counter(levels)
    return counts.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Methodology block — per-bullet "(i)" icon scopes
# ---------------------------------------------------------------------------


def _build_methodology_block(insight: Any) -> dict[str, dict[str, Any]]:
    """Per-bullet methodology entries.

    Phase 33 — calls the 4 dedicated per-bullet builders in
    ``methodology_provenance.py`` (``what_changed``, ``why_it_matters``,
    ``what_it_triggers``, ``what_to_watch``). Each builder reads the
    article's actual computed values and emits article-specific,
    plain-English explanation. Generic fallback content (the formula-heavy
    pre-Phase-33 strings) is gone — if a builder fails we surface a clear
    "methodology unavailable" stub so the UI doesn't render misleading
    generic boilerplate.
    """
    try:
        from engine.analysis.methodology_provenance import build_panel_methodology
        insight_dict = insight.to_dict() if insight is not None and hasattr(insight, "to_dict") else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis: methodology import failed (%s)", exc)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for bullet_id in ("what_changed", "why_it_matters", "what_it_triggers", "what_to_watch"):
        try:
            block = build_panel_methodology(insight_dict, bullet_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("unified_analysis: %s methodology failed (%s)", bullet_id, exc)
            block = None
        if block is None:
            block = {
                "metric": bullet_id,
                "source": "(methodology unavailable for this bullet)",
                "simple_logic": "Methodology unavailable for this bullet — please refresh the article.",
                "formula_human": "",
                "ontology_anchors": [],
                "your_inputs": {},
            }
        out[bullet_id] = block
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_unified_analysis(
    result: Any,
    insight: Any,
    *,
    recommendations: Any = None,
    sasb_warning: str | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose the unified 4-bullet analysis block.

    Args:
        result: ``engine.analysis.pipeline.PipelineResult`` — Stages 1–9 outputs.
        insight: ``engine.analysis.insight_generator.DeepInsight`` — Stage 10
            output. May be ``None`` for SECONDARY-tier articles that didn't
            run Stage 10.
        recommendations: ``engine.analysis.recommendation_engine.RecommendationResult``.
        sasb_warning: Phase 3 — pass ``"sasb_unmapped"`` when the company's
            sasb_category isn't in the materiality TTL.
        benchmarks: Phase 4 — list of ``{source, metric, value, as_of}`` dicts
            from ``company_benchmarks``. Hidden in the UI when empty.

    Returns:
        Dict with 5 top-level keys: ``what_changed``, ``why_it_matters``,
        ``what_it_triggers``, ``what_to_watch``, ``methodology``.

    Never raises — partial inputs produce partial output (empty strings /
    empty lists) rather than blocking the write path.
    """
    try:
        what_changed = _build_what_changed(result, insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_changed failed (%s)", exc)
        what_changed = {}

    try:
        why_it_matters = _build_why_it_matters(result, insight, sasb_warning)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.why_it_matters failed (%s)", exc)
        why_it_matters = {}

    try:
        what_it_triggers = _build_what_it_triggers(result, recommendations)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_it_triggers failed (%s)", exc)
        what_it_triggers = {}

    try:
        what_to_watch = _build_what_to_watch(result, insight, benchmarks)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.what_to_watch failed (%s)", exc)
        what_to_watch = {}

    try:
        methodology = _build_methodology_block(insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning("unified_analysis.methodology failed (%s)", exc)
        methodology = {}

    # Surface the headline-only flag on the analysis block so the
    # frontend can render a transparency cue without needing to walk
    # the full insight payload. The pipeline stamps `headline_only=True`
    # in insight_generator when article body < 300 chars (publisher
    # paywall / scraper 403 / JS-rendered SPA). When set, every
    # ₹ figure + specific recommendation in the analysis is engine
    # extrapolation — not article-grounded.
    headline_only = bool(getattr(insight, "headline_only", False)) if insight is not None else False
    body_char_count = int(getattr(insight, "body_char_count", 0) or 0) if insight is not None else 0

    return {
        "what_changed": what_changed,
        "why_it_matters": why_it_matters,
        "what_it_triggers": what_it_triggers,
        "what_to_watch": what_to_watch,
        "methodology": methodology,
        "headline_only": headline_only,
        "body_char_count": body_char_count,
    }


# ---------------------------------------------------------------------------
# POW-2 — Industry-shared vs per-company split.
# ---------------------------------------------------------------------------


def build_light_analysis(result: Any) -> dict[str, Any]:
    """Phase 48.C — light analysis from Stages 1-9 only (no Stage 10 insight).

    Powers the 7 low-priority deck articles. Produces a VALID 4-bullet block
    — what_changed + a banded why_it_matters + frameworks-only
    what_it_triggers + risk-only what_to_watch — but NO lede, NO
    recommendations, NO ₹ exposure. Those are reserved for the 3 critical
    articles that get the full Opus pipeline. Band is forced LOW so light
    articles always sort below the critical 3 on the deck.

    Unlike the old (buggy) tier gate that left SECONDARY articles with a
    blank deep_insight, this guarantees a non-empty analysis block on disk
    + in the deck — the relaxed light-tier deck contract (what_changed +
    criticality_summary required; lede + recs optional).
    """
    what_changed = _build_what_changed(result, None)
    crit = getattr(result, "criticality", None) or {}
    components = crit.get("components") or {}
    dominant = _dominant_signal(components)

    materiality_weight = None
    rel = getattr(result, "relevance", None)
    if rel is not None:
        raw = getattr(rel, "materiality_weight", None)
        if isinstance(raw, (int, float)):
            materiality_weight = max(0.0, min(1.0, float(raw)))

    topic = (what_changed.get("headline") or "this story").strip().rstrip(".")[:90]
    why_it_matters = {
        "materiality_band": "LOW",
        "materiality_weight": (
            round(materiality_weight, 3) if materiality_weight is not None else None
        ),
        "dominant_signal": dominant,
        "criticality_summary": f"Low priority — {topic}.",
        "stakes_for_company": (
            "Quick read for your watchlist. Open one of the day's priority "
            "articles for the full ₹ impact, recommendations and editorial brief."
        ),
        # Light articles never carry a ₹ figure — full cascade only runs on
        # the 3 critical. Mark explicitly so the UI shows no exposure chip.
        "financial_exposure": {
            "kind": "light",
            "source": "not_computed",
            "label": "Full ₹ analysis runs on the day's 3 priority articles.",
        },
        "warning": None,
    }
    return {
        "what_changed": what_changed,
        "why_it_matters": why_it_matters,
        "what_it_triggers": _build_what_it_triggers(result, None),
        "what_to_watch": _build_what_to_watch(result, None),
        "methodology": {},
        "tier": "light",
    }


def split_analysis(unified: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split the unified 4-bullet payload into industry-shared + per-company.

    See: docs/POWER_OF_NOW_ARCHITECTURE.md §4.1 — "Where each piece lands".

      - ``what_changed`` (event facts, frameworks, polarity, source) is
        industry-shared. Every reader on a story gets the same block.
      - ``why_it_matters`` (criticality_summary, stakes_for_company,
        financial_exposure, dominant_signal) is per-company.
      - ``what_it_triggers`` (recommended_actions, framework citations
        triggered for the company) is per-company.
      - ``what_to_watch`` (sentiment_trajectory, top_risk_categories,
        next_decision_window, benchmarks) is per-company.
      - ``methodology`` is split keyed-by-bullet so the "i" icon drawer
        on each bullet still sees its own block.

    Returns:
        ``(shared, personalised)`` — two dicts that combined re-form the
        original ``unified`` payload (minus any keys not in the schema).
    """
    if not isinstance(unified, dict):
        return {}, {}

    methodology = unified.get("methodology") or {}

    shared: dict[str, Any] = {
        "what_changed": unified.get("what_changed") or {},
        "methodology": {
            "what_changed": methodology.get("what_changed") or {},
        },
    }

    # Phase 54 backstop — never store a model refusal / meta-commentary as the
    # lede. The verified lede_writer path already guards this, but a refusal can
    # leak in via a different upstream setter (seen live: an empty-body article
    # whose lede was "The article body excerpt is empty. … A deterministic
    # fallback is the correct output here: …"). Replace it with a clean
    # headline-grounded opener so the card never ships the model's reasoning.
    _safe_lede = unified.get("lede") or {}
    if isinstance(_safe_lede, dict) and _safe_lede.get("text"):
        from engine.analysis.lede_writer import _looks_like_refusal
        if _looks_like_refusal(_safe_lede["text"]):
            _hl = ((unified.get("what_changed") or {}).get("headline")) or ""
            _safe_lede = ({"text": _hl, "pattern": "generic",
                           "model_used": "fallback_headline"} if _hl else {})

    personalised: dict[str, Any] = {
        "why_it_matters": unified.get("why_it_matters") or {},
        "what_it_triggers": unified.get("what_it_triggers") or {},
        "what_to_watch": unified.get("what_to_watch") or {},
        # Phase 47.P — `lede` (Phase 39 editorial opener) is article-
        # specific and per-company because the LLM grounds it in the
        # caller's persona + painpoints. It belongs in the personalised
        # half so the frontend's shared+personalised merge picks it up.
        # Prior to this fix, lede was dropped on the floor at split
        # time even though the writer stamped it on disk — `/now/feed`
        # then served decks with no lede text.
        "lede": _safe_lede,
        "methodology": {
            "why_it_matters": methodology.get("why_it_matters") or {},
            "what_it_triggers": methodology.get("what_it_triggers") or {},
            "what_to_watch": methodology.get("what_to_watch") or {},
        },
    }

    return shared, personalised
