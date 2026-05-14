"""Env-var resolution for the LLM gateway.

Precedence:
  1. `OPENROUTER_API_KEY` → use OpenRouter (preferred)
  2. `OPENAI_API_KEY` → use OpenAI directly (legacy fallback)
  3. Neither → return a placeholder so test stubs can construct a client
"""
from __future__ import annotations

import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_HTTP_REFERER_DEFAULT = "https://snowkap.com"
_X_TITLE_DEFAULT = "Snowkap ESG"


def resolve_openrouter_key() -> str:
    """Return the API key to use. Order: OPENROUTER, OPENAI, placeholder."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    return "sk-placeholder-no-key-set"


def is_using_legacy_openai() -> bool:
    """True when there's no OpenRouter key set (i.e. legacy direct-OpenAI)."""
    return not os.environ.get("OPENROUTER_API_KEY", "").strip()


def resolve_base_url() -> str | None:
    """When OpenRouter is active, return its base URL. Otherwise None
    (the OpenAI client defaults to https://api.openai.com/v1)."""
    if is_using_legacy_openai():
        return None
    return OPENROUTER_BASE_URL


def resolve_http_referer() -> str:
    return os.environ.get("OPENROUTER_HTTP_REFERER", _HTTP_REFERER_DEFAULT)


def resolve_x_title() -> str:
    return os.environ.get("OPENROUTER_X_TITLE", _X_TITLE_DEFAULT)
