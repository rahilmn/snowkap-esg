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

        event = getattr(result, "event", None)
        event_id = getattr(event, "event_id", None) if event else None
        event_polarity = _event_polarity_from_event(event)

        nlp = getattr(result, "nlp", None)
        sentiment = getattr(nlp, "sentiment", None) if nlp else None
        narrative_polarity = _narrative_polarity(None, sentiment)

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

        event = getattr(result, "event", None)
        event_id = getattr(event, "event_id", None) if event else None
        event_polarity = _event_polarity_from_event(event)

        nlp = getattr(result, "nlp", None)
        sentiment = getattr(nlp, "sentiment", None) if nlp else None
        narrative_polarity = _narrative_polarity(insight_dict, sentiment)

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
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("score_at_insight_time failed (non-fatal): %s", exc)
        return None
