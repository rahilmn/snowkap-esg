"""Phase 3 §5.3 — Optional LLM upgrade for role generators.

Provides three small functions (`llm_cfo`, `llm_ceo`, `llm_analyst`)
that take an EvidencePack + recommendations and return polished
narrative fields for the corresponding RoleDistinctPayload.

Used by the deterministic generators as an OPTIONAL upgrade path:
  1. Deterministic baseline fills the payload with structured data
  2. If `SNOWKAP_LLM_ROLE_GENERATORS=1` AND the OpenAI key is present,
     the LLM is called to refine `headline`, `role_takeaways`, and
     `role_paragraph` ONLY. Hero metric, panel order, recommendations
     stay deterministic (locked-contract fields the LLM shouldn't touch).
  3. On ANY failure (no key, API error, malformed JSON) → silently fall
     back to the deterministic version. Never blocks the article write.

This keeps tests deterministic by default (env flag off) and lets a
production deployment turn LLM polish on with one env var flip.

Cost ceiling: 3 calls × ~600 tokens × gpt-4.1 ≈ $0.04 per article when
enabled. Cached per article_id by the on-demand pipeline so re-opens
don't re-spend.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from engine.analysis.evidence_pack import EvidencePack
from engine.analysis.role_generators.types import RecommendationStub

logger = logging.getLogger(__name__)


_FLAG_ENV = "SNOWKAP_LLM_ROLE_GENERATORS"
_MODEL = "gpt-4.1-mini"  # Cheap polish; the heavy lifting already happened in Stage 10
_MAX_TOKENS = 800


def llm_upgrade_enabled() -> bool:
    """True iff the env flag is set AND we have an OpenAI key.

    Default-off so the deterministic baseline ships in all environments
    that don't explicitly opt in.
    """
    if os.environ.get(_FLAG_ENV, "0").strip() != "1":
        return False
    try:
        from engine.config import get_openai_api_key
        return bool(get_openai_api_key())
    except Exception:  # noqa: BLE001
        return False


def _build_user_message(pack: EvidencePack, role_hint: str) -> str:
    """Compose a structured prompt body from the EvidencePack.

    Pure JSON-friendly serialization — every field maps to one of the
    canonical EvidencePack sub-dataclasses. The role-specific system
    prompt determines how the LLM uses these inputs."""
    fws = ", ".join(f.code for f in pack.frameworks[:5]) or "—"
    stakeholders = ", ".join(
        f"{s.name}({s.stance})" for s in pack.stakeholders[:5]
    ) or "—"
    peers = ", ".join(p.company for p in pack.comparables[:3]) or "—"
    deadlines = "; ".join(
        f"{d.label}: {d.deadline}" for d in pack.decision_windows[:3]
    ) or "—"
    cascade = pack.cascade
    return (
        f"ROLE: {role_hint}\n"
        f"POLARITY: {pack.polarity}\n"
        f"CASCADE_TOTAL_CR: {cascade.total_cr}\n"
        f"CASCADE_MARGIN_BPS: {cascade.margin_bps or 'n/a'}\n"
        f"FRAMEWORK_HITS: {fws}\n"
        f"STAKEHOLDERS: {stakeholders}\n"
        f"PEER_COMPARABLES: {peers}\n"
        f"CAUSAL_CHAIN: {pack.causal_chain.relationship_type or 'n/a'} "
        f"({pack.causal_chain.hops}-hop)\n"
        f"DECISION_WINDOWS: {deadlines}\n"
        f"CONFIDENCE_METHOD: {pack.confidence_bounds.method or 'unverified'}"
    )


_CFO_SYSTEM = (
    "You are a CFO communications copywriter. Lead every sentence with a "
    "₹ figure, a peer name, or an action verb with payback. Never write "
    "strategic positioning, 3-year horizons, governance philosophy, or "
    "comms tasks. Use 2-significant-figure rounding (e.g. ₹1,900 Cr not "
    "₹1,857.6 Cr). Output JSON only with keys: headline (≤90 chars), "
    "role_takeaways (3 bullets, each ≤30 words), role_paragraph (≤90 words)."
)
_CEO_SYSTEM = (
    "You are a CEO board-narrative copywriter. NEVER lead with a ₹ figure. "
    "Lead with competitive positioning, stakeholder signal, or strategic "
    "optionality. Frame on a 3-year horizon. Reference at least one peer "
    "event matching the article's polarity. Output JSON only with keys: "
    "headline (≤90 chars, no ₹), role_takeaways (3 bullets), "
    "role_paragraph (≤80 words, ZERO ₹ figures)."
)
_ANALYST_SYSTEM = (
    "You are an ESG Analyst writer. Every material claim must cite a "
    "framework section code. Surface confidence bounds (β, lag, method) on "
    "every quantitative claim. Flag unverified claims with [unverified]. "
    "Output JSON only with keys: headline (≤90 chars, lead with framework "
    "code when present), role_takeaways (3-5 bullets), role_paragraph "
    "(≤100 words)."
)


def _call_llm(
    system_prompt: str, user_message: str
) -> dict[str, Any] | None:
    """Single OpenAI call. Returns parsed JSON or None on any failure."""
    try:
        from engine.llm import get_llm_client

        client = get_llm_client(task_class="composition").sync
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        from engine.models.llm_calls import log_openai_usage
        log_openai_usage(resp, model=_MODEL, stage="role_upgrade")
        raw = resp.choices[0].message.content or ""
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — never block on LLM failure
        logger.debug("LLM role-generator call failed: %s", exc)
        return None


def llm_polish(
    pack: EvidencePack,
    role: str,
) -> dict[str, str | list[str]] | None:
    """Polished {headline, role_takeaways, role_paragraph} or None.

    Caller checks `llm_upgrade_enabled()` first. None return means the
    LLM call failed for any reason — caller falls back to deterministic.
    """
    role_lower = (role or "").lower()
    if role_lower == "cfo":
        sysprompt = _CFO_SYSTEM
    elif role_lower == "ceo":
        sysprompt = _CEO_SYSTEM
    elif role_lower in ("analyst", "esg-analyst", "esg_analyst"):
        sysprompt = _ANALYST_SYSTEM
    else:
        return None

    user = _build_user_message(pack, role_lower)
    parsed = _call_llm(sysprompt, user)
    if not isinstance(parsed, dict):
        return None

    # Defensive shape coercion — if any required key is missing or the
    # wrong type, fall back. The deterministic baseline already covers
    # every field shape correctly; we only swap when the LLM clearly
    # produced what we asked for.
    headline = parsed.get("headline")
    takeaways = parsed.get("role_takeaways")
    paragraph = parsed.get("role_paragraph")
    if not (
        isinstance(headline, str) and headline
        and isinstance(takeaways, list) and takeaways
        and isinstance(paragraph, str) and paragraph
    ):
        return None
    return {
        "headline": headline,
        "role_takeaways": [str(t) for t in takeaways if t],
        "role_paragraph": paragraph,
    }


def maybe_apply_llm_polish(
    pack: EvidencePack,
    role: str,
    deterministic_fields: dict[str, Any],
) -> dict[str, Any]:
    """Returns either the LLM-polished fields or the deterministic ones.

    Keeps the contract narrow: only headline/role_takeaways/role_paragraph
    are eligible for replacement. Hero metric, panels, recs stay
    deterministic (the locked-contract fields).
    """
    if not llm_upgrade_enabled():
        return deterministic_fields
    # Defensive outer try — catches any failure path inside llm_polish
    # (network, parse, contract mismatch). Falls back to deterministic
    # rather than raising into the article-write code path.
    try:
        polished = llm_polish(pack, role)
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_polish raised, falling back: %s", exc)
        polished = None
    if polished is None:
        return deterministic_fields
    out = dict(deterministic_fields)
    out["headline"] = polished["headline"]
    out["role_takeaways"] = polished["role_takeaways"]
    out["role_paragraph"] = polished["role_paragraph"]
    return out


# unused import removed — RecommendationStub kept here for downstream
# import-stability if a caller wants the whole module surface.
__all__ = [
    "llm_upgrade_enabled",
    "llm_polish",
    "maybe_apply_llm_polish",
    "RecommendationStub",
]
