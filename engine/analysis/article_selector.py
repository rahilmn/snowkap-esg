"""Phase 25 W7 — top-N article selector for the overnight batch pipeline.

The cost lever for the 17-tenant SLA. Without selection:

    17 tenants × 20 fetched × full pipeline = 340 LLM runs × $0.059 = $20/night

With selection (relevance scoring is FREE — no LLM):

    17 tenants × 20 fetched × free relevance + top-3 × full pipeline
        = 51 LLM runs × $0.059 = $3/night

The 17 articles we drop per tenant would have hit the REJECTED tier
anyway (per the existing 30-40% HOME rate), so quality doesn't suffer
— we just skip the ~$0.85 cost of running deep-insight on articles
that would have been filtered out post-LLM.

Score formula:

    score = (materiality_weight × source_credibility_tier × relevance_total)

Tiebreakers: published_at DESC (fresher first), then content_length DESC
(more substantive first).

Works as a standalone helper — does NOT mutate the input list.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result wrapper — preserves the article + the score so callers can audit
# ---------------------------------------------------------------------------


@dataclass
class ScoredArticle:
    """Wraps an article with the score the selector computed for it."""
    article: object  # IngestedArticle (avoid circular import)
    score: float
    materiality_weight: float
    source_credibility_tier: int
    relevance_total: float
    criticality_boost: float  # Phase 51.L — severity/negativity lift [0,1]
    rank_reason: str  # human-readable why this rank


# ---------------------------------------------------------------------------
# Top-level selector
# ---------------------------------------------------------------------------


def select_top_n_for_pipeline(
    articles: Iterable[object],
    *,
    n: int = 3,
    company_slug: str | None = None,
    primary_industry: str | None = None,
) -> list[object]:
    """Return the top-N articles by composite score.

    Returns FEWER than N when fewer pass the basic filter (no padding
    with REJECTED-grade articles). The selector NEVER calls Stage 10
    LLM — it scores via free Stage 4 relevance only.

    ``primary_industry`` is the tenant's industry from companies.json;
    used to fetch materiality weights via SPARQL. None falls back to a
    weight of 1.0 (no industry boost).
    """
    article_list = list(articles)
    if not article_list:
        return []

    scored: list[ScoredArticle] = []
    for art in article_list:
        try:
            scored.append(_score_article(art, primary_industry))
        except Exception as exc:  # noqa: BLE001 — never let one bad article tank the batch
            logger.warning(
                "select_top_n: scoring failed for article %r: %s",
                getattr(art, "id", "?"), exc,
            )

    if not scored:
        return []

    # Sort by composite score DESC, then published_at DESC, then content_length DESC
    scored.sort(
        key=lambda s: (
            -s.score,
            -_published_timestamp(getattr(s.article, "published_at", "")),
            -len(getattr(s.article, "content", "") or ""),
        )
    )

    selected = scored[: max(1, int(n))]
    if logger.isEnabledFor(logging.INFO):
        logger.info(
            "select_top_n_for_pipeline: %s slug=%s, picked %d/%d "
            "(top score %.3f, dropped %d below)",
            primary_industry or "?", company_slug or "?",
            len(selected), len(article_list),
            selected[0].score if selected else 0.0,
            max(0, len(scored) - len(selected)),
        )
    return [s.article for s in selected]


# ---------------------------------------------------------------------------
# Per-article scoring
# ---------------------------------------------------------------------------


def _score_article(article: object, primary_industry: str | None) -> ScoredArticle:
    """Compute the composite score for one article.

    Three components:
      1. ``materiality_weight`` — 0.0-1.0 from ontology (industry-specific).
         Falls back to 0.5 when industry / theme can't be resolved.
      2. ``source_credibility_tier`` — 1-5 (Phase 12.6 tier; W8a boost
         applies if W8a is enabled).
      3. ``relevance_total`` — 0-10 (Stage 4 5D scoring, FREE).

    Score = weight × (tier / 5) × (relevance / 10)  → range 0.0-1.0
    """
    title = (getattr(article, "title", "") or "").strip()
    content = (getattr(article, "content", "") or "").strip()
    summary = (getattr(article, "summary", "") or "").strip()
    source = (getattr(article, "source", "") or "").lower()

    # Heuristic relevance score — the real Stage 4 scorer requires a full
    # PipelineResult which we don't have at fetch-time. We approximate
    # via keyword density on title+summary+content.
    relevance_total = _approximate_relevance(title, summary, content)

    # Source credibility tier — uses the W8a whitelist when available
    source_credibility_tier = _resolve_source_credibility(source, getattr(article, "url", ""))

    # Materiality weight — looked up via SPARQL on the dominant theme.
    # Falls back to neutral 0.5 when no theme detected.
    materiality_weight = _resolve_materiality_weight(title, summary, primary_industry)

    base = materiality_weight * (source_credibility_tier / 5.0) * (relevance_total / 10.0)
    # Phase 51.L — severity/negativity-aware selection. A critical NEGATIVE ESG
    # event (penalty, violation, spill, fraud, community harm) must win a
    # priority-brief slot over generic green-growth keyword density, which the
    # flat keyword count alone (every keyword 0.5) could otherwise let happen.
    # Both signals are deterministic + FREE (no LLM): the ontology event
    # score_floor (severity) and a curated negative/harm keyword density.
    # Additive so a genuinely critical-negative story surfaces even when its
    # overall ESG keyword density is modest.
    severity = _event_severity_excess(title, content)
    negativity = _negativity_density(title, summary, content)
    criticality_boost = max(severity, negativity)
    score = base + _CRITICALITY_BOOST_WEIGHT * criticality_boost
    rank_reason = (
        f"base(materiality={materiality_weight:.2f}×src_tier={source_credibility_tier}/5"
        f"×relevance={relevance_total:.1f}/10)={base:.3f} + "
        f"crit_boost={criticality_boost:.2f}(sev={severity:.2f},neg={negativity:.2f})"
        f"×{_CRITICALITY_BOOST_WEIGHT}"
    )
    return ScoredArticle(
        article=article,
        score=score,
        materiality_weight=materiality_weight,
        source_credibility_tier=source_credibility_tier,
        relevance_total=relevance_total,
        criticality_boost=criticality_boost,
        rank_reason=rank_reason,
    )


# ---------------------------------------------------------------------------
# Component scorers (lightweight, no LLM, no SPARQL on the hot path)
# ---------------------------------------------------------------------------

# ESG keyword density proxy — matches words across the 21 themes + the
# Phase 25 critical signals. Each match contributes 0.5 to relevance,
# capped at 10.0.
_ESG_KEYWORDS: tuple[str, ...] = (
    # Environmental
    "climate", "carbon", "emission", "ghg", "scope 1", "scope 2", "scope 3",
    "water", "drought", "flood", "biodiversity", "deforestation",
    "pollution", "spill", "leak", "ozone", "renewable", "solar", "wind",
    "circular", "recycl", "waste", "landfill", "hazardous",
    # Social
    "labour", "labor", "child labor", "forced labour", "human rights",
    "modern slavery", "wage theft", "sweatshop", "diversity",
    "inclusion", "harassment", "discrimination", "safety",
    "community", "displacement", "tribal", "indigenous", "ngo",
    # Governance
    "corruption", "bribery", "fraud", "ml", "fcpa", "compliance",
    "disclosure", "transparency", "audit", "whistleblower",
    "board", "shareholder", "esg rating", "msci", "sustainalytics",
    # Regulatory + framework
    "sebi", "rbi", "csrd", "esrs", "brsr", "tcfd", "cdp", "sbti",
    "issb", "sasb", "gri", "sec climate", "fca", "sdr",
    # Phase 25 signal terms
    "regulatory penalty", "show cause", "consultation", "fine",
    "violation", "ban", "restriction", "tariff", "subsidy",
)


# Phase 51.L — NEGATIVE / harm / enforcement signals. These mark a critical
# negative ESG event (regulatory action, environmental harm, governance failure,
# social harm) that should win a priority-brief slot over generic green-growth
# coverage. Curated subset — deliberately NOT positive terms (solar, renewable,
# capacity) so the boost biases toward business-impacting downside.
_NEGATIVE_KEYWORDS: tuple[str, ...] = (
    # Governance / enforcement
    "fraud", "corruption", "bribery", "penalty", "fine", "fined", "show cause",
    "violation", "breach", "lawsuit", "litigation", "indictment", "indicted",
    "probe", "investigation", "ban", "banned", "revoked", "revocation",
    "non-compliance", "noncompliance", "sebi action", "rbi action", "default",
    "downgrade", "recall", "scam", "embezzle", "insider trading",
    # Environmental harm
    "spill", "leak", "contamination", "contaminated", "pollution", "hazardous",
    "toxic", "emission breach", "effluent", "encroachment", "deforestation",
    # Social harm
    "child labor", "child labour", "forced labour", "forced labor",
    "modern slavery", "wage theft", "displacement", "eviction", "protest",
    "human rights", "harassment", "discrimination", "accident", "fatality",
    "injury", "death", "casualt", "strike", "layoff", "cyber", "data breach",
)

# How hard the criticality (severity/negativity) signal lifts an article's
# ranking score. Additive: a max-critical negative event adds this much. Env-
# tunable for ops without a code change (set at boot, e.g. on Railway).
_CRITICALITY_BOOST_WEIGHT: float = float(
    os.environ.get("SNOWKAP_SELECTOR_CRITICALITY_BOOST", "0.6")
)


def _approximate_relevance(title: str, summary: str, content: str) -> float:
    """Free heuristic — count distinct ESG keywords in the article text.
    The real Stage 4 scorer is more nuanced (5D), but this is just for
    pre-pipeline ranking. Stage 4 still runs on the selected top-N."""
    haystack = " ".join((title, summary, content)).lower()
    if not haystack.strip():
        return 0.0
    distinct_hits = sum(1 for kw in _ESG_KEYWORDS if kw in haystack)
    # 0.5 per keyword, capped at 10.0
    return min(10.0, distinct_hits * 0.5)


def _negativity_density(title: str, summary: str, content: str) -> float:
    """Free [0,1] signal — density of NEGATIVE / harm / enforcement terms.
    4+ distinct negative signals → 1.0. Biases priority-brief selection toward
    business-impacting downside (penalty, violation, spill, fraud, community
    harm) rather than generic green-growth keyword density."""
    haystack = " ".join((title, summary, content)).lower()
    if not haystack.strip():
        return 0.0
    hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in haystack)
    return min(1.0, hits / 4.0)


def _event_severity_excess(title: str, content: str) -> float:
    """Above-routine event severity in [0,1] from the DETERMINISTIC (no-LLM)
    ontology event classifier's ``score_floor``.

    Routine events (floor ≤3 — quarterly results, dividends, analyst outlook)
    → 0; enforcement / harm events (heavy_penalty / violation floor 7, criminal
    indictment / license_revocation floor 8) → high. Free + safe-degrades to 0
    (so selection never crashes the batch and the no-LLM contract holds —
    ``classify_event`` is pure keyword matching against cached ontology rules).
    """
    try:
        from engine.nlp.event_classifier import classify_event
        ev = classify_event(title or "", content or "")
        floor = float(getattr(ev, "score_floor", 0) or 0)
    except Exception as exc:  # noqa: BLE001 — selection must never crash the batch
        logger.debug("event severity lookup failed (non-fatal): %s", exc)
        return 0.0
    # Normalise "above routine": floor 3 → 0, floor 10 → 1.0
    return max(0.0, min(1.0, (floor - 3.0) / 7.0))


def _resolve_source_credibility(source: str, url: str) -> int:
    """Returns tier 1-5. Defaults to tier 3 (medium credibility).
    W8a's source_credibility module bumps this by +1 for whitelisted
    domains."""
    # Try W8a if available
    try:
        from engine.ingestion.source_credibility import score as _w8a_score
        boost = _w8a_score(url or "")
        return max(1, min(5, 3 + boost))
    except (ImportError, Exception):
        # W8a not yet shipped — conservative default
        pass

    # Fallback: heuristic on source name
    source_lower = (source or "").lower()
    if any(t in source_lower for t in ("bloomberg", "reuters", "ft.com", "wsj")):
        return 5
    if any(t in source_lower for t in ("mint", "economic times", "business standard", "moneycontrol")):
        return 4
    if "google_news" in source_lower or "newsapi" in source_lower:
        return 3  # aggregator
    return 3


def _resolve_materiality_weight(
    title: str, summary: str, primary_industry: str | None,
) -> float:
    """Look up materiality weight via the ontology. Heuristic theme
    detection (matches first keyword cluster); falls back to 0.5 when
    theme or industry can't be resolved."""
    if not primary_industry:
        return 0.5

    # Cheap theme detection — assigns the article to the first ESG theme
    # whose keywords match
    theme_keywords: dict[str, tuple[str, ...]] = {
        "Water": ("water", "drought", "flood"),
        "Carbon": ("carbon", "emission", "ghg", "scope 1", "scope 2", "scope 3", "climate"),
        "Pollution": ("pollution", "spill", "leak"),
        "Labour": ("labour", "labor", "child labor", "forced labour", "wage", "modern slavery"),
        "Governance": ("corruption", "bribery", "fraud", "fcpa", "audit", "whistleblower"),
        "Compliance": ("sebi", "rbi", "csrd", "esrs", "brsr", "regulatory penalty", "show cause", "fine"),
    }
    haystack = (title + " " + summary).lower()
    detected: str | None = None
    for theme, kws in theme_keywords.items():
        if any(kw in haystack for kw in kws):
            detected = theme
            break

    if detected is None:
        return 0.5

    # Try ontology lookup — falls back to 0.5 on any error
    try:
        from engine.ontology.intelligence import query_materiality_weight
        weight = query_materiality_weight(detected, primary_industry)
        if weight is not None:
            return float(weight)
    except Exception as exc:  # noqa: BLE001
        logger.debug("materiality SPARQL lookup failed: %s", exc)
    return 0.5


def _published_timestamp(published_at: str) -> float:
    """Parse ISO datetime to Unix timestamp; older → smaller. Returns 0
    on parse failure so unparseable dates rank last."""
    if not published_at:
        return 0.0
    raw = published_at.strip()
    for cand in (raw, raw.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0
