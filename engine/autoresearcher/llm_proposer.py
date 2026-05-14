"""LLM-driven knob proposer — replaces the deterministic random walk
with gpt-4.1-mini smart proposals.

Env-flag gated: `SNOWKAP_AUTORESEARCHER_LLM_PROPOSER=1` enables it.
Default is OFF — the deterministic experimenter is the v1 baseline.

Same fall-back pattern as `engine.governance.llm_belief_refiner`:
on ANY failure (API error, malformed JSON, schema violation, etc.)
the proposer returns None and the loop falls back to the deterministic
experimenter. So the LLM proposer is purely additive — it can only
improve proposal quality, never degrade it.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from engine.autoresearcher.experimenter import Proposal
from engine.autoresearcher.knobs import Knob
from engine.autoresearcher.ontology_introspector import KnobRegistry

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """You are an ML calibration researcher proposing one
knob change at a time to improve a Snowkap-ESG prediction-system
calibration metric.

You will see:
  - The list of available knobs with their kind, current value, baseline,
    and magnitude bound
  - Recent experiment history (kept vs discarded)

Your job: propose ONE knob change that you believe will improve the
composite calibration metric. The change MUST be within the knob's
magnitude bound.

OUTPUT JSON ONLY:
{
  "knob_id": "<exact knob_id from the registry>",
  "new_value": <numeric value, or null for set-valued knobs>,
  "rationale": "<one-line explanation>"
}
"""


def is_llm_proposer_enabled() -> bool:
    return os.environ.get(
        "SNOWKAP_AUTORESEARCHER_LLM_PROPOSER", "0",
    ).strip() == "1"


def _knob_summary(knob: Knob) -> dict[str, Any]:
    return {
        "knob_id": knob.knob_id,
        "kind": knob.kind,
        "current": knob.current_value() if knob.kind != "keyword_set_membership"
                   and knob.kind != "set_membership" else None,
        "baseline": knob.baseline_value() if knob.kind not in
                    ("keyword_set_membership", "set_membership") else None,
        "magnitude_bound": knob.magnitude_bound(),
    }


def _build_user_prompt(
    registry: KnobRegistry,
    recent_ledger: list[dict[str, Any]] | None = None,
    max_knobs: int = 50,
) -> str:
    """Render the user prompt — cap knob count to keep tokens bounded."""
    knobs = registry.knobs[:max_knobs]
    return (
        f"AVAILABLE KNOBS ({len(knobs)} of {len(registry.knobs)} shown):\n"
        + json.dumps([_knob_summary(k) for k in knobs], indent=2)
        + "\n\nRECENT EXPERIMENT HISTORY ("
        + str(len(recent_ledger or []))
        + " entries):\n"
        + json.dumps(recent_ledger or [], indent=2)[:2000]
        + "\n\nPropose ONE knob change as JSON only."
    )


def _parse_response(
    raw: str,
    registry: KnobRegistry,
) -> Proposal | None:
    """Parse JSON → Proposal. Returns None on any schema violation."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("llm_proposer: malformed JSON: %.200s", raw)
        return None
    if not isinstance(parsed, dict):
        return None
    knob_id = parsed.get("knob_id")
    new_value = parsed.get("new_value")
    rationale = parsed.get("rationale") or "LLM proposal"
    if not knob_id or not isinstance(knob_id, str):
        return None

    # Find the matching knob in the registry
    knob = next((k for k in registry.knobs if k.knob_id == knob_id), None)
    if knob is None:
        return None

    return Proposal(knob=knob, new_value=new_value, rationale=str(rationale)[:200])


def llm_propose(
    *,
    registry: KnobRegistry,
    recent_ledger: list[dict[str, Any]] | None = None,
    model: str = "gpt-4.1-mini",
    temperature: float = 0.1,
    max_tokens: int = 400,
    client: Any = None,
) -> Proposal | None:
    """LLM-driven proposal. Returns None on any failure (caller falls
    back to deterministic experimenter)."""
    if not is_llm_proposer_enabled():
        return None
    if not registry.knobs:
        return None

    if client is None:
        try:
            from engine.llm import get_llm_client
            client = get_llm_client(task_class="reasoning_default").sync
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm_proposer: client init failed: %s", exc)
            return None

    user_prompt = _build_user_prompt(registry, recent_ledger=recent_ledger)
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_proposer: API call failed: %s", exc)
        return None

    try:
        raw = completion.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return None

    return _parse_response(raw, registry)
