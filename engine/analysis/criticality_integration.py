"""Phase 1.5 — Criticality scorer pipeline integration.

The pure scorer in ``criticality_scorer.py`` takes named arguments and
knows nothing about ``PipelineResult`` or ``DeepInsight``. This module
is the thin shim that:

  1. Extracts the right fields from a PipelineResult (or insight payload)
  2. Loads the tenant's cached painpoint embeddings
  3. Embeds the article (title + first 200 chars of NLP-derived narrative)
  4. Calls ``criticality_scorer.score()`` with the assembled inputs
  5. Returns the CriticalityResult dict ready to stamp onto the JSON

Two entry points:

  ``score_at_pipeline_end(result, company)`` — runs at the end of
    ``process_article``. The cascade hasn't run yet, so cascade_total_cr=0;
    financial_magnitude defaults to 0 in the score. This produces a BASELINE
    criticality used to rank articles on the home page + feed BEFORE any
    expensive Stage 10-12 enrichment.

  ``score_at_insight_time(result, insight_dict, company)`` — runs inside
    ``insight_generator`` after the cascade is computed, so financial_magnitude
    becomes meaningful. Overwrites the baseline with the full picture.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from engine.analysis.criticality_scorer import (
    CriticalityResult,
    score as score_criticality,
)
from engine.analysis.signal_classifiers import is_market_commentary

logger = logging.getLogger(__name__)


_RUPEE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(crore|Lakh|Lkh|Cr|L)?\b",
    re.IGNORECASE,
)


def _extract_cascade_total_from_insight(insight: dict[str, Any]) -> float:
    """Pull the canonical ₹ exposure from a generated insight dict.

    Looks for ``decision_summary.financial_exposure`` then ``net_impact_summary``
    for the largest ₹ figure. Returns 0.0 when nothing is found — the
    scorer treats that as ``financial_magnitude=0``.
    """
    candidates: list[float] = []
    decision = insight.get("decision_summary") or {}
    for key in ("financial_exposure", "key_risk", "top_opportunity"):
        v = decision.get(key) or ""
        for m in _RUPEE_RE.finditer(str(v)):
            try:
                amount = float(m.group(1).replace(",", ""))
                # crore vs lakh — normalise to crore
                unit = (m.group(2) or "").lower()
                if unit in ("lakh", "lkh", "l"):
                    amount /= 100.0
                candidates.append(amount)
            except (TypeError, ValueError):
                continue
    return max(candidates) if candidates else 0.0


def _narrative_polarity(insight: dict[str, Any] | None, sentiment: int | None) -> str | None:
    """Best-effort narrative-polarity classifier for the polarity_drift_penalty.

    Pulls from insight `event_polarity` if present, else from NLP sentiment.
    Returns 'positive' / 'negative' / 'neutral' / None.
    """
    if insight:
        ep = (insight.get("event_polarity") or "").strip().lower()
        if ep in ("positive", "negative", "neutral"):
            return ep
    if sentiment is None:
        return None
    if sentiment >= 1:
        return "positive"
    if sentiment <= -1:
        return "negative"
    return "neutral"


def _embed_article_safely(title: str, head_text: str) -> list[float]:
    """Wrap embed_article_for_scoring so an embedding API failure never
    breaks the pipeline — caller falls through to painpoint_match=0.
    """
    try:
        from engine.analysis.painpoint_embeddings import embed_article_for_scoring
        return embed_article_for_scoring(title, head_text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("article embedding failed (non-fatal): %s", exc)
        return []


def _load_painpoint_embeddings_safely(tenant_id: str) -> list[tuple[list[float], float]]:
    try:
        from engine.analysis.painpoint_embeddings import load_painpoint_embeddings
        return load_painpoint_embeddings(tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("painpoint embeddings load failed (non-fatal): %s", exc)
        return []


def _get_inferred_painpoints(company: Any) -> list[str]:
    """Phase 46.D — pull LLM-inferred painpoint strings off the company.

    Set by the Phase 46.A LLM resolver into
    `company.primitive_calibration["inferred_painpoints"]`. Returns []
    if missing — the criticality scorer's fallback path then yields 0.
    """
    calib = getattr(company, "primitive_calibration", None) or {}
    if not isinstance(calib, dict):
        return []
    items = calib.get("inferred_painpoints") or []
    if not isinstance(items, list):
        return []
    return [str(s) for s in items if isinstance(s, str) and s.strip()]


def _get_article_text_for_match(result: Any, insight_dict: dict[str, Any] | None) -> str:
    """Concat title + first 800 chars of body + headline for token-overlap.

    Insight headline is included because Stage 10 sometimes surfaces
    keywords that don't appear in the raw article body (LLM elaboration).
    """
    title = (getattr(result, "title", "") or "")[:240]
    body = (getattr(result, "article_content", "") or "")[:800]
    headline = ""
    if isinstance(insight_dict, dict):
        headline = (insight_dict.get("headline") or "")[:240]
    return f"{title} {headline} {body}".strip()


def _industry_materiality_for(result: Any, relevance: Any) -> float | None:
    """The SASB sector × theme materiality weight (Stage 4
    ``relevance.materiality_weight``, e.g. 0.85 for Ethics&Compliance at a bank)
    used to FLOOR the criticality materiality component.

    Phase 53.C introduced this for industry-THEMATIC articles only (company not
    named). Phase 53.I removes that gate: materiality is intrinsic to
    theme × industry and does NOT depend on whether the company is named. Gating
    it to thematic let a foreign SECTOR story (a UK-banks fraud thematic, cyber
    materiality 0.90) outrank a company's OWN ₹1,000cr fraud (Ethics 0.85, no
    floor) — the live audit caught exactly this. Applying the floor to every
    article fixes it; over-promotion is prevented by the other guards: only a
    genuine keyword-matched ACTIONABLE event (not a theme-fallback, Phase 53.H)
    earns the actionability that lifts a high-materiality article into critical,
    and market-commentary is hard-capped LOW (Phase 53.G). Returns None on any
    missing field so scoring never crashes on the additive path.

    Phase 53.N — a NON-EVENT must not receive the floor. The SASB weight says
    "this THEME is material to the sector"; without a real event there is nothing
    material happening, so flooring an event_default / theme-fallback article (an
    ESOP allotment, a sector thought-piece) lifted noise into critical even after
    Phase 53.M correctly classified it a non-event. Only a genuine, classified
    event (keyword- or LLM-matched, not event_default, not a theme-fallback)
    earns the floor.
    """
    event = getattr(result, "event", None)
    event_id = getattr(event, "event_id", None) if event is not None else None
    if not event_id or event_id == "event_default" or _is_theme_fallback(event):
        return None
    weight = getattr(relevance, "materiality_weight", None) if relevance else None
    if weight is None:
        return None
    try:
        return float(weight)
    except (TypeError, ValueError):
        return None


def _is_theme_fallback(event: Any) -> bool:
    """True when the event was NOT detected from the article text but guessed from
    the article's theme (classify_event's last-resort fallback, tagged
    matched_keywords == ['[theme_fallback]']).

    Phase 53.H — such an event must NOT lend the article actionability or an event
    severity floor: it is a thematic guess, not a detected event. Otherwise a
    routine corporate filing (an ESOP allotment themed 'Human Capital' → default
    event_labour_strike; RBI policy minutes themed 'Risk Management' → default
    event_credit_rating) gets actionability 0.8 + a severity floor and outranks a
    genuine, keyword-matched fraud/penalty event — the tier inversion the live
    audit caught. Genuine events now keyword-match (the criminal_indictment
    keyword set was enriched with fraud/bail/loan-fraud/raids in the same change)
    so they keep their actionability.
    """
    if event is None:
        return False
    kws = getattr(event, "matched_keywords", None) or []
    return list(kws) == ["[theme_fallback]"]


def _scoring_event(event: Any) -> tuple[str | None, float | None]:
    """Return (event_id, event_severity) for the criticality scorer, neutralised
    to (None, None) for a theme-fallback event so it earns neither actionability
    nor a severity floor (Phase 53.H)."""
    if event is None or _is_theme_fallback(event):
        return None, None
    event_id = getattr(event, "event_id", None)
    floor = getattr(event, "score_floor", None)
    try:
        severity = float(floor) / 10.0 if floor is not None else None
    except (TypeError, ValueError):
        severity = None
    return event_id, severity


def _event_polarity_from_event(event: Any) -> str | None:
    """Pull polarity from EventClassification if available."""
    if event is None:
        return None
    pol = getattr(event, "polarity", None)
    if pol:
        return str(pol).strip().lower()
    # Some event objects expose `expected_score_floor` / `expected_score_ceiling`
    floor = getattr(event, "score_floor", None)
    if floor is not None:
        try:
            f = float(floor)
            if f >= 5:
                return "negative"
            if f <= 3:
                return "positive"
        except (TypeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def score_at_pipeline_end(
    result: Any, company: Any,
    *, embed_article: bool = True,
) -> CriticalityResult | None:
    """Compute baseline criticality at end of process_article — cascade
    hasn't run yet, so financial_magnitude=0.

    Returns the CriticalityResult or None on any error (so the pipeline
    never crashes because of additive scoring).
    """
    try:
        relevance = getattr(result, "relevance", None)
        relevance_total = getattr(relevance, "total", None) if relevance else None
        industry_materiality_weight = _industry_materiality_for(result, relevance)

        event = getattr(result, "event", None)
        event_polarity = _event_polarity_from_event(event)

        nlp = getattr(result, "nlp", None)
        sentiment = getattr(nlp, "sentiment", None) if nlp else None
        narrative_polarity = _narrative_polarity(None, sentiment)

        # Phase 51.G — floor materiality by the EVENT TYPE's ontology severity
        # (EventRule.score_floor / 10): enforces the intrinsic per-event
        # significance that the gpt-4.1-mini 5D relevance can under-score (a
        # ₹200cr criminal indictment scored relevance 6 → materiality 0.6). NOT
        # the RiskAssessment aggregate — its non-ESG "Market & Uncertainty"
        # category is rated HIGH on routine earnings and would re-promote the
        # market noise PR #8 was reverted to avoid.
        # Phase 53.H — a theme-FALLBACK event (no keyword match, guessed from the
        # theme) earns neither actionability nor a severity floor, so a routine
        # filing can't outrank a genuine keyword-matched event.
        event_id, event_severity = _scoring_event(event)

        company_revenue = getattr(company, "revenue_cr", None)

        # Article embedding for painpoint match (only if embeddings cached)
        article_emb: list[float] = []
        painpoint_embs: list[tuple[list[float], float]] = []
        slug = getattr(company, "slug", None)
        if slug:
            painpoint_embs = _load_painpoint_embeddings_safely(slug)
            if painpoint_embs and embed_article:
                title = getattr(result, "title", "") or ""
                content = getattr(result, "article_content", "") or ""
                article_emb = _embed_article_safely(title, content[:200])

        return score_criticality(
            relevance_total=relevance_total,
            event_severity=event_severity,
            industry_materiality_weight=industry_materiality_weight,
            cascade_total_cr=0.0,                 # not yet computed at pipeline-end
            company_revenue_cr=company_revenue,
            event_id=event_id,
            article_embedding=article_emb,
            painpoint_embeddings=painpoint_embs,
            # Phase 46.D — fall back to LLM-inferred painpoint strings
            # when no curated embeddings are cached for this tenant.
            inferred_painpoints=_get_inferred_painpoints(company),
            article_text=_get_article_text_for_match(result, None),
            published_at=getattr(result, "published_at", None),
            source=getattr(result, "source", None),
            url=getattr(result, "url", None),
            cascade_confidence=None,
            event_polarity=event_polarity,
            narrative_polarity=narrative_polarity,
            # Phase 51.K — demote non-actionable market-commentary listicles
            # so they can't outrank genuine ESG signals in the feed.
            market_commentary=is_market_commentary(result),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("score_at_pipeline_end failed (non-fatal): %s", exc)
        return None


def score_at_insight_time(
    result: Any, insight_dict: dict[str, Any], company: Any,
    cascade_total_cr: float | None = None,
    cascade_confidence: str | None = None,
    *, embed_article: bool = True,
    forecaster_output: dict[str, Any] | None = None,
) -> CriticalityResult | None:
    """Compute full criticality at insight-generation time with cascade total
    available. Overwrites the baseline computed at pipeline end.

    `cascade_total_cr` can be passed explicitly (e.g. from compute_cascade
    output) or will be extracted from the insight's ``decision_summary``.

    `forecaster_output` (Phase C) — if provided, feeds the
    ``sentiment_trajectory`` component. Caller is expected to pass the
    output of ``engine.analysis.forecaster.forecast_sentiment_trajectory``.
    Missing → neutral 0.5 contribution.
    """
    try:
        relevance = getattr(result, "relevance", None)
        relevance_total = getattr(relevance, "total", None) if relevance else None
        industry_materiality_weight = _industry_materiality_for(result, relevance)

        event = getattr(result, "event", None)
        event_polarity = _event_polarity_from_event(event)

        nlp = getattr(result, "nlp", None)
        sentiment = getattr(nlp, "sentiment", None) if nlp else None
        narrative_polarity = _narrative_polarity(insight_dict, sentiment)

        # Phase 51.G — see score_at_pipeline_end: floor materiality by the event
        # type's ontology score_floor. Re-read here because the full-score pass
        # overwrites the baseline criticality.
        # Phase 53.H — theme-fallback events earn no actionability / severity floor.
        event_id, event_severity = _scoring_event(event)

        company_revenue = getattr(company, "revenue_cr", None)

        if cascade_total_cr is None:
            cascade_total_cr = _extract_cascade_total_from_insight(insight_dict)

        article_emb: list[float] = []
        painpoint_embs: list[tuple[list[float], float]] = []
        slug = getattr(company, "slug", None)
        if slug:
            painpoint_embs = _load_painpoint_embeddings_safely(slug)
            if painpoint_embs and embed_article:
                title = getattr(result, "title", "") or insight_dict.get("headline") or ""
                content = getattr(result, "article_content", "") or ""
                article_emb = _embed_article_safely(title, content[:200])

        return score_criticality(
            relevance_total=relevance_total,
            event_severity=event_severity,
            industry_materiality_weight=industry_materiality_weight,
            cascade_total_cr=cascade_total_cr,
            company_revenue_cr=company_revenue,
            event_id=event_id,
            article_embedding=article_emb,
            painpoint_embeddings=painpoint_embs,
            # Phase 46.D — fall back to LLM-inferred painpoint strings
            # when no curated embeddings are cached for this tenant.
            inferred_painpoints=_get_inferred_painpoints(company),
            article_text=_get_article_text_for_match(result, insight_dict),
            published_at=getattr(result, "published_at", None),
            source=getattr(result, "source", None),
            url=getattr(result, "url", None),
            cascade_confidence=cascade_confidence,
            event_polarity=event_polarity,
            narrative_polarity=narrative_polarity,
            forecaster_output=forecaster_output,
            # Phase 51.K — demote non-actionable market-commentary listicles.
            market_commentary=is_market_commentary(result),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("score_at_insight_time failed (non-fatal): %s", exc)
        return None
