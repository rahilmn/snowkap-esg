"""LLM client — unified OpenAI interface with production resilience.

Centralizes LLM access with:
- Retry with exponential backoff (3 attempts)
- Per-call timeout (30s default, configurable)
- Rate limit (429) handling
- Centralized JSON parsing helper
- Token budget logging
"""

import asyncio
import json
import time
from typing import Any

import structlog
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from backend.core.config import settings

logger = structlog.get_logger()

# Default model for all LLM calls
DEFAULT_MODEL = "gpt-4o"
FAST_MODEL = "gpt-4o-mini"

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds, doubles each retry
DEFAULT_TIMEOUT = 30.0  # seconds per call
LONG_TIMEOUT = 60.0     # for deep insight, REREACT

# Client singleton
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    """Get the OpenAI async client, or None if not configured."""
    global _client
    if not settings.OPENAI_API_KEY:
        return None
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=DEFAULT_TIMEOUT,
            max_retries=0,  # We handle retries ourselves
        )
    return _client


def is_configured() -> bool:
    """Check if the LLM is configured."""
    return bool(settings.OPENAI_API_KEY)


async def chat(
    messages: list[dict[str, Any]],
    system: str | None = None,
    max_tokens: int = 2000,
    model: str | None = None,
    temperature: float = 0.3,
    timeout: float | None = None,
) -> str:
    """Send a chat completion request with retry and timeout.

    Args:
        messages: List of {"role": "user"|"assistant", "content": "..."}
        system: Optional system prompt (prepended as system message)
        max_tokens: Max response tokens
        model: Override model (default: gpt-4o)
        temperature: Sampling temperature
        timeout: Per-call timeout in seconds (default: 30s)

    Raises:
        RuntimeError: If OPENAI_API_KEY not configured
        Exception: After all retries exhausted
    """
    client = _get_client()
    if not client:
        raise RuntimeError("OPENAI_API_KEY not configured")

    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    use_model = model or DEFAULT_MODEL
    call_timeout = timeout or DEFAULT_TIMEOUT
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=use_model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=call_timeout,
            )
            elapsed = time.monotonic() - start
            result = response.choices[0].message.content or ""

            # Log token usage for cost tracking
            usage = response.usage
            if usage:
                logger.debug(
                    "llm_call_ok",
                    model=use_model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    elapsed_s=round(elapsed, 2),
                    attempt=attempt,
                )
            return result

        except RateLimitError as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "llm_rate_limited",
                model=use_model, attempt=attempt,
                retry_after=delay, error=str(e),
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)

        except (APITimeoutError, asyncio.TimeoutError) as e:
            last_error = e
            elapsed = time.monotonic() - start
            logger.warning(
                "llm_timeout",
                model=use_model, attempt=attempt,
                timeout_s=call_timeout, elapsed_s=round(elapsed, 2),
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BASE_DELAY)

        except APIError as e:
            last_error = e
            # Retry on 5xx server errors
            if hasattr(e, "status_code") and e.status_code and e.status_code >= 500:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "llm_server_error",
                    model=use_model, attempt=attempt,
                    status=e.status_code, retry_after=delay,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
            else:
                raise  # 4xx errors are not retryable

    # All retries exhausted
    logger.error(
        "llm_all_retries_failed",
        model=use_model, attempts=MAX_RETRIES,
        error=str(last_error),
    )
    raise last_error or RuntimeError("LLM call failed after all retries")


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

    response = await asyncio.wait_for(
        client.chat.completions.create(
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
        ),
        timeout=LONG_TIMEOUT,
    )

    return response.choices[0].message.content or ""


def parse_json_response(raw: str) -> dict | list | None:
    """Centralized JSON parser for LLM responses.

    Handles: markdown code fences, preamble text before JSON,
    trailing text after JSON, and common LLM formatting quirks.
    Returns None if parsing fails.
    """
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Extract JSON object/array if LLM included surrounding text
    if not text.startswith(("{", "[")):
        for start_char in ("{", "["):
            idx = text.find(start_char)
            if idx >= 0:
                text = text[idx:]
                break

    end_char = "}" if text.startswith("{") else "]" if text.startswith("[") else None
    if end_char and not text.endswith(end_char):
        idx = text.rfind(end_char)
        if idx >= 0:
            text = text[:idx + 1]

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_json_parse_failed", raw_preview=raw[:200])
        return None
