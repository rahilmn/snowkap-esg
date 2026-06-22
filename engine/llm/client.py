"""OpenRouterClient — sync + async + streaming LLM gateway.

Wraps the OpenAI SDK with two transports:
  - When OPENROUTER_API_KEY is set: base_url → OpenRouter, model strings
    are vendor-prefixed (e.g. `anthropic/claude-opus-4.6`)
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

import logging
import os
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reasoning-model parameter normalisation (OpenAI gpt-5*, o-series)
# ---------------------------------------------------------------------------
# OpenAI reasoning models reject `max_tokens` (require `max_completion_tokens`)
# and reject a non-default `temperature`; they also spend hidden reasoning tokens
# before emitting output, so the completion budget needs headroom or the visible
# answer comes back empty/truncated. Non-reasoning models (gpt-4.1, gpt-4o, the
# OpenRouter Claude models) are left byte-identical to before.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str | None) -> bool:
    m = (model or "").lower().split("/")[-1]
    return m.startswith(_REASONING_PREFIXES)


def _normalize_params_for_model(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Translate chat-completion params for OpenAI reasoning models. No-op for
    every other model, so existing gpt-4.1 / Claude calls are unchanged."""
    if not _is_reasoning_model(kwargs.get("model")):
        return kwargs
    kwargs.pop("temperature", None)  # reasoning models only allow the default (1)
    mt = kwargs.pop("max_tokens", None)
    if "max_completion_tokens" not in kwargs:  # idempotent — don't recompute
        try:
            buf = int(os.environ.get("SNOWKAP_REASONING_TOKEN_BUFFER", "") or 8000)
        except ValueError:
            buf = 8000
        # max_completion_tokens is a CEILING (billed on actual usage), so a generous
        # buffer just prevents reasoning from starving the visible output.
        kwargs["max_completion_tokens"] = (int(mt) if mt else 0) + buf
    kwargs.setdefault("reasoning_effort", os.environ.get("SNOWKAP_REASONING_EFFORT", "").strip() or "low")
    return kwargs


class _NormalizingCompletions:
    """Proxy that normalises reasoning-model params on .create()."""
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def create(self, **kwargs: Any) -> Any:
        return self._inner.create(**_normalize_params_for_model(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_inner"], name)


class _NormalizingChat:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def completions(self) -> "_NormalizingCompletions":
        return _NormalizingCompletions(self._inner.completions)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_inner"], name)


class _NormalizingClientProxy:
    """Wraps an OpenAI/AsyncOpenAI client so ``.chat.completions.create()``
    translates params for OpenAI reasoning models (gpt-5*, o-series). This is
    the single choke point that covers the ~19 stages which call the raw SDK
    directly (``client = llm.sync``) instead of the gateway's complete(). A
    no-op for gpt-4.1 / Claude, so existing behaviour is unchanged. Streaming,
    embeddings, .beta etc. pass straight through ``__getattr__``."""
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def chat(self) -> "_NormalizingChat":
        return _NormalizingChat(self._inner.chat)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_inner"], name)


def _is_openrouter_out_of_credits(exc: Exception) -> bool:
    """True when `exc` looks like OpenRouter rejecting a call for lack of
    credits (HTTP 402 / 'insufficient credits') — the signal to fall back to
    direct OpenAI. Conservative: only matches 402 or explicit credit wording,
    so genuine errors (429, 5xx, auth) still propagate / retry normally."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 402:
        return True
    msg = str(exc).lower()
    if "402" in msg or "payment required" in msg:
        return True
    return "credit" in msg and ("insufficient" in msg or "negative" in msg or "exhaust" in msg)


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
            # Phase 51.C — explicit SDK retries (exponential backoff + jitter on
            # 429 / 5xx / connection errors). Default 3; SNOWKAP_LLM_MAX_RETRIES
            # tunes it. The OpenAI SDK applies this to both sync + async clients.
            "max_retries": int(os.environ.get("SNOWKAP_LLM_MAX_RETRIES", "3")),
        }
        base = resolve_base_url()
        if base:
            kwargs["base_url"] = base
        return kwargs

    # ------------------------------------------------------------------
    # Phase 51.C — transparent fallback to direct OpenAI when OpenRouter
    # runs out of credits (402). Fires ONLY when OpenRouter is the active
    # provider AND an OPENAI_API_KEY is available; otherwise the original
    # error propagates. Uses the task class's bare OpenAI model
    # (reasoning_heavy → gpt-4.1) so a credit-out degrades gracefully.
    # ------------------------------------------------------------------

    def _maybe_fallback_kwargs(
        self, exc: Exception, orig_kwargs: dict[str, Any], override: str | None
    ) -> dict[str, Any] | None:
        if is_using_legacy_openai():
            return None  # already on direct OpenAI — nowhere to fall back to
        if not _is_openrouter_out_of_credits(exc):
            return None
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return None
        from engine.llm.routing import resolve_openai_fallback_model
        fb = dict(orig_kwargs)
        fb.pop("extra_headers", None)  # OpenRouter routing headers — not for OpenAI
        fb["model"] = resolve_openai_fallback_model(self.task_class, override)
        # The OpenAI fallback model may be a reasoning model (gpt-5-mini) which
        # rejects max_tokens/temperature — translate before the fallback call.
        fb = _normalize_params_for_model(fb)
        logger.warning(
            "OpenRouter out of credits (%s) — falling back to direct OpenAI model %s",
            type(exc).__name__, fb["model"],
        )
        return fb

    def _direct_openai_sync(self) -> Any:
        from openai import OpenAI
        return OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            timeout=self.timeout,
            max_retries=int(os.environ.get("SNOWKAP_LLM_MAX_RETRIES", "3")),
        )

    def _direct_openai_async(self) -> Any:
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            timeout=self.timeout,
            max_retries=int(os.environ.get("SNOWKAP_LLM_MAX_RETRIES", "3")),
        )

    @property
    def sync(self) -> Any:
        if self._sync is None:
            from openai import OpenAI
            self._sync = OpenAI(**self._build_kwargs())
        # Wrap so the ~19 stages that call the raw SDK directly (client = llm.sync)
        # get reasoning-model param translation. No-op for gpt-4.1 / Claude.
        return _NormalizingClientProxy(self._sync)

    @property
    def async_client(self) -> Any:
        if self._async is None:
            from openai import AsyncOpenAI
            self._async = AsyncOpenAI(**self._build_kwargs())
        return _NormalizingClientProxy(self._async)

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
        kwargs = _normalize_params_for_model(kwargs)

        t0 = time.perf_counter()
        try:
            completion = self.sync.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — inspected; non-credits errors re-raise
            fb_kwargs = self._maybe_fallback_kwargs(exc, kwargs, model)
            if fb_kwargs is None:
                raise
            kwargs = fb_kwargs
            completion = self._direct_openai_sync().chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000

        resp = self._build_response(completion, kwargs["model"], latency_ms)
        self._log_usage(resp)
        return resp

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
        kwargs = _normalize_params_for_model(kwargs)

        t0 = time.perf_counter()
        try:
            completion = await self.async_client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — inspected; non-credits errors re-raise
            fb_kwargs = self._maybe_fallback_kwargs(exc, kwargs, model)
            if fb_kwargs is None:
                raise
            kwargs = fb_kwargs
            completion = await self._direct_openai_async().chat.completions.create(**kwargs)
        latency_ms = (time.perf_counter() - t0) * 1000
        resp = self._build_response(completion, kwargs["model"], latency_ms)
        self._log_usage(resp)
        return resp

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
        kwargs = _normalize_params_for_model(kwargs)

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
        kwargs = _normalize_params_for_model(kwargs)

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

    def _log_usage(self, resp: "LLMResponse") -> None:
        """Phase 51 — record token usage + cost for this call to llm_calls.
        Non-blocking: telemetry must never break the LLM path. Covers every
        gateway caller (lede, approval, chat, …)."""
        try:
            from engine.models.llm_calls import log_call
            u = resp.usage or {}
            log_call(
                model=resp.model_used,
                prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
                completion_tokens=int(u.get("completion_tokens", 0) or 0),
                stage=self.task_class,
                cost_usd=getattr(resp, "cost_usd", None),
            )
        except Exception:  # noqa: BLE001
            # Telemetry must never break the LLM path, but must not be
            # swallowed silently either (CLAUDE.md §12.2 — no silent except).
            logger.warning("llm_calls usage logging failed", exc_info=True)


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
