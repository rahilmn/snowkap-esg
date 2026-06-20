"""Task-class → model routing.

The autoresearcher, criticality scorer, insight generator, etc. each
declare a TASK CLASS (`extraction`, `composition`, `chat`, etc.) when
they call `get_llm_client(task_class=...)`. This file resolves the
task class to a concrete OpenRouter (or OpenAI) model name.

Override precedence:
  1. explicit `override` arg to `resolve_model()`
  2. env override: SNOWKAP_REASONING_MODEL (reasoning_heavy only) / SNOWKAP_LLM_MODEL (all)
  3. `task_class` lookup in TASK_CLASS_TO_MODEL
  4. default fallback (`reasoning_default`)
"""
from __future__ import annotations

import os

from engine.llm.keys import is_using_legacy_openai

# OpenRouter model identifiers (vendor-prefixed). When OpenRouter is
# active, these strings go straight to the API. When legacy OpenAI is
# active, we strip the `vendor/` prefix and use the model bare.
TASK_CLASS_TO_MODEL: dict[str, str] = {
    "reasoning_heavy":   "anthropic/claude-opus-4.6",
    "reasoning_default": "openai/gpt-4.1",
    "extraction":        "openai/gpt-4.1-mini",
    "composition":       "openai/gpt-4.1",
    "classification":    "openai/gpt-4o-mini",
    # Phase 52 — Ask/chat on Claude Sonnet 4.6 via OpenRouter: stronger
    # conversational grounding than gpt-4.1 at ~1/3 the output cost. Falls back
    # to gpt-4.1 (legacy map below) when OpenRouter is unavailable.
    "chat":              "anthropic/claude-sonnet-4.6",
    "search_aided":      "perplexity/sonar-pro",
    "embeddings":        "openai/text-embedding-3-small",
}

# When falling back to direct OpenAI, we strip the vendor prefix
# (OpenAI's API takes bare model names).
_LEGACY_OPENAI_FALLBACK: dict[str, str] = {
    "reasoning_heavy":   "gpt-4.1",
    "reasoning_default": "gpt-4.1",
    "extraction":        "gpt-4.1-mini",
    "composition":       "gpt-4.1",
    "classification":    "gpt-4o-mini",
    "chat":              "gpt-4.1",
    "search_aided":      "gpt-4.1",
    "embeddings":        "text-embedding-3-small",
}


def resolve_model(
    task_class: str | None = None,
    override: str | None = None,
) -> str:
    """Resolve the model name to send to the API.

    Args:
        task_class: one of the keys in `TASK_CLASS_TO_MODEL`. When
            None, defaults to `reasoning_default`.
        override: explicit model name, takes precedence over task_class.

    Returns:
        Model name (with or without vendor prefix depending on whether
        OpenRouter is active).
    """
    if override:
        return override

    tc = task_class or "reasoning_default"

    # Env overrides — flip the model without a code change. Useful while
    # OpenRouter is out of credit (reasoning_heavy silently degrades to
    # gpt-4.1): pin SNOWKAP_REASONING_MODEL=gpt-4.1 to test on a capable model
    # now, then set it to anthropic/claude-opus-4.6 (+ OPENROUTER_API_KEY) once
    # credit returns — no redeploy. The caller must give a string that matches
    # the active provider (bare for OpenAI-direct, vendor/-prefixed for
    # OpenRouter), exactly as the explicit `override` arg already requires.
    if tc == "reasoning_heavy":
        env_reasoning = os.environ.get("SNOWKAP_REASONING_MODEL", "").strip()
        if env_reasoning:
            return env_reasoning
    env_all = os.environ.get("SNOWKAP_LLM_MODEL", "").strip()
    if env_all:
        return env_all

    if is_using_legacy_openai():
        return _LEGACY_OPENAI_FALLBACK.get(tc, "gpt-4.1")
    return TASK_CLASS_TO_MODEL.get(tc, TASK_CLASS_TO_MODEL["reasoning_default"])


def resolve_openai_fallback_model(
    task_class: str | None = None,
    override: str | None = None,
) -> str:
    """The DIRECT-OpenAI (bare) model for a task class.

    Used when OpenRouter is the active provider but fails (e.g. a 402
    out-of-credits) and the gateway falls back to OpenAI. A vendor-prefixed
    override (e.g. ``anthropic/claude-opus-4.6``) can't run on OpenAI, so it
    falls to the task-class default (reasoning_heavy → gpt-4.1).
    """
    if override and "/" not in override:
        return override
    tc = task_class or "reasoning_default"
    return _LEGACY_OPENAI_FALLBACK.get(tc, "gpt-4.1")
