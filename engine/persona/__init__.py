"""Phase 6 — Persona-driven personalization.

A persona is a structured fingerprint per user (not per company). It
modulates Criticality scoring + ingestion query generation + UI ordering
without ever filtering content (CRITICAL articles always surface).
"""
from engine.persona.persona_model import (
    Persona,
    PERSONA_QUESTIONS,
    DecisionStyle,
    Horizon,
    RiskAppetite,
    default_persona_for_role,
    deserialise_persona,
)
from engine.persona.persona_store import (
    delete_persona,
    get_persona,
    record_click_affinity,
    upsert_persona,
)
from engine.persona.persona_scorer import (
    compute_persona_boost,
    score_with_persona,
)
from engine.persona.persona_rerank import apply_persona_to_feed

__all__ = [
    "Persona",
    "PERSONA_QUESTIONS",
    "DecisionStyle",
    "Horizon",
    "RiskAppetite",
    "default_persona_for_role",
    "deserialise_persona",
    "delete_persona",
    "get_persona",
    "record_click_affinity",
    "upsert_persona",
    "compute_persona_boost",
    "score_with_persona",
    "apply_persona_to_feed",
]
