"""ESG theme tagger — map articles to the 21-theme taxonomy.

Uses a two-stage strategy:
1. Try OpenAI (gpt-4.1-mini) with the list of 21 topic labels from the ontology.
2. Fall back to keyword matching against the topic labels + sub-metrics.

Topics and their sub-metrics live in ``data/ontology/knowledge_base.ttl`` —
this module never hardcodes the taxonomy. It queries the ontology at import
time for the authoritative list.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.config import get_openai_api_key, load_settings
from engine.ontology.graph import get_graph

logger = logging.getLogger(__name__)


@dataclass
class ESGThemeTags:
    primary_theme: str
    primary_pillar: str  # E | S | G
    primary_sub_metrics: list[str]
    secondary_themes: list[dict]  # [{theme, pillar, sub_metrics}]
    confidence: float
    method: str  # "llm" | "keyword_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Ontology-sourced taxonomy
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _load_topic_taxonomy() -> list[dict[str, Any]]:
    """Return all ESG topics from the ontology with label, pillar, sub-metrics."""
    g = get_graph()
    sparql = """
    SELECT ?label ?pillar (GROUP_CONCAT(?sub; SEPARATOR="|") AS ?subs) WHERE {
        ?topic a ?cls .
        FILTER(?cls IN (snowkap:EnvironmentalTopic, snowkap:SocialTopic, snowkap:GovernanceTopic))
        ?topic rdfs:label ?label .
        OPTIONAL { ?topic snowkap:esgPillar ?pillar }
        OPTIONAL { ?topic snowkap:subMetric ?sub }
    }
    GROUP BY ?topic ?label ?pillar
    ORDER BY ?label
    """
    rows = g.select_rows(sparql)
    taxonomy: list[dict[str, Any]] = []
    for row in rows:
        subs = [s for s in row.get("subs", "").split("|") if s]
        taxonomy.append(
            {
                "label": row["label"],
                "pillar": row.get("pillar", "mixed"),
                "sub_metrics": subs,
            }
        )
    return taxonomy


# ---------------------------------------------------------------------------
# LLM tagging
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    taxonomy = _load_topic_taxonomy()
    topic_list = "\n".join(
        f'- "{t["label"]}" ({t["pillar"]}): {", ".join(t["sub_metrics"][:5])}'
        for t in taxonomy
    )
    return f"""You are an ESG theme tagger. Map the article to exactly one primary theme from the taxonomy below and up to 3 secondary themes.

TAXONOMY:
{topic_list}

Respond with a JSON object:
{{
  "primary_theme": "<one label from the taxonomy>",
  "primary_pillar": "<E | S | G>",
  "primary_sub_metrics": [<2-4 sub-metrics relevant to the primary theme>],
  "secondary_themes": [
    {{"theme": "<label>", "pillar": "<E|S|G>", "sub_metrics": [...]}}
  ],
  "confidence": <0.0-1.0>
}}

Return ONLY the JSON, no markdown."""


def _keyword_fallback(title: str, content: str) -> ESGThemeTags:
    text = f"{title} {content}".lower()
    taxonomy = _load_topic_taxonomy()
    scores: list[tuple[dict, int]] = []
    for topic in taxonomy:
        score = 0
        if topic["label"].lower() in text:
            score += 10
        for sub in topic["sub_metrics"]:
            token = sub.replace("_", " ")
            if token in text:
                score += 3
        if score > 0:
            scores.append((topic, score))
    if not scores:
        # Default to a generic topic
        return ESGThemeTags(
            primary_theme="Climate Change",
            primary_pillar="E",
            primary_sub_metrics=[],
            secondary_themes=[],
            confidence=0.1,
            method="keyword_fallback",
        )
    scores.sort(key=lambda pair: pair[1], reverse=True)
    primary, _ = scores[0]
    secondary = [
        {
            "theme": t["label"],
            "pillar": t["pillar"],
            "sub_metrics": t["sub_metrics"][:3],
        }
        for t, _ in scores[1:4]
    ]
    return ESGThemeTags(
        primary_theme=primary["label"],
        primary_pillar=primary["pillar"],
        primary_sub_metrics=primary["sub_metrics"][:4],
        secondary_themes=secondary,
        confidence=0.4,
        method="keyword_fallback",
    )


def tag_esg_themes(
    title: str,
    content: str,
    client: OpenAI | None = None,
) -> ESGThemeTags:
    """Tag an article with its primary + secondary ESG themes."""
    title = (title or "").strip()
    content = (content or "").strip()
    if not title and not content:
        return ESGThemeTags(
            primary_theme="Climate Change",
            primary_pillar="E",
            primary_sub_metrics=[],
            secondary_themes=[],
            confidence=0.0,
            method="keyword_fallback",
        )

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    temperature = llm_cfg.get("temperature", 0.2)

    client = client or OpenAI(api_key=get_openai_api_key())
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {
                    "role": "user",
                    "content": f"TITLE: {title}\n\nCONTENT: {content[:3000]}",
                },
            ],
            temperature=temperature,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("theme_tagger LLM failed (%s) — using keyword fallback", type(exc).__name__)
        return _keyword_fallback(title, content)

    return ESGThemeTags(
        primary_theme=str(parsed.get("primary_theme", "") or "Climate Change"),
        primary_pillar=str(parsed.get("primary_pillar", "E") or "E"),
        primary_sub_metrics=list(parsed.get("primary_sub_metrics", []) or []),
        secondary_themes=list(parsed.get("secondary_themes", []) or []),
        confidence=float(parsed.get("confidence", 0.7) or 0.7),
        method="llm",
    )
