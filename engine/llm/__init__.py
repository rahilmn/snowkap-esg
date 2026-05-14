"""Phase C — Unified LLM gateway.

`engine.llm` is the single entry point for every LLM call in the
engine. It wraps the OpenAI SDK pointed at OpenRouter (when
`OPENROUTER_API_KEY` is set) or at OpenAI directly (fallback). All
~25 existing call sites migrate to `get_llm_client(task_class=...)`
or `get_llm_client(model=...)`.

Behavioural guarantee: when `OPENROUTER_API_KEY` is absent the
returned client is **byte-for-byte equivalent** to the prior
`OpenAI(api_key=...)` path, so Phase 26's existing 1731 tests pass
unchanged.

Modules:
  - client.py     OpenRouterClient (sync + async + streaming)
  - routing.py    TASK_CLASS_TO_MODEL + resolve_model
  - cost.py       parse_cost from response.usage.cost
  - keys.py       OPENROUTER_API_KEY resolution + legacy fallback
"""
from engine.llm.client import LLMResponse, OpenRouterClient, TokenEvent, get_llm_client
from engine.llm.keys import is_using_legacy_openai
from engine.llm.routing import TASK_CLASS_TO_MODEL, resolve_model

__all__ = [
    "OpenRouterClient",
    "LLMResponse",
    "TokenEvent",
    "get_llm_client",
    "is_using_legacy_openai",
    "TASK_CLASS_TO_MODEL",
    "resolve_model",
]
