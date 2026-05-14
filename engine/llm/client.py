"""OpenRouterClient — sync + async + streaming LLM gateway.

Wraps the OpenAI SDK with two transports:
  - When OPENROUTER_API_KEY is set: base_url → OpenRouter, model strings
    are vendor-prefixed (e.g. `anthropic/claude-opus-4.7`)
  - Otherwise: base_url → OpenAI directly, model strings are bare
    (e.g. `gpt-4.1`). Byte-equivalent to the legacy code path so Phase 26
    tests keep passing without modification.

Public surface:
  - `get_llm_client(task_class=None, model=None) -> OpenRouterClient`
  - `OpenRouterClient.complete(messages, **kwargs) -> LLMResponse`
  - `OpenRouterClient.acomplete(messages, **kwargs) -> LLMResponse`  (async)
  - `OpenRouterClient.stream(messages, **kwargs) -> Iterator[TokenEvent]`
  - `OpenRouterClient.astream(messages, **kwargs) -> AsyncIterator[TokenEvent]`

Cost handling: when OpenRouter returns `usage.cost`, we surface it on
`LLMResponse.cost_usd`. When absent, the cost-tracking module in
`engine/models/llm_calls.py` will fall through to its hardcoded
estimate (unchanged from Phase 26).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

from engine.llm.cost import parse_cost
from engine.llm.keys import (
    is_using_legacy_openai,
    resolve_base_url,
    resolve_http_referer,
    resolve_openrouter_key,
    resolve_x_title,
)
from engine.llm.routing import resolve_model


@dataclass
class LLMResponse:
    """Result of a single non-streaming completion."""
    text: str
    model_used: str
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    provider_route: str = "openrouter"
    finish_reason: str = ""
    raw: Any = None

    @property
    def cost_usd(self) -> float | None:
        """Authoritative cost from OpenRouter, or None when unavailable."""
        return parse_cost(self.usage)


@dataclass
class TokenEvent:
    """One token chunk from a streaming completion."""
    delta: str
    model: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    raw: Any = None


class OpenRouterClient:
    """Thin wrapper over `openai.OpenAI` + `openai.AsyncOpenAI`.

    Constructor caches both sync and async clients; methods route to
    whichever is needed.
    """

    def __init__(
        self,
        *,
        task_class: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        sync_client: Any = None,
        async_client: Any = None,
    ):
        self.task_class = task_class
        self._model_override = model
        self.timeout = timeout
        self._sync = sync_client  # injectable for tests
        self._async = async_client

        # Default headers so OpenRouter knows who's calling
        self._extra_headers = {
            "HTTP-Referer": resolve_http_referer(),
            "X-Title": resolve_x_title(),
        }

    # ------------------------------------------------------------------
    # Client construction (lazy, injectable)
    # ------------------------------------------------------------------

    def _build_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "api_key": resolve_openrouter_key(),
            "timeout": self.timeout,
        }
        base = resolve_base_url()
        if base:
            kwargs["base_url"] = base
        return kwargs

    @property
    def sync(self) -> Any:
        if self._sync is None:
            from openai import OpenAI
            self._sync = OpenAI(**self._build_kwargs())
        return self._sync

    @property
    def async_client(self) -> Any:
        if self._async is None:
            from openai import AsyncOpenAI
            self._async = AsyncOpenAI(**self._build_kwargs())
        return self._async

    def model_for(self, override: str | None = None) -> str:
        return resolve_model(
            task_class=self.task_class,
            override=override or self._model_override,
        )

    @property
    def provider_route(self) -> str:
        return "openai-direct" if is_using_legacy_openai() else "openrouter"

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        **extra: Any,
    ) -> LLMResponse:
        """Synchronous non-streaming completion."""
        kwargs: dict[str, Any] = {
            "model": self.model_for(model),
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        # OpenRouter requires the routing headers
        kwargs["extra_headers"] = {**self._extra_headers, **extra.pop("extra_headers", {})}
        kwargs.update(extra)

        t0 = time.perf_counter()
        completion = self.sync.chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000

        return self._build_response(completion, kwargs["model"], latency_ms)

    async def acomplete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        **extra: Any,
    ) -> LLMResponse:
        """Async non-streaming completion."""
        kwargs: dict[str, Any] = {
            "model": self.model_for(model),
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        kwargs["extra_headers"] = {**self._extra_headers, **extra.pop("extra_headers", {})}
        kwargs.update(extra)

        t0 = time.perf_counter()
        completion = await self.async_client.chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._build_response(completion, kwargs["model"], latency_ms)

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> Iterator[TokenEvent]:
        """Synchronous streaming. Yields TokenEvent per chunk."""
        kwargs: dict[str, Any] = {
            "model": self.model_for(model),
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs["extra_headers"] = {**self._extra_headers, **extra.pop("extra_headers", {})}
        kwargs.update(extra)

        stream = self.sync.chat.completions.create(**kwargs)
        for chunk in stream:
            yield self._build_token_event(chunk, kwargs["model"])

    async def astream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> AsyncIterator[TokenEvent]:
        """Async streaming. Yields TokenEvent per chunk."""
        kwargs: dict[str, Any] = {
            "model": self.model_for(model),
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs["extra_headers"] = {**self._extra_headers, **extra.pop("extra_headers", {})}
        kwargs.update(extra)

        stream = await self.async_client.chat.completions.create(**kwargs)
        async for chunk in stream:
            yield self._build_token_event(chunk, kwargs["model"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_response(self, completion: Any, model: str, latency_ms: float) -> LLMResponse:
        try:
            choice = completion.choices[0]
            text = choice.message.content or ""
            finish_reason = choice.finish_reason or ""
        except (AttributeError, IndexError):
            text, finish_reason = "", ""

        usage = self._extract_usage(completion)
        return LLMResponse(
            text=text,
            model_used=model,
            usage=usage,
            latency_ms=latency_ms,
            provider_route=self.provider_route,
            finish_reason=finish_reason,
            raw=completion,
        )

    def _build_token_event(self, chunk: Any, model: str) -> TokenEvent:
        try:
            delta_obj = chunk.choices[0].delta
            delta = getattr(delta_obj, "content", "") or ""
            finish_reason = chunk.choices[0].finish_reason
        except (AttributeError, IndexError):
            delta, finish_reason = "", None
        usage = self._extract_usage(chunk)
        return TokenEvent(
            delta=delta,
            model=model,
            finish_reason=finish_reason,
            usage=usage,
            raw=chunk,
        )

    def _extract_usage(self, completion: Any) -> dict[str, Any]:
        usage = getattr(completion, "usage", None)
        if usage is None:
            return {}
        if isinstance(usage, dict):
            return usage
        # Pydantic model — pull common fields
        out: dict[str, Any] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost", "cost_usd"):
            v = getattr(usage, key, None)
            if v is not None:
                out[key] = v
        return out


def get_llm_client(
    task_class: str | None = None,
    model: str | None = None,
    *,
    timeout: float = 120.0,
) -> OpenRouterClient:
    """Single entry point for all engine code that needs an LLM client.

    Replaces the scattered `client = OpenAI(api_key=...)` pattern.
    """
    return OpenRouterClient(task_class=task_class, model=model, timeout=timeout)
