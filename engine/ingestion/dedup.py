"""Semantic deduplication for ingested articles.

Title/summary-based Jaccard similarity to collapse near-duplicate stories
that share content but have different URLs (typical of syndicated news:
the same Reuters wire published by 10 aggregators under different URLs).

Threshold + window configurable via settings.json `ingestion`:
- semantic_dedup_threshold (default 0.75) — Jaccard over title+summary tokens
- semantic_dedup_window_hours (default 48) — only dedup within this window

Pure-Python, no new dependencies. Can be upgraded to TF-IDF cosine (sklearn)
later if the coverage audit shows Jaccard misses critical cases.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# English stopwords — small curated list, keeps semantic tokens
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "he", "in", "is", "it", "its", "of", "on", "or", "that", "the", "to",
    "was", "were", "will", "with", "this", "these", "those", "they", "their",
    "have", "had", "been", "but", "not", "can", "could", "would", "should",
    "may", "might", "do", "does", "did", "said", "says", "about", "after",
    "also", "amid", "around", "before", "between", "during", "over", "under",
    "while", "into", "than", "then", "who", "what", "when", "where", "why",
    "how",
})


def _tokenize(text: str) -> frozenset[str]:
    """Lowercase, strip HTML, drop stopwords and short tokens."""
    if not text:
        return frozenset()
    tokens = _TOKEN_RE.findall(text.lower())
    return frozenset(t for t in tokens if len(t) > 2 and t not in _STOPWORDS)


def jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union) if union else 0.0


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


class SemanticDedup:
    """Detect near-duplicate articles within a rolling time window.

    Call `is_duplicate(article)` for each candidate. Duplicates are rejected;
    non-duplicates are added to the internal index for comparison against
    subsequent candidates.

    article dict must contain: `title`, `summary` (optional), `published_at`
    (ISO-8601 string). Other fields are ignored.
    """

    def __init__(
        self,
        threshold: float = 0.75,
        window_hours: int = 48,
    ) -> None:
        self.threshold = threshold
        self.window = timedelta(hours=window_hours)
        self._seen: list[tuple[datetime, frozenset[str], str]] = []  # (ts, tokens, url)

    def _tokens_for(self, article: dict) -> frozenset[str]:
        title = article.get("title") or ""
        summary = article.get("summary") or ""
        # Title carries more signal; give it weight by including twice
        return _tokenize(title + " " + title + " " + summary)

    def is_duplicate(self, article: dict) -> tuple[bool, str | None]:
        """Returns (is_dup, matched_url). Adds the article to the index when not a dup."""
        ts = _parse_iso(article.get("published_at"))
        if ts is None:
            # Can't place in window — fall back to simple add, no dedup
            tokens = self._tokens_for(article)
            self._seen.append((datetime.now(timezone.utc), tokens, article.get("url", "")))
            return False, None

        # Prune out-of-window entries to keep the index bounded
        cutoff = ts - self.window
        self._seen = [(t, toks, url) for (t, toks, url) in self._seen if t >= cutoff]

        tokens = self._tokens_for(article)
        if not tokens:
            return False, None

        for (other_ts, other_tokens, other_url) in self._seen:
            if abs((ts - other_ts).total_seconds()) > self.window.total_seconds():
                continue
            sim = jaccard_similarity(tokens, other_tokens)
            if sim >= self.threshold:
                logger.info(
                    "semantic dedup: '%s' ~ '%s' (jaccard %.2f) - skipping",
                    (article.get("title") or "")[:60],
                    other_url,
                    sim,
                )
                return True, other_url

        self._seen.append((ts, tokens, article.get("url", "")))
        return False, None

    def reset(self) -> None:
        self._seen.clear()


def filter_duplicates(
    articles: Iterable[dict],
    threshold: float = 0.75,
    window_hours: int = 48,
) -> list[dict]:
    """Convenience: returns only the non-duplicates from an iterable of articles.

    Stable order — articles returned in the order they appeared.
    """
    dedup = SemanticDedup(threshold=threshold, window_hours=window_hours)
    out: list[dict] = []
    for art in articles:
        is_dup, _ = dedup.is_duplicate(art)
        if not is_dup:
            out.append(art)
    return out


def is_fresh(
    article: dict,
    max_age_days: int = 90,
    now: datetime | None = None,
) -> bool:
    """Returns True if article `published_at` is within max_age_days of `now`.

    Articles with unparseable timestamps are treated as fresh (fail-open) to
    avoid losing signal from malformed feeds. Use strict=True at the caller
    if you want fail-closed behaviour.
    """
    ts = _parse_iso(article.get("published_at"))
    if ts is None:
        return True
    now = now or datetime.now(timezone.utc)
    age = now - ts
    return age <= timedelta(days=max_age_days)
