"""Phase 31 — LLM-driven query generator for live news fetch.

Produces two short, high-signal news queries per company:

    sustainability_query — the single best ESG / climate / labour /
                            governance query Google News should run for
                            this company.
    general_query         — the single best general business news query
                            (earnings, M&A, leadership, regulation).

These are stamped on the ``companies`` table at onboard time
(``sustainability_query`` + ``general_query`` columns added by migration
004) and consumed by :mod:`engine.ingestion.live_fetcher` on every
``/api/news/live`` request. One LLM call per company per onboard —
amortised cost ~$0.0003.

Fail-soft: on any LLM error we return industry-anchored fallbacks so
the live fetcher never sees a None.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompanyQueries:
    sustainability_query: str
    general_query: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sustainability_query": self.sustainability_query,
            "general_query": self.general_query,
        }


_SYSTEM_PROMPT = """You craft news-search queries for an ESG intelligence platform.

For each company, return exactly TWO short Google-News-style queries:

1. `sustainability_query` — the single best query to surface ESG /
   sustainability / climate / labour / governance / regulatory news
   specific to this company over the next week. Lean on themes the
   company is materially exposed to (e.g. for a steel mill: emissions,
   CBAM, scope 3; for a bank: climate stress test, fossil-fuel
   financing, BRSR).
2. `general_query` — the single best query for high-signal general
   business news (earnings, M&A, leadership change, regulator action,
   stock-moving event).

Constraints:
- Each query MUST start with the company's name verbatim.
- 6-12 words total. Quoted multi-word phrases allowed.
- No boolean operators (AND / OR / NOT). Plain prose only.
- No date filters — the platform applies its own freshness window.
- Output strict JSON: {"sustainability_query": "...", "general_query": "..."}
- Nothing else. No explanations, no preamble."""


def _user_prompt(name: str, industry: str | None, region: str | None) -> str:
    return (
        f"Company: {name}\n"
        f"Industry: {industry or 'Unknown'}\n"
        f"Region: {region or 'GLOBAL'}\n"
        "\n"
        "Return the JSON object now."
    )


def _fallback(name: str, industry: str | None) -> CompanyQueries:
    """Deterministic fallback when the LLM call fails. Industry-anchored
    so we still get reasonably-targeted coverage."""
    industry_anchor = {
        "Financials/Banking": "climate stress test BRSR disclosure",
        "Asset Management": "stewardship code fund governance",
        "Power/Energy": "emissions scope 3 climate transition",
        "Renewable Energy": "solar capacity PPA supply chain",
        "Steel": "emissions CBAM coking coal",
        "Automotive": "emission norms EV transition supply chain",
        "Oil & Gas": "scope 3 climate transition carbon levy",
        "Chemicals": "hazardous emissions effluent pollution",
        "Pharmaceuticals": "supply chain safety FDA warning",
        "Information Technology": "sustainability scope 3 talent ESG rating",
        "Consumer/Beverage": "water stress plastic packaging sustainability",
    }.get(industry or "", "ESG sustainability climate disclosure")
    return CompanyQueries(
        sustainability_query=f"{name} {industry_anchor}",
        general_query=f"{name} earnings results regulatory action",
    )


def generate_queries(
    name: str,
    industry: str | None = None,
    region: str | None = None,
    *,
    model: str = "gpt-4.1-mini",
) -> CompanyQueries:
    """Return (sustainability_query, general_query) for the company.

    One LLM call. Fails open: returns a deterministic industry-anchored
    fallback if the model is unavailable or the response is malformed.
    """
    if not name or not name.strip():
        raise ValueError("generate_queries: name is required")

    try:
        from engine.llm import get_llm_client
        client = get_llm_client(task_class="classification").sync
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(name, industry, region)},
            ],
            temperature=0.2,
            max_tokens=180,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        parsed: dict[str, Any] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — fail-soft per docstring
        logger.warning(
            "llm_query_generator: LLM call failed (%s) — using fallback",
            type(exc).__name__,
        )
        return _fallback(name, industry)

    sustainability = (parsed.get("sustainability_query") or "").strip()
    general = (parsed.get("general_query") or "").strip()

    if not sustainability or not general:
        logger.warning(
            "llm_query_generator: incomplete LLM payload for %s — using fallback",
            name,
        )
        return _fallback(name, industry)

    return CompanyQueries(sustainability_query=sustainability, general_query=general)
