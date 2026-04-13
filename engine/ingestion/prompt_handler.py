"""Normalize unstructured text prompts into the common ingestion shape.

Accepts a raw text file or a string and returns a structured payload that
looks like a news article so the downstream pipeline can treat it uniformly::

    {
        "source_type": "prompt",
        "title": "...",
        "content": "...",
        "intent": "question | analysis_request | incident_report | ...",
        "metadata": {"word_count": N, ...},
    }
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INTENT_KEYWORDS = {
    "question": ["what", "why", "how", "when", "who", "?"],
    "analysis_request": ["analyze", "analysis", "evaluate", "assess", "review"],
    "incident_report": ["incident", "accident", "breach", "violation", "failure", "outage"],
    "compliance_check": ["comply", "compliance", "regulation", "brsr", "csrd", "sebi", "rbi"],
    "strategic_query": ["strategy", "competitive", "market", "position", "differentiation"],
    "financial_query": ["cost", "revenue", "margin", "ebitda", "roi", "capex"],
}


@dataclass
class NormalizedPrompt:
    source_type: str
    title: str
    content: str
    intent: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _classify_intent(text: str) -> str:
    lowered = text.lower()
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        scores[intent] = sum(lowered.count(k) for k in keywords)
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "general"


def _derive_title(text: str) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if len(first_line) > 120:
        return first_line[:117] + "..."
    return first_line or "Untitled Prompt"


def normalize_text(text: str, title: str | None = None) -> NormalizedPrompt:
    """Normalize a raw text prompt into the common shape."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Empty prompt")
    intent = _classify_intent(cleaned)
    return NormalizedPrompt(
        source_type="prompt",
        title=title or _derive_title(cleaned),
        content=cleaned,
        intent=intent,
        metadata={
            "word_count": len(cleaned.split()),
            "char_count": len(cleaned),
            "line_count": cleaned.count("\n") + 1,
        },
    )


def normalize_file(path: Path | str) -> NormalizedPrompt:
    """Load a text file and normalize its contents."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Prompt file not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    return normalize_text(text, title=p.stem.replace("_", " ").replace("-", " ").title())
