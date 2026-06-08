"""Phase 51 — LLM routing health / "is Opus actually working?" check.

It is easy to miss that ``reasoning_heavy`` has SILENTLY fallen back from
Opus 4.6 to gpt-4.1 because ``OPENROUTER_API_KEY`` is unset or out of credit
(``engine/llm/keys.py``). This module logs the resolved reasoning model +
provider at startup so ops can see it at a glance, and offers an optional
1-token live ping behind ``SNOWKAP_VERIFY_OPUS=1``.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def routing_report() -> dict[str, object]:
    """Resolved reasoning_heavy model + provider. No network call."""
    from engine.llm.keys import is_using_legacy_openai
    from engine.llm.routing import resolve_model

    legacy = is_using_legacy_openai()
    reasoning = resolve_model("reasoning_heavy")
    return {
        "provider": "openai-direct" if legacy else "openrouter",
        "reasoning_heavy_model": reasoning,
        "opus_active": (not legacy) and "opus" in reasoning.lower(),
    }


def report_routing(verify: bool | None = None) -> dict[str, object]:
    """Log the LLM routing at startup; optionally ping the reasoning model once.

    When ``verify`` (or ``SNOWKAP_VERIFY_OPUS=1``) is set, make a single
    ``max_tokens=1`` call to confirm the reasoning model actually responds.
    Off by default so normal boots spend nothing.
    """
    rep = routing_report()
    if rep["opus_active"]:
        logger.info(
            "LLM routing: reasoning_heavy=%s via %s — Opus ACTIVE",
            rep["reasoning_heavy_model"], rep["provider"],
        )
    else:
        logger.warning(
            "LLM routing: reasoning_heavy=%s via %s — OPUS NOT ACTIVE; set "
            "OPENROUTER_API_KEY (with credit) to enable claude-opus-4.6",
            rep["reasoning_heavy_model"], rep["provider"],
        )

    if verify is None:
        verify = os.environ.get("SNOWKAP_VERIFY_OPUS", "").strip().lower() in {"1", "true", "yes", "on"}
    if verify:
        try:
            from engine.llm import get_llm_client
            resp = get_llm_client(task_class="reasoning_heavy").complete(
                [{"role": "user", "content": "ping"}], max_tokens=1,
            )
            rep["ping_ok"] = True
            rep["ping_model"] = getattr(resp, "model_used", "")
            logger.info("LLM verify: reasoning_heavy ping OK (model=%s)", rep["ping_model"])
        except Exception as exc:  # noqa: BLE001 — verification must never crash boot
            rep["ping_ok"] = False
            logger.warning("LLM verify: reasoning_heavy ping FAILED: %s", exc, exc_info=True)
    return rep


__all__ = ["routing_report", "report_routing"]
