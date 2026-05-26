"""Phase 6 §8.3 — Persona × Criticality scoring.

Wraps the Phase 1 ``criticality_scorer.score()`` result with a
multiplicative persona modulator.

Persona NEVER filters — only re-ranks via ``score`` boost. CRITICAL
articles are guaranteed to surface (never below the 0.65 home-page
floor) even on a full persona mismatch. The "outside_focus" tag lets
the UI render an "Outside your focus, but high-impact" badge.

Boost rules (per the plan):

  - esg_focus overlap   → up to +40% (×1.40 max)
  - frameworks overlap  → up to +30%
  - geographies overlap → up to +25%
  - horizon mismatch    → ×0.7 (quarterly persona × long-tail cascade)
                         × 0.6 (5yr+ persona × earnings_blip event)
  - risk_appetite       → ±15% on polarity match
  - click_affinity      → up to +20% on top topic match

  Final score capped at 1.0; CRITICAL band floored at 0.65.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from engine.persona.persona_model import Persona


HOME_FLOOR = 0.65
MAX_FINAL_SCORE = 1.0
_PREFERENCE_WORD_RE = re.compile(r"[a-z0-9_]+")


@dataclass
class PersonaScoredResult:
    """Output of `score_with_persona`. Wraps a CriticalityResult.

    Defined here as a structural object (not subclassing CriticalityResult)
    so we don't depend on the criticality module at import time — keeps
    persona_scorer testable in isolation.
    """
    score: float
    band: str
    base_score: float
    persona_boost: float
    outside_focus: bool
    components: dict[str, Any]
    role_scores: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "band": self.band,
            "base_score": round(self.base_score, 4),
            "persona_boost": round(self.persona_boost, 4),
            "outside_focus": self.outside_focus,
            "components": self.components,
            "role_scores": {k: round(v, 4) for k, v in self.role_scores.items()},
        }


def _safe_overlap(article_set: set[str], persona_list: list[str]) -> float:
    """Fraction of persona items found in article tags."""
    if not persona_list:
        return 0.0
    return len(article_set & set(persona_list)) / len(persona_list)


def _memory_preference_overlap(
    tenant_id: str | None,
    user_id: str | None,
    article_topics: list[str] | None,
) -> float:
    """Phase 27 — fraction of the user's stored 'preference' memories whose
    content overlaps the article's topics on word boundaries.

    Returns 0.0 when:
      - tenant_id or article_topics is missing
      - the memory module is unavailable
      - the user has no preference memories
      - none of the preferences mention any article topic as a whole token

    Capped at 1.0 (always 0..1 range). Never raises — any error path
    collapses to 0.0 so persona scoring degrades gracefully.

    Word-boundary match: "esg" matches "ESG focus" but NOT "ESGallow".
    Multi-word topics like "supply chain" match when ALL constituent
    tokens appear in the memory's token set.
    """
    if not tenant_id or not article_topics:
        return 0.0
    try:
        from engine.memory.retrieval import retrieve_for_injection
    except Exception:  # noqa: BLE001
        return 0.0

    try:
        records = retrieve_for_injection(
            tenant_id=tenant_id,
            user_id=user_id,
            query=" ".join(str(t) for t in article_topics),
            top_n=8,
            only_kinds=["preference"],
        )
    except Exception:  # noqa: BLE001
        return 0.0

    if not records:
        return 0.0

    # Each topic → set of constituent tokens; the article matches a memory
    # when ALL tokens of at least one topic appear as whole words in that
    # memory's token set.
    topic_token_sets: list[frozenset[str]] = []
    for topic in article_topics:
        if not topic:
            continue
        toks = frozenset(_PREFERENCE_WORD_RE.findall(str(topic).lower()))
        if toks:
            topic_token_sets.append(toks)
    if not topic_token_sets:
        return 0.0

    matches = 0
    for rec in records:
        rec_tokens = set(_PREFERENCE_WORD_RE.findall((rec.content or "").lower()))
        if not rec_tokens:
            continue
        if any(topic_toks <= rec_tokens for topic_toks in topic_token_sets):
            matches += 1
    return matches / len(records)


def compute_persona_boost(
    persona: Persona,
    article_topics: list[str] | None = None,
    article_frameworks: list[str] | None = None,
    article_regions: list[str] | None = None,
    cascade_dominant_lag_months: int | None = None,
    event_type: str | None = None,
    polarity: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> tuple[float, bool]:
    """Compute the multiplicative persona boost + outside-focus flag.

    All article-side inputs are optional — missing data defaults to "no
    overlap" (boost = 1.0 for that dimension), so the function tolerates
    partially-tagged articles without crashing.

    Phase 27 — when `tenant_id` is supplied, preferences stored in
    ``tenant_memory`` for this (tenant, user) contribute up to +20% boost
    (``memory_preference_match``). Preferences raise rank; they never gate
    the feed and never flip the ``outside_focus`` flag — so a user who
    says "I track water risk" sees water articles boosted, but a CRITICAL
    governance article still surfaces via the existing 0.65 home floor.

    Returns (boost, outside_focus). `outside_focus` is True iff the
    article's topics have ZERO overlap with the persona's esg_focus —
    used to render the "Outside your focus" badge.
    """
    boost = 1.0
    topics_set = set(article_topics or [])
    fw_set = set(article_frameworks or [])
    geo_set = set(article_regions or [])

    # ESG focus → up to +40%
    focus_overlap = _safe_overlap(topics_set, persona.esg_focus)
    boost *= 1.0 + 0.4 * focus_overlap

    # Framework match → up to +30%
    fw_overlap = _safe_overlap(fw_set, persona.frameworks)
    boost *= 1.0 + 0.3 * fw_overlap

    # Geography match → up to +25%
    geo_overlap = _safe_overlap(geo_set, persona.geographies)
    boost *= 1.0 + 0.25 * geo_overlap

    # Horizon penalties
    if (
        persona.horizon == "quarterly"
        and cascade_dominant_lag_months is not None
        and cascade_dominant_lag_months > 12
    ):
        boost *= 0.7
    if persona.horizon == "5yr_plus" and event_type == "earnings_blip":
        boost *= 0.6

    # Risk appetite × polarity ±15%
    if persona.risk_appetite == "opportunistic" and polarity == "positive":
        boost *= 1.15
    if persona.risk_appetite == "defensive" and polarity == "negative":
        boost *= 1.15

    # Click affinity on top topic — up to +20%
    if article_topics:
        top_topic = article_topics[0]
        if top_topic in persona.click_affinity:
            click = persona.click_affinity[top_topic]
            boost *= 1.0 + 0.2 * max(0.0, min(1.0, click))

    # Phase 27 — memory preference match → up to +20%
    mem_overlap = _memory_preference_overlap(tenant_id, user_id, article_topics)
    if mem_overlap > 0:
        boost *= 1.0 + 0.2 * mem_overlap

    outside_focus = persona.esg_focus and len(topics_set & set(persona.esg_focus)) == 0
    return boost, bool(outside_focus)


def score_with_persona(
    base_result: Any,
    persona: Persona,
    article_topics: list[str] | None = None,
    article_frameworks: list[str] | None = None,
    article_regions: list[str] | None = None,
    cascade_dominant_lag_months: int | None = None,
    event_type: str | None = None,
    polarity: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> PersonaScoredResult:
    """Apply persona modulation on top of a Phase 1 CriticalityResult.

    `base_result` is structurally typed: must expose `.score` (float),
    `.band` (str), `.components` (dataclass with `.as_dict()` or dict),
    and `.role_scores` (dict). Real callers pass an
    `engine.analysis.criticality_scorer.CriticalityResult`; tests can
    pass a duck-typed stub.

    Phase 27 — `tenant_id` / `user_id` (optional) enable the
    memory-preference-match boost; omitted by default to keep the
    function testable in isolation.
    """
    base_score = float(getattr(base_result, "score", 0.0))
    band = str(getattr(base_result, "band", "LOW"))

    boost, outside_focus = compute_persona_boost(
        persona,
        article_topics=article_topics,
        article_frameworks=article_frameworks,
        article_regions=article_regions,
        cascade_dominant_lag_months=cascade_dominant_lag_months,
        event_type=event_type,
        polarity=polarity,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    raw_final = base_score * boost
    final_score = min(MAX_FINAL_SCORE, raw_final)

    # CRITICAL guarantee — never drag a CRITICAL below the home-page floor
    if band == "CRITICAL":
        final_score = max(final_score, HOME_FLOOR)

    components_dict: dict[str, Any] = {}
    base_components = getattr(base_result, "components", None)
    if base_components is not None:
        if hasattr(base_components, "as_dict"):
            components_dict = dict(base_components.as_dict())
        elif isinstance(base_components, dict):
            components_dict = dict(base_components)

    return PersonaScoredResult(
        score=final_score,
        band=band,
        base_score=base_score,
        persona_boost=boost,
        outside_focus=outside_focus,
        components=components_dict,
        role_scores=dict(getattr(base_result, "role_scores", {}) or {}),
    )
