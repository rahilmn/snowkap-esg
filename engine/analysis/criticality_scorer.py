"""Phase 1.1 — Criticality Scorer.

A reproducible, defensible criticality model for ESG news articles.
Replaces ad-hoc materiality CRITICAL/HIGH tags (which were LLM-emitted
and inconsistent) with a 6-component weighted score in [0, 1] plus
3 subtractive penalties.

Runs as Stage 9.5 of the pipeline (after the cascade in Stage 9, before
the deep insight in Stage 10). **Additive — does not replace the
existing relevance gate.** Articles still need `relevance >= 4` to enter
Stage 10+. Criticality determines RANKING and outbound floors, not gate-pass.

Per-role weights modulate the components: a CFO's criticality leans
financial_magnitude + actionability; a CEO leans materiality + painpoint
match; an analyst leans materiality + actionability.

Pure Python, deterministic. The only LLM/embedding cost paths are:
  * Optional 1 mini call for `actionability` if the event_type doesn't
    deterministically resolve it (~$0.0005/article)
  * 1 text-embedding-3-small call for the article (~$0.00002/article) —
    skipped if the tenant has no painpoint embeddings cached.

Bands (per §3.2):
  CRITICAL  ≥ 0.75
  HIGH      ≥ 0.55
  MEDIUM    ≥ 0.35
  LOW       <  0.35

Persisted to article JSON as the `criticality` block alongside existing
fields. Phase 27 added a 7th component (`sentiment_trajectory`) from the
forecaster; insight payloads are now stamped at schema_version
`2.3-trajectory-stamped`.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CriticalityComponents:
    """Seven positive components in [0, 1] + three subtractive penalties."""
    materiality: float           # normalized relevance score
    financial_magnitude: float   # log-scaled cascade ₹ vs revenue
    actionability: float         # event_type or LLM-derived decision_window
    painpoint_match: float       # cosine vs cached tenant painpoint embeddings
    recency: float               # exponential decay, 7-day half-life
    source_authority: float      # static lookup
    sentiment_trajectory: float = 0.5  # Phase C: forecaster 3m/6m direction × confidence

    # Penalties (subtractive, [0, 1])
    staleness_penalty: float = 0.0
    confidence_penalty: float = 0.0
    polarity_drift_penalty: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Band = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]


@dataclass
class CriticalityResult:
    score: float                 # 0..1, final
    band: Band
    components: CriticalityComponents
    role_scores: dict[str, float] = field(default_factory=dict)  # cfo, ceo, analyst

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "band": self.band,
            "components": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in self.components.as_dict().items()
            },
            "role_scores": {k: round(v, 4) for k, v in self.role_scores.items()},
        }


# ---------------------------------------------------------------------------
# Weights + bands (locked per the plan §3.1, §3.2)
# ---------------------------------------------------------------------------


# Phase 51.E — materiality-led default (mirrors criticality_weights.ttl). The
# deck scores every article with the "default" role; the old financial-cascade-
# led weights (financial_magnitude 0.30 > materiality 0.20) buried genuine
# ESG-governance events (no cascade ₹ → financial_magnitude=0) under financial
# news. For an ESG product, ESG materiality must lead.
WEIGHTS_DEFAULT: dict[str, float] = {
    "materiality": 0.40,
    "financial_magnitude": 0.10,
    "actionability": 0.15,
    "painpoint_match": 0.20,
    "recency": 0.075,
    "source_authority": 0.025,
    "sentiment_trajectory": 0.05,
}


WEIGHTS_BY_ROLE: dict[str, dict[str, float]] = {
    "cfo": {
        "financial_magnitude": 0.40, "actionability": 0.20, "materiality": 0.15,
        "painpoint_match": 0.10, "recency": 0.075, "source_authority": 0.025,
        "sentiment_trajectory": 0.05,
    },
    "ceo": {
        "materiality": 0.25, "painpoint_match": 0.25, "financial_magnitude": 0.20,
        "actionability": 0.10, "recency": 0.125, "source_authority": 0.025,
        "sentiment_trajectory": 0.05,
    },
    "analyst": {
        "materiality": 0.30, "painpoint_match": 0.25, "actionability": 0.15,
        "financial_magnitude": 0.15, "recency": 0.075, "source_authority": 0.025,
        "sentiment_trajectory": 0.05,
    },
}


BAND_THRESHOLDS: list[tuple[Band, float]] = [
    ("CRITICAL", 0.75),
    ("HIGH", 0.55),
    ("MEDIUM", 0.35),
    ("LOW", 0.0),
]


# Deterministic-actionability event_types (per plan §3.2 actionability rule).
# When the article carries one of these event_ids, actionability = 0.8 base
# without spending a mini LLM call.
ACTIONABLE_EVENT_TYPES: frozenset[str] = frozenset({
    "event_regulatory_filing",
    "event_litigation_initiated",
    "event_contract_award",
    "event_contract_win",
    "event_merger_announced",
    "event_rating_action",
    "event_esg_rating_change",
    "event_capacity_addition",
    "event_capacity_announcement",
    "event_license_revocation",
    "event_violation_notice",
    "event_quarterly_results",
    "event_dividend_policy",
    "event_board_change",
    "event_climate_disclosure_index",
    "event_esg_certification",
    "event_green_finance_milestone",
    "event_sebi_action",
    "event_rbi_action",
    # Phase 51.G — enforcement / harm events that DEMAND a concrete company
    # response (internal investigation, statutory disclosure, remediation,
    # incident response) but were missing here, so genuine ESG-governance
    # events scored actionability 0.2 instead of 0.8. event_criminal_indictment
    # is the class of the IDFC ₹200cr fraud that this fix is calibrated against.
    "event_criminal_indictment",
    "event_heavy_penalty",
    "event_social_violation",
    "event_cyber_incident",
    # Phase 53.C — sector/regulatory ESG events that demand a company response
    # but are usually NOT company-headlined (so they arrive via the industry-
    # thematic lane). A new RBI climate-disclosure norm, a sector emission
    # standard, an environmental show-cause, a community/labour dispute, or a
    # physical climate event all force a concrete compliance / disclosure /
    # remediation / resilience action — yet they classified as actionability 0.2
    # and could never rank into the deck for a company whose only material ESG
    # news is sector-wide. These mirror the EventTypes marked
    # ``snowkap:actionable true`` in the ontology (see _actionable_event_types).
    "event_regulatory_policy",
    "event_regulatory_announcement",
    "event_regulatory_penalty",
    "event_systemic_regulatory",
    "event_systemic_regulatory_change",
    "event_disclosure_announcement",
    "event_framework_update",
    "event_environmental_violation",
    "event_governance_failure",
    "event_fraud_disclosure",
    "event_show_cause_notice",
    "event_labour_strike",
    "event_community_conflict",
    "event_community_protest",
    "event_climate_event",
})


# Module cache for the ontology-resolved actionable set (frozenset above is the
# fallback). Mirrors the _AUTHORITY_CACHE pattern — populated lazily, reset via
# set_actionable_event_overrides() in tests.
_ACTIONABLE_CACHE: frozenset[str] | None = None


def set_actionable_event_overrides(events: frozenset[str] | set[str] | None) -> None:
    """Test hook — pin the actionable-event set without touching the ontology."""
    global _ACTIONABLE_CACHE
    _ACTIONABLE_CACHE = frozenset(events) if events is not None else None


def _actionable_event_types() -> frozenset[str]:
    """Actionable event_ids: ontology first (``snowkap:actionable true``), then
    the built-in ACTIONABLE_EVENT_TYPES literal as the fallback.

    The ontology is the source of truth (CLAUDE.md rule #1), but in prod a
    Railway volume can shadow the bundled TTLs, so the literal MUST stay a
    complete, self-sufficient fallback. The ontology set is UNIONed onto the
    literal (never replaces it) so a partial/empty ontology can only ADD, never
    silently drop a known-actionable event.
    """
    global _ACTIONABLE_CACHE
    if _ACTIONABLE_CACHE is not None:
        return _ACTIONABLE_CACHE
    onto: set[str] = set()
    try:
        from engine.ontology.intelligence import query_actionable_event_types
        onto = set(query_actionable_event_types() or ())
    except Exception:  # noqa: BLE001 — degrade to the literal fallback
        logger.warning(
            "criticality: ontology actionable-events unavailable; using built-in fallback",
            exc_info=True,
        )
    _ACTIONABLE_CACHE = ACTIONABLE_EVENT_TYPES | frozenset(onto)
    return _ACTIONABLE_CACHE


# ---------------------------------------------------------------------------
# Component derivations
# ---------------------------------------------------------------------------


def _materiality_component(
    relevance_total: float | int | None,
    event_severity: float | None = None,
    industry_materiality_weight: float | None = None,
) -> float:
    """Plan §3.2: existing relevance_score / 10, clipped [0, 1].

    `relevance.total` is the 0-10 RelevanceScore.total field from Stage 4.

    Phase 51.G — floored by the EVENT TYPE's ontology severity
    (``EventRule.score_floor`` / 10 — the ontology's per-event-type minimum
    significance). The deck score previously took the gpt-4.1-mini 5D relevance
    at face value, so a genuinely severe ESG-governance event the LLM
    under-scored (a ₹200cr criminal indictment landed relevance 6 → materiality
    0.6) got a weak materiality and was buried. The event taxonomy already
    encodes intrinsic severity (criminal_indictment / license_revocation floor
    8, heavy_penalty / social_violation / systemic_regulatory 7, vs
    quarterly_results 3, dividend_policy / analyst_outlook 2), so
    ``max(relevance/10, score_floor/10)`` enforces that ontology floor.

    This deliberately does NOT use ``RiskAssessment.aggregate_score``: that
    blend is dominated by a non-ESG "Market & Uncertainty" risk category the
    assessor rates HIGH/CRITICAL on routine earnings articles, so flooring on
    it would re-promote the market noise PR #8 was reverted to avoid. The event
    floor is self-calibrating instead — routine events have intrinsically low
    floors and can never be lifted, so business noise is not over-promoted
    (verified: across 47 live insights only governance + genuine mid-severity
    ESG events are lifted, zero quarterly/dividend/analyst articles).

    Phase 53.C — ``industry_materiality_weight`` is the ontology SASB sector ×
    theme materiality (relevance.materiality_weight from Stage 4, e.g. 0.95 for
    Climate at a Commercial Bank). It is passed ONLY for industry-thematic
    articles — sector/regulatory ESG news where the company is not named, so it
    has no painpoint match and a weak event classification, yet is genuinely
    material to the company's sector. Flooring materiality at the SASB weight is
    what lets such an article reach the deck for a company whose ONLY material
    ESG news is sector-wide. It is self-gating: the SASB neutral default is 0.5,
    so a non-material theme (weight ≤ 0.5) can never lift a relevance-based
    materiality, and the upstream market-commentary LOW-cap remains the guardrail
    against a noise listicle that happens to tag a material theme. Company-NAMED
    articles do NOT receive this floor (weight is None) — they already score via
    the event floor + painpoint + name-in-text paths, so the 7 tuned baseline
    decks are unaffected.
    """
    base = 0.0
    if relevance_total is not None:
        try:
            base = float(relevance_total) / 10.0
        except (TypeError, ValueError):
            base = 0.0
    floor = 0.0
    if event_severity is not None:
        try:
            floor = float(event_severity)
        except (TypeError, ValueError):
            floor = 0.0
    industry_floor = 0.0
    if industry_materiality_weight is not None:
        try:
            industry_floor = float(industry_materiality_weight)
        except (TypeError, ValueError):
            industry_floor = 0.0
    return _clip01(max(base, floor, industry_floor))


def _financial_magnitude_component(
    cascade_total_cr: float | None,
    company_revenue_cr: float | None,
) -> float:
    """Plan §3.2: ``min(1.0, log10(1 + cascade/revenue * 100) / 2)``.

    A cascade impact equal to 1% of revenue → ~0.5. 10% → ~1.0.
    Returns 0.0 when revenue is missing or cascade is zero.
    """
    if not cascade_total_cr or not company_revenue_cr or company_revenue_cr <= 0:
        return 0.0
    try:
        ratio_pct = float(cascade_total_cr) / float(company_revenue_cr) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    if ratio_pct <= 0:
        return 0.0
    val = math.log10(1.0 + ratio_pct) / 2.0
    return _clip01(val)


def _actionability_component(
    event_id: str | None,
    has_deadline: bool = False,
    days_to_decision: int | None = None,
) -> float:
    """Plan §3.2 — three branches:

    1. event_id ∈ actionable set (ontology + literal) → 0.8
    2. has_deadline → 1.0 - days_to_decision/180 (clipped)
    3. else → 0.2
    """
    if event_id and event_id in _actionable_event_types():
        return 0.8
    if has_deadline and days_to_decision is not None:
        try:
            d = max(0, int(days_to_decision))
        except (TypeError, ValueError):
            return 0.2
        return _clip01(1.0 - (d / 180.0))
    return 0.2


def _painpoint_match_component(
    article_embedding: list[float] | None,
    painpoint_embeddings: list[tuple[list[float], float]] | None,
    *,
    inferred_painpoints: list[str] | None = None,
    article_text: str | None = None,
) -> float:
    """Plan §3.2 — max cosine match across tenant painpoints,
    weighted by the painpoint's severity.

    `painpoint_embeddings` is a list of (embedding, severity_weight) tuples
    loaded from the tenant's painpoints.ttl + cache.

    Phase 46.D fallback: when no embeddings are available (e.g. a
    fresh self-service onboard where the user didn't manually set up
    painpoints.ttl), fall back to token overlap between
    LLM-inferred painpoint strings and the article text. This means
    every tenant — even one onboarded 5 minutes ago — has a non-zero
    painpoint signal in the criticality score.

    Returns 0.0 only if BOTH paths have no inputs.
    """
    # Primary path: embedding-based cosine match (tenants with curated
    # painpoint embeddings — the 7 baseline companies + anyone who
    # explicitly set up painpoints.ttl).
    if article_embedding and painpoint_embeddings:
        best: float = 0.0
        for emb, severity in painpoint_embeddings:
            if not emb:
                continue
            cos = _cosine(article_embedding, emb)
            weighted = cos * float(severity or 0.5)
            if weighted > best:
                best = weighted
        if best > 0:
            return _clip01(best)

    # Fallback path: LLM-inferred painpoints as strings (Phase 46.A).
    # Token overlap with the article text. Each painpoint contributes
    # the fraction of its substantive tokens that appear in the article.
    # We take the max across painpoints (the strongest match wins).
    if inferred_painpoints and article_text:
        text_lower = article_text.lower()
        # Tokens worth at least 3 chars, filter common stopwords
        _STOP = {
            "the", "and", "for", "with", "from", "this", "that", "into",
            "their", "have", "been", "will", "are", "was", "were",
            "under", "over", "between", "across", "around",
        }
        best_match: float = 0.0
        for painpoint in inferred_painpoints:
            tokens = [
                t for t in re.findall(r"\b[a-z][a-z0-9]{2,}\b", painpoint.lower())
                if t not in _STOP
            ]
            if not tokens:
                continue
            hits = sum(1 for t in tokens if t in text_lower)
            match_ratio = hits / len(tokens)
            if match_ratio > best_match:
                best_match = match_ratio
        return _clip01(best_match)

    return 0.0


def _recency_component(published_at: str | None, now: datetime | None = None) -> float:
    """Plan §3.2: ``exp(-days_since_published / 7)``.

    7-day half-life-ish (technically e-fold at 7 days).
    Returns 0.5 when published_at is missing/unparseable (neutral).
    """
    if not published_at:
        return 0.5
    pub = _parse_iso(published_at)
    if pub is None:
        return 0.5
    ref = now or datetime.now(timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    delta_days = max(0.0, (ref - pub).total_seconds() / 86400.0)
    return _clip01(math.exp(-delta_days / 7.0))


# Static source-authority lookup. Populated lazily on first call from
# `data/source_authority.json`. Can be overridden in tests via
# `set_source_authority_overrides({...})`.
_AUTHORITY_CACHE: dict[str, float] | None = None
_AUTHORITY_OVERRIDES: dict[str, float] = {}


def set_source_authority_overrides(overrides: dict[str, float]) -> None:
    """Test hook — override the lookup without touching disk."""
    global _AUTHORITY_OVERRIDES
    _AUTHORITY_OVERRIDES = dict(overrides)


def _load_source_authority() -> dict[str, float]:
    global _AUTHORITY_CACHE
    if _AUTHORITY_CACHE is not None:
        return _AUTHORITY_CACHE
    try:
        from engine.config import get_data_path
        import json
        p = get_data_path("source_authority.json")
        if p.exists():
            _AUTHORITY_CACHE = json.loads(p.read_text(encoding="utf-8"))
        else:
            _AUTHORITY_CACHE = {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_authority load failed: %s", exc)
        _AUTHORITY_CACHE = {}
    return _AUTHORITY_CACHE


def _source_authority_component(source: str | None, url: str | None = None) -> float:
    """Plan §3.2: static lookup — Bloomberg/Reuters/FT = 1.0,
    Mint/BusinessLine/ET = 0.85, aggregators = 0.5, blogs = 0.3.

    Looks up by source name first, then falls back to URL domain.
    Default 0.5 (aggregator-tier) when unknown.
    """
    auth = _load_source_authority()
    auth = {**auth, **_AUTHORITY_OVERRIDES}

    # 1. Match by source name (case-insensitive)
    if source:
        s = source.strip().lower()
        if s in auth:
            return _clip01(auth[s])
        # Substring match (e.g. "Reuters India" → matches "reuters")
        for key, val in auth.items():
            if key.lower() in s:
                return _clip01(val)

    # 2. Fallback: URL domain
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            host = host.lower().lstrip("www.")
        except Exception:  # noqa: BLE001
            host = ""
        if host:
            for key, val in auth.items():
                if key.lower() in host:
                    return _clip01(val)

    return 0.5  # unknown → aggregator-tier neutral


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


def _staleness_penalty(published_at: str | None, now: datetime | None = None) -> float:
    """Plan §3.3: 0.2 if days_since_published > 30 else 0.0."""
    if not published_at:
        return 0.0
    pub = _parse_iso(published_at)
    if pub is None:
        return 0.0
    ref = now or datetime.now(timezone.utc)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    delta_days = (ref - pub).total_seconds() / 86400.0
    return 0.2 if delta_days > 30 else 0.0


def _confidence_penalty(cascade_confidence: str | float | None) -> float:
    """Plan §3.3: 0.15 if cascade_confidence < 0.5 else 0.0.

    The cascade emits string buckets ('low'/'medium'/'high') today; we
    treat 'low' as 0.3 for this check.
    """
    if cascade_confidence is None:
        return 0.0
    if isinstance(cascade_confidence, str):
        m = {"low": 0.3, "medium": 0.6, "high": 0.85}
        v = m.get(cascade_confidence.strip().lower(), 0.6)
    else:
        try:
            v = float(cascade_confidence)
        except (TypeError, ValueError):
            return 0.0
    return 0.15 if v < 0.5 else 0.0


_TRAJECTORY_MAP: dict[tuple[str, str], float] = {
    ("declining", "high"): 0.9,
    ("declining", "moderate"): 0.7,
    ("declining", "low"): 0.55,
    ("stable", "high"): 0.5,
    ("stable", "moderate"): 0.5,
    ("stable", "low"): 0.5,
    ("improving", "low"): 0.45,
    ("improving", "moderate"): 0.3,
    ("improving", "high"): 0.1,
}


def _sentiment_trajectory_component(
    forecaster_output: dict[str, Any] | None,
) -> float:
    """Phase C: collapse forecaster horizons to a [0,1] criticality contribution.

    Reads `horizons["3m"]` and `horizons["6m"]` from the
    ``forecast_sentiment_trajectory`` output. Returns the *worse* of the two
    horizon scores so a near-term decline isn't washed out by a distant
    stabilisation. Missing / malformed input → 0.5 (neutral).
    """
    if not forecaster_output or not isinstance(forecaster_output, dict):
        return 0.5
    horizons = forecaster_output.get("horizons") or {}
    scores: list[float] = []
    for key in ("3m", "6m"):
        h = horizons.get(key) or {}
        direction = str(h.get("direction") or "").strip().lower()
        confidence = str(h.get("confidence") or "moderate").strip().lower()
        if direction in ("improving", "stable", "declining"):
            scores.append(_TRAJECTORY_MAP.get((direction, confidence), 0.5))
    if not scores:
        return 0.5
    return max(scores)  # worse horizon dominates


def _polarity_drift_penalty(
    event_polarity: str | None, narrative_polarity: str | None,
) -> float:
    """Plan §3.3: 0.2 if polarities mismatch (positive event, negative
    narrative — usually a low-quality source). Both must be set.
    """
    if not event_polarity or not narrative_polarity:
        return 0.0
    e = event_polarity.strip().lower()
    n = narrative_polarity.strip().lower()
    if e in ("positive", "negative") and n in ("positive", "negative") and e != n:
        return 0.2
    return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_components(
    *,
    relevance_total: float | None,
    event_severity: float | None = None,
    industry_materiality_weight: float | None = None,
    cascade_total_cr: float | None,
    company_revenue_cr: float | None,
    event_id: str | None,
    has_deadline: bool = False,
    days_to_decision: int | None = None,
    article_embedding: list[float] | None = None,
    painpoint_embeddings: list[tuple[list[float], float]] | None = None,
    published_at: str | None = None,
    source: str | None = None,
    url: str | None = None,
    cascade_confidence: str | float | None = None,
    event_polarity: str | None = None,
    narrative_polarity: str | None = None,
    forecaster_output: dict[str, Any] | None = None,
    now: datetime | None = None,
    # Phase 46.D — domain-only onboards have no curated painpoint
    # embeddings. Pass through the LLM-inferred painpoint strings + the
    # article title/body so the painpoint scorer can fall back to
    # token-overlap matching. Mediates the criticality score by the
    # tenant's actual concerns even on day-zero of a fresh onboard.
    inferred_painpoints: list[str] | None = None,
    article_text: str | None = None,
) -> CriticalityComponents:
    """Compute all 7 positive components + 3 penalties for one article.

    Pure function — deterministic given identical inputs (modulo embedding
    floats which are stable per article+model).
    """
    return CriticalityComponents(
        materiality=_materiality_component(
            relevance_total, event_severity, industry_materiality_weight,
        ),
        financial_magnitude=_financial_magnitude_component(
            cascade_total_cr, company_revenue_cr,
        ),
        actionability=_actionability_component(
            event_id, has_deadline=has_deadline, days_to_decision=days_to_decision,
        ),
        painpoint_match=_painpoint_match_component(
            article_embedding, painpoint_embeddings,
            inferred_painpoints=inferred_painpoints,
            article_text=article_text,
        ),
        recency=_recency_component(published_at, now=now),
        source_authority=_source_authority_component(source, url=url),
        sentiment_trajectory=_sentiment_trajectory_component(forecaster_output),
        staleness_penalty=_staleness_penalty(published_at, now=now),
        confidence_penalty=_confidence_penalty(cascade_confidence),
        polarity_drift_penalty=_polarity_drift_penalty(
            event_polarity, narrative_polarity,
        ),
    )


def _weighted_score(
    components: CriticalityComponents, weights: dict[str, float],
) -> float:
    """Apply weights to the 7 positive components, then subtract penalties."""
    pos = (
        components.materiality * weights["materiality"]
        + components.financial_magnitude * weights["financial_magnitude"]
        + components.actionability * weights["actionability"]
        + components.painpoint_match * weights["painpoint_match"]
        + components.recency * weights["recency"]
        + components.source_authority * weights["source_authority"]
        + components.sentiment_trajectory * weights.get("sentiment_trajectory", 0.0)
    )
    pen = (
        components.staleness_penalty
        + components.confidence_penalty
        + components.polarity_drift_penalty
    )
    return _clip01(pos - pen)


def _ontology_weight_sets() -> dict[str, dict[str, float]]:
    """Phase 51 — criticality weight sets from the ontology, or {} if unavailable."""
    try:
        from engine.ontology.intelligence import query_criticality_weights
        return query_criticality_weights() or {}
    except Exception:  # noqa: BLE001 — degrade to the built-in literals
        logger.warning("criticality: ontology weights unavailable; using built-in fallback", exc_info=True)
        return {}


def _weights_for(role: str) -> dict[str, float]:
    """Weight dict for a role: ontology first (Phase 51), then built-in literals."""
    key = (role or "").strip().lower()
    sets = _ontology_weight_sets()
    if sets.get(key):
        return sets[key]
    if sets.get("default"):
        return sets["default"]
    return WEIGHTS_BY_ROLE.get(key, WEIGHTS_DEFAULT)


def _active_bands() -> list[tuple[Band, float]]:
    """Criticality bands (level, min_score) DESC: ontology first, then literals."""
    try:
        from engine.ontology.intelligence import query_criticality_bands
        bands = query_criticality_bands()
        if bands:
            return bands  # already sorted DESC by min score
    except Exception:  # noqa: BLE001 — degrade to the built-in literals
        logger.warning("criticality: ontology bands unavailable; using built-in fallback", exc_info=True)
    return BAND_THRESHOLDS


def _band_for(score: float) -> Band:
    for band, threshold in _active_bands():
        if score >= threshold:
            return band  # type: ignore[return-value]
    return "LOW"


def score(
    *,
    relevance_total: float | None,
    event_severity: float | None = None,
    industry_materiality_weight: float | None = None,
    cascade_total_cr: float | None,
    company_revenue_cr: float | None,
    event_id: str | None,
    has_deadline: bool = False,
    days_to_decision: int | None = None,
    article_embedding: list[float] | None = None,
    painpoint_embeddings: list[tuple[list[float], float]] | None = None,
    published_at: str | None = None,
    source: str | None = None,
    url: str | None = None,
    cascade_confidence: str | float | None = None,
    event_polarity: str | None = None,
    narrative_polarity: str | None = None,
    forecaster_output: dict[str, Any] | None = None,
    now: datetime | None = None,
    # Phase 46.D — LLM-inferred painpoints + article text for token-overlap
    # fallback when no curated embeddings exist for the tenant.
    inferred_painpoints: list[str] | None = None,
    article_text: str | None = None,
    # Phase 51.K — when the caller has classified the article as market
    # commentary (a non-actionable investor/stock-comparison listicle), the
    # final band is hard-capped at LOW so it can't outrank a genuine ESG
    # signal. Set by criticality_integration via signal_classifiers.
    market_commentary: bool = False,
) -> CriticalityResult:
    """Score an article for criticality. Returns a `CriticalityResult` with
    the final score, band, all components, and per-role scores.

    Pure, deterministic. No I/O except the source_authority disk read
    (cached after first call).
    """
    components = score_components(
        relevance_total=relevance_total,
        event_severity=event_severity,
        industry_materiality_weight=industry_materiality_weight,
        cascade_total_cr=cascade_total_cr,
        company_revenue_cr=company_revenue_cr,
        event_id=event_id,
        has_deadline=has_deadline,
        days_to_decision=days_to_decision,
        article_embedding=article_embedding,
        painpoint_embeddings=painpoint_embeddings,
        published_at=published_at,
        source=source,
        url=url,
        cascade_confidence=cascade_confidence,
        event_polarity=event_polarity,
        narrative_polarity=narrative_polarity,
        forecaster_output=forecaster_output,
        now=now,
        inferred_painpoints=inferred_painpoints,
        article_text=article_text,
    )

    final = _weighted_score(components, _weights_for("default"))
    band = _band_for(final)
    # Phase 51.K — market-commentary demotion. A non-actionable investor/stock-
    # comparison listicle ("X vs Y, which is a better bet?") must never outrank a
    # genuine ESG signal in the feed: hard-cap it at LOW (and drop the numeric
    # score below the MEDIUM floor so feed sorts agree). Genuine events carry an
    # actionable event_id, so signal_classifiers.is_market_commentary returns
    # False for them and this branch never fires — their score is untouched.
    if market_commentary and band != "LOW":
        medium_floor = next((t for b, t in _active_bands() if b == "MEDIUM"), 0.35)
        final = min(final, max(0.0, medium_floor - 0.01))
        band = "LOW"
    # Phase 51.F — role-based analysis DROPPED. The deck + product consume the
    # single default (materiality-led) score; per-role criticality (role_scores)
    # is no longer computed. The field stays present (empty) for back-compat.
    return CriticalityResult(
        score=final,
        band=band,
        components=components,
        role_scores={},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for i in range(n):
        x = a[i]; y = b[i]
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0 or norm_b <= 0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _parse_iso(ts: str) -> datetime | None:
    """Best-effort ISO-8601 parser. Returns None on failure."""
    if not ts:
        return None
    s = ts.strip()
    # Trim 'Z' to '+00:00' for fromisoformat
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Accept date-only
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
