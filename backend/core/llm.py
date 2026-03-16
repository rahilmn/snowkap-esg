"""LLM client — unified OpenAI interface for all AI calls.

Centralizes LLM access so swapping providers requires changing only this file.
Currently uses OpenAI GPT-4o via the openai SDK.
"""

from typing import Any

import structlog
from openai import AsyncOpenAI

from backend.core.config import settings

logger = structlog.get_logger()

# Default model for all LLM calls
DEFAULT_MODEL = "gpt-4o"
FAST_MODEL = "gpt-4o-mini"


def _get_client() -> AsyncOpenAI | None:
    """Get the OpenAI async client, or None if not configured."""
    if not settings.OPENAI_API_KEY:
        return None
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def is_configured() -> bool:
    """Check if the LLM is configured."""
    return bool(settings.OPENAI_API_KEY)


async def chat(
    messages: list[dict[str, Any]],
    system: str | None = None,
    max_tokens: int = 2000,
    model: str | None = None,
    temperature: float = 0.3,
) -> str:
    """Send a chat completion request. Returns the response text.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."}
        system: Optional system prompt (prepended as system message)
        max_tokens: Max response tokens
        model: Override model (default: gpt-4o)
        temperature: Sampling temperature
    """
    client = _get_client()
    if not client:
        raise RuntimeError("OPENAI_API_KEY not configured")

    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    response = await client.chat.completions.create(
        model=model or DEFAULT_MODEL,
        messages=full_messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    return response.choices[0].message.content or ""


async def chat_with_image(
    image_b64: str,
    media_type: str,
    prompt: str,
    max_tokens: int = 2000,
) -> str:
    """Send a vision request with a base64-encoded image."""
    client = _get_client()
    if not client:
        raise RuntimeError("OPENAI_API_KEY not configured")

    response = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{image_b64}",
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""
