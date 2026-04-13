"""OpenAI-powered NLP extraction.

Single-call extraction that produces all signals needed for downstream
analysis in one LLM round-trip (≈ 800 tokens). Uses JSON mode for reliable
parsing and falls back to defaults on errors.

Extracts:
- Sentiment (-2 to +2)
- Tone (controlled 10-word vocabulary)
- Narrative arc (core claim, implied causation, stakeholder framing)
- Named entities (companies, locations, regulators, commodities, people)
- Financial signals (amount, unit, context)
- Regulatory references (framework codes, sections)
- Source credibility tier (1-4, rule-based)
- ESG pillar (E/S/G)
- Content type (regulatory/financial/operational/reputational/technical/narrative)
- Urgency (critical/high/medium/low)
- Time horizon (immediate/days/weeks/months)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.config import get_openai_api_key, load_settings

logger = logging.getLogger(__name__)

TONE_VOCAB = [
    "alarmist",
    "cautionary",
    "analytical",
    "neutral",
    "optimistic",
    "promotional",
    "adversarial",
    "conciliatory",
    "urgent",
    "speculative",
]

# Tier 1: institutional / regulatory
TIER_1_SOURCES = {
    "sebi",
    "rbi",
    "sec",
    "epa",
    "ipcc",
    "world bank",
    "imf",
    "moef",
    "cpcb",
    "niti aayog",
    "esrb",
    "esma",
    "eba",
}
# Tier 2: established financial/business media
TIER_2_SOURCES = {
    "reuters",
    "bloomberg",
    "financial times",
    "wall street journal",
    "wsj",
    "economic times",
    "business standard",
    "mint",
    "livemint",
    "moneycontrol",
    "cnbc",
    "forbes",
    "nikkei",
    "ft",
}


@dataclass
class NLPExtraction:
    sentiment: int  # -2 to +2
    sentiment_confidence: float  # 0-1
    tone: list[str]  # subset of TONE_VOCAB
    narrative_core_claim: str
    narrative_implied_causation: str
    narrative_stakeholder_framing: str
    entities: list[str]  # flat list of extracted names
    entity_types: dict[str, str]  # entity → type
    financial_signal: dict[str, Any]  # {amount, unit, context}
    regulatory_references: list[str]
    esg_pillar: str  # E | S | G | mixed
    esg_topics: list[str]  # free-form topic hints
    content_type: str  # regulatory/financial/operational/reputational/technical/narrative
    urgency: str  # critical|high|medium|low
    time_horizon: str  # immediate|days|weeks|months
    source_credibility_tier: int  # 1-4
    climate_events: list[str] = field(default_factory=list)
    raw_llm_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw_llm_response", None)
        return data


_SYSTEM_PROMPT = """You are an ESG intelligence NLP extractor. Your job is to analyze news articles about companies and extract structured signals for downstream ontology-driven ESG analysis.

You MUST respond with a single JSON object matching this exact schema:
{
  "sentiment": <integer between -2 and 2>,
  "sentiment_confidence": <float 0-1>,
  "tone": [<list of 1-3 tone words from: alarmist, cautionary, analytical, neutral, optimistic, promotional, adversarial, conciliatory, urgent, speculative>],
  "narrative_core_claim": "<1 sentence core claim>",
  "narrative_implied_causation": "<1 sentence cause-effect chain>",
  "narrative_stakeholder_framing": "<who is protagonist, who is affected>",
  "entities": [<list of up to 10 named entities: companies, locations, regulators, commodities, people>],
  "entity_types": {<entity>: <type>},
  "financial_signal": {"amount": <number or null>, "unit": "<crore|lakh|million|billion|null>", "context": "<what the amount refers to>"},
  "regulatory_references": [<framework codes mentioned: BRSR, GRI, TCFD, SEBI, CSRD, etc.>],
  "esg_pillar": "<E | S | G | mixed>",
  "esg_topics": [<list of free-form topic hints: climate, water, emissions, governance, supply chain labor, etc.>],
  "content_type": "<regulatory | financial | operational | reputational | technical | narrative | data_release>",
  "urgency": "<critical | high | medium | low>",
  "time_horizon": "<immediate | days | weeks | months>",
  "climate_events": [<list of climate events mentioned: flood, drought, heatwave, cyclone, wildfire, etc. (empty list if none)>]
}

Rules:
- Sentiment: -2 (crisis/scandal), -1 (risk/concern), 0 (factual/routine), +1 (progress), +2 (breakthrough)
- For narrative fields, keep each under 200 characters
- For entities, include the text exactly as it appears
- If no financial amount, set {"amount": null, "unit": null, "context": ""}
- Return ONLY the JSON object, no preamble, no markdown."""


def _tier_for_source(source: str) -> int:
    lowered = (source or "").lower()
    if any(t in lowered for t in TIER_1_SOURCES):
        return 1
    if any(t in lowered for t in TIER_2_SOURCES):
        return 2
    if lowered and lowered != "google news":
        return 3
    return 4


def _default_extraction(title: str, content: str, source: str) -> NLPExtraction:
    """Fallback when the LLM call fails."""
    return NLPExtraction(
        sentiment=0,
        sentiment_confidence=0.0,
        tone=["neutral"],
        narrative_core_claim=title[:200],
        narrative_implied_causation="",
        narrative_stakeholder_framing="",
        entities=[],
        entity_types={},
        financial_signal={"amount": None, "unit": None, "context": ""},
        regulatory_references=[],
        esg_pillar="mixed",
        esg_topics=[],
        content_type="narrative",
        urgency="low",
        time_horizon="months",
        source_credibility_tier=_tier_for_source(source),
        climate_events=[],
        raw_llm_response={"error": "fallback"},
    )


def run_nlp_pipeline(
    title: str,
    content: str,
    source: str = "",
    client: OpenAI | None = None,
) -> NLPExtraction:
    """Extract all NLP signals from a news article in a single OpenAI call."""
    title = (title or "").strip()
    content = (content or "").strip()
    if not title and not content:
        return _default_extraction(title, content, source)

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    max_tokens = llm_cfg.get("max_tokens_extraction", 800)
    temperature = llm_cfg.get("temperature", 0.2)

    client = client or OpenAI(api_key=get_openai_api_key())

    user_prompt = (
        f"TITLE: {title}\n\n"
        f"SOURCE: {source}\n\n"
        f"CONTENT: {content[:4000]}"  # cap content to control tokens
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning("NLP extraction failed (%s) — using fallback", type(exc).__name__)
        return _default_extraction(title, content, source)

    return NLPExtraction(
        sentiment=int(parsed.get("sentiment", 0) or 0),
        sentiment_confidence=float(parsed.get("sentiment_confidence", 0.5) or 0.5),
        tone=list(parsed.get("tone", ["neutral"])),
        narrative_core_claim=(str(parsed.get("narrative_core_claim", "") or "").strip() or title)[:500],
        narrative_implied_causation=str(parsed.get("narrative_implied_causation", "") or "")[:500],
        narrative_stakeholder_framing=str(parsed.get("narrative_stakeholder_framing", "") or "")[:500],
        entities=list(parsed.get("entities", []) or []),
        entity_types=dict(parsed.get("entity_types", {}) or {}),
        financial_signal=dict(parsed.get("financial_signal", {}) or {}),
        regulatory_references=list(parsed.get("regulatory_references", []) or []),
        esg_pillar=str(parsed.get("esg_pillar", "mixed") or "mixed"),
        esg_topics=list(parsed.get("esg_topics", []) or []),
        content_type=str(parsed.get("content_type", "narrative") or "narrative"),
        urgency=str(parsed.get("urgency", "low") or "low"),
        time_horizon=str(parsed.get("time_horizon", "months") or "months"),
        source_credibility_tier=_tier_for_source(source),
        climate_events=list(parsed.get("climate_events", []) or []),
        raw_llm_response=parsed,
    )
