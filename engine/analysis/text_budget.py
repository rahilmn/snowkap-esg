"""Phase 51 — full-article text budgeting.

The engine fetches and persists the FULL article body from NewsAPI.ai, but
historically every LLM stage truncated it to the first 1.5-6 KB. Measured on
the live corpus, ~31% of articles exceed 5 KB and late sections routinely
carry the ₹ figures, penalty amounts and regulatory citations the analysis
depends on — so a head-only truncation silently dropped material facts.

This module centralises a single, generous ceiling and a *smart* compaction
that preserves late money/regulatory sentences for the rare very-long article
instead of a naive head cut. Input tokens are cheap (12 KB ≈ ~3-4k tokens);
the cost drivers (number of Opus calls, output tokens) are unchanged, so the
old caps saved almost nothing while losing accuracy.

Single knob: ``ARTICLE_CEILING``. Revert it to 6000 to restore prior behaviour.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Per-stage article-text ceiling. 12 KB covers ~96% of the live corpus
# end-to-end (median ≈ 3.2 KB, p95 ≈ 8.8 KB; only ~4% exceed 12 KB).
ARTICLE_CEILING = 12_000

# Late-article material we never want to lose to a head cut: money figures,
# penalties, and the regulators / frameworks that drive ESG materiality.
_SALIENT_RE = re.compile(
    r"(₹|\bRs\.?|\bINR\b|\$|crore|lakh|\bcr\b|billion|million|"
    r"penalt|fine|notice|\border\b|verdict|ruling|tribunal|"
    r"SEBI|RBI|MCA|NGT|CERC|\bSEC\b|BRSR|TCFD|TNFD|CSRD|ESRS|ISSB|SASB|SBTi|CDP|CBAM|IFRS|GRI)",
    re.IGNORECASE,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Publisher sidebar / related-posts boundary markers (mirrors
# unified_analysis._article_main_body's markers). We strip ONLY at a real
# marker — never a blind percentage cut, which would drop the salient tail.
_BOUNDARY_RE = re.compile(
    r"\b(?:Related\s+Posts|Related\s+Articles|Related\s+Stories|More\s+from|"
    r"Read\s+also|Read\s+more|You\s+may\s+also\s+like|Recommended\s+for\s+you|"
    r"Trending|Popular\s+posts|Latest\s+news|Comments|Tags?:|Filed\s+under)\b",
    re.IGNORECASE,
)


def clamp_article_text(text: str | None, limit: int = ARTICLE_CEILING) -> str:
    """Bound ``text`` to ``limit`` chars without losing late ₹/regulatory facts.

    - ``len(text) <= limit`` (the common case, ~96% of articles): returned whole.
    - Otherwise: drop publisher sidebar/related-posts noise, keep the head, and
      back-fill the remaining budget with the *salient* (money/regulatory)
      sentences from the tail so late figures survive a naive head cut.
    """
    if not text:
        return text or ""
    if len(text) <= limit:
        return text

    # Step 1 — drop publisher sidebar / related-posts, but ONLY at a real
    # boundary marker. A blind percentage cut would defeat the whole point by
    # dropping the salient tail we are trying to preserve.
    body = text
    marker = _BOUNDARY_RE.search(text)
    if marker and marker.start() > 0:
        body = text[: marker.start()]
        if len(body) <= limit:
            logger.info("clamp_article_text: trimmed sidebar at marker %d->%d chars", len(text), len(body))
            return body

    # Step 2 — head + salient tail (scans the full body so late ₹/regulatory
    # facts survive a naive head cut).
    head_budget = int(limit * 0.6)
    tail_budget = limit - head_budget
    head = body[:head_budget]
    kept: list[str] = []
    used = 0
    for sent in _SENT_SPLIT.split(body[head_budget:]):
        if not _SALIENT_RE.search(sent):
            continue
        s = sent.strip()
        if used + len(s) + 1 > tail_budget:
            break
        kept.append(s)
        used += len(s) + 1
    tail = " ".join(kept) if kept else body[-tail_budget:]
    out = f"{head}\n…[compacted: {len(kept)} salient tail sentences kept]…\n{tail}"
    logger.info(
        "clamp_article_text: compacted %d->%d chars (head=%d salient_tail=%d sentences=%d)",
        len(text), len(out), len(head), len(tail), len(kept),
    )
    return out


__all__ = ["ARTICLE_CEILING", "clamp_article_text"]
