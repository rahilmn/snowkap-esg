"""L7 — LLM callback for belief revision.

Plugs into `belief_revision.revise_from_article(llm_callback=...)` to
refine the deterministic R1-R4 proposals using gpt-4.1-mini in JSON
mode.

Contract:
  - Receives the deterministic proposals + the same context dict that
    drove them
  - Asks the LLM to (a) confirm / drop / modify each proposal, and
    (b) optionally propose NEW beliefs the deterministic rules missed
  - Returns the refined list of BeliefProposal objects

Failure modes (all fall through to deterministic baseline):
  - OpenAI API error / timeout
  - Malformed JSON response
  - Schema violation (LLM emits unknown belief kind or out-of-enum band)
  - Empty / negative-confidence claims

The deterministic baseline ALWAYS wins on type safety: any LLM proposal
that fails to construct a valid `TypedBelief` is silently dropped, the
matching deterministic proposal kept.

Prompt quality is the long pole. The current prompt is a baseline; refine
iteratively against the fuzz corpus.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from engine.governance.belief_revision import BeliefProposal
from engine.governance.belief_schema import (
    FinancialExposureBelief,
    FYCascadeSnapshotBelief,
    FrameworkComplianceBelief,
    PainpointSeverityBelief,
    RiskBandBelief,
    TransitionStanceBelief,
    TypedBelief,
)

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are an ESG analyst reviewing an algorithm's preliminary belief
proposals about a listed company. The algorithm has applied 4
deterministic rules to flag what it thinks should be updated; your job
is to inspect the article + cascade context and decide whether each
proposal is correct, needs adjustment, or should be dropped.

The 6 belief kinds you can emit:

  1. risk_band                 — { topic: str, band: LOW|MODERATE|HIGH|CRITICAL }
  2. financial_exposure        — { scenario: str, exposure_cr_lo: float,
                                   exposure_cr_hi: float, method: str }
  3. transition_stance         — { stance: leader|fast_follower|compliant|
                                   lagging|regressing, horizon_fy: str }
  4. framework_compliance      — { framework_id: str, status: compliant|in_progress|
                                   gap_identified|non_compliant|not_applicable,
                                   deadline: str }
  5. painpoint_severity        — { painpoint_topic: str, severity: float in [0,1] }
  6. fy_cascade_snapshot       — { fy: str, primitive: str, delta_cr: float,
                                   base_value_cr: float, method: str }

You can ALSO drop a deterministic proposal entirely if it's a false
positive (e.g. R1 fired on a misclassified event polarity).

Confidence bands: low | moderate | high (NEVER 'unverified' — those
route to a separate advisor queue).

OUTPUT JSON ONLY. Schema:
{
  "proposals": [
    {
      "kind": "<belief_kind>",
      "payload": { ...belief-kind-specific fields... },
      "confidence_band": "low|moderate|high",
      "rationale": "<one-line explanation>",
      "rule_id": "LLM"
    }
  ]
}

If the deterministic proposals are correct as-is, return them unchanged
(same kind / payload) with rule_id="LLM" to mark you reviewed.
"""


def _belief_to_prompt_dict(belief: TypedBelief) -> dict[str, Any]:
    """Render a TypedBelief into the prompt's flat-payload shape."""
    payload = belief.to_dict()
    # Strip the dataclass shell keys; only the kind-specific fields matter
    for k in ("kind", "confidence_band", "last_evidence", "updated_at"):
        payload.pop(k, None)
    return {
        "kind": belief.kind,
        "payload": payload,
        "confidence_band": belief.confidence_band,
    }


def _build_user_prompt(
    proposals: list[BeliefProposal],
    context: dict[str, Any],
) -> str:
    """Render the full user prompt: context + deterministic proposals."""
    article = context.get("article") or {}
    cascade = context.get("cascade_result") or {}
    advisor = context.get("advisor_events") or []
    revenue = context.get("company_revenue_cr") or 0

    det_blob = [
        {
            "rule_id": p.rule_id,
            "rationale": p.rationale,
            **_belief_to_prompt_dict(p.belief),
        }
        for p in proposals
    ]

    article_summary = {
        "id": article.get("id"),
        "event_id": article.get("event_id"),
        "event_polarity": article.get("event_polarity"),
        "materiality": article.get("materiality")
            or (article.get("decision_summary") or {}).get("materiality"),
        "topic": article.get("topic") or article.get("primary_theme"),
        "title": article.get("title"),
        "summary": (article.get("summary") or "")[:500],
    }

    return (
        "ARTICLE CONTEXT:\n"
        + json.dumps(article_summary, indent=2)
        + "\n\nCASCADE RESULT:\n"
        + json.dumps(cascade, indent=2)
        + f"\n\nCOMPANY REVENUE (₹ Cr): {revenue}"
        + f"\n\nRECENT ADVISOR EVENTS (last 7d, count): {len(advisor)}"
        + "\n\nDETERMINISTIC PROPOSALS TO REVIEW:\n"
        + json.dumps(det_blob, indent=2)
        + "\n\nRespond with the refined `proposals` JSON only."
    )


_KIND_TO_CLS: dict[str, type[TypedBelief]] = {
    "risk_band": RiskBandBelief,
    "financial_exposure": FinancialExposureBelief,
    "transition_stance": TransitionStanceBelief,
    "framework_compliance": FrameworkComplianceBelief,
    "painpoint_severity": PainpointSeverityBelief,
    "fy_cascade_snapshot": FYCascadeSnapshotBelief,
}


def _parse_response(raw: str) -> list[BeliefProposal] | None:
    """Parse the LLM JSON response into BeliefProposal objects.

    Returns None on parse failure (caller falls back to deterministic).
    Skips individual proposals that fail to construct (silently drops
    schema-violating entries rather than failing the whole list).
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("llm_belief_refiner: malformed JSON: %s", raw[:200])
        return None

    items = parsed.get("proposals") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return None

    out: list[BeliefProposal] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        cls = _KIND_TO_CLS.get(kind)
        if cls is None:
            continue
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        confidence = item.get("confidence_band", "moderate")
        rationale = item.get("rationale", "")
        rule_id = item.get("rule_id", "LLM")
        try:
            belief = cls(**payload, confidence_band=confidence)
        except (TypeError, ValueError):
            continue
        out.append(BeliefProposal(
            belief=belief,
            rationale=rationale,
            rule_id=rule_id,
        ))
    return out


def openai_belief_refiner(
    proposals: list[BeliefProposal],
    context: dict[str, Any],
    *,
    model: str = "gpt-4.1-mini",
    temperature: float = 0.1,
    max_tokens: int = 1200,
    client: Any = None,
) -> list[BeliefProposal]:
    """LLM-driven refinement callback for `belief_revision.revise_from_article`.

    Drop-in for `llm_callback=`. Falls back to the input `proposals`
    on ANY failure (API error, timeout, malformed JSON, schema violation).
    The `client` arg lets tests inject a stub OpenAI client; production
    leaves it None and the function constructs one.
    """
    if not proposals and not context.get("article"):
        return proposals  # nothing to refine + no article = no work

    if client is None:
        try:
            from engine.llm import get_llm_client
            client = get_llm_client(task_class="reasoning_default").sync
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm_belief_refiner: client init failed: %s", exc)
            return proposals

    user_prompt = _build_user_prompt(proposals, context)
    try:
        completion = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — any LLM failure → fallback
        logger.warning("llm_belief_refiner: API call failed: %s", exc)
        return proposals

    try:
        raw = completion.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return proposals

    refined = _parse_response(raw)
    if refined is None:
        return proposals
    return refined
