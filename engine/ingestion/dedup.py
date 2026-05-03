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

    # Phase 24.3 — separate title-only token set so syndicated stories
    # (same headline, different aggregator, different lede paragraph) get
    # caught even when the body summary diverges. Pre-fix dedup, the
    # 4 Yes Bank "Nippon Life ... settle ... investment case" syndications
    # all slipped through because each aggregator's lede tokens dragged
    # the title+summary Jaccard below the 0.75 threshold.
    _TITLE_ONLY_THRESHOLD = 0.78

    def _tokens_for(self, article: dict) -> frozenset[str]:
        """Combined title+summary tokens (legacy threshold)."""
        title = article.get("title") or ""
        summary = article.get("summary") or ""
        return _tokenize(title + " " + title + " " + summary)

    def _title_tokens(self, article: dict) -> frozenset[str]:
        """Title-only tokens (stricter threshold)."""
        # Strip trailing publisher tags ("- Reuters", "| CNBC TV18", etc.)
        # so titles that differ only in that trailing slug normalize together.
        title = (article.get("title") or "")
        # Cut at the LAST " - " or " | " (publisher separator) — only if it
        # falls in the back third of the string so we don't mangle headlines
        # that legitimately use those characters early on.
        for sep in (" - ", " | "):
            idx = title.rfind(sep)
            if idx > len(title) * 0.6:
                title = title[:idx]
                break
        return _tokenize(title)

    def is_duplicate(self, article: dict) -> tuple[bool, str | None]:
        """Returns (is_dup, matched_url). Adds the article to the index when not a dup."""
        ts = _parse_iso(article.get("published_at"))
        if ts is None:
            # Can't place in window — fall back to simple add, no dedup
            tokens = self._tokens_for(article)
            title_tokens = self._title_tokens(article)
            self._seen.append((datetime.now(timezone.utc), tokens, article.get("url", ""), title_tokens))
            return False, None

        # Prune out-of-window entries to keep the index bounded
        cutoff = ts - self.window
        self._seen = [
            entry for entry in self._seen if entry[0] >= cutoff
        ]

        tokens = self._tokens_for(article)
        title_tokens = self._title_tokens(article)
        if not tokens:
            return False, None

        for entry in self._seen:
            other_ts, other_tokens, other_url = entry[0], entry[1], entry[2]
            other_title_tokens = entry[3] if len(entry) > 3 else frozenset()

            if abs((ts - other_ts).total_seconds()) > self.window.total_seconds():
                continue

            # Title-only check first — strict 0.78 threshold catches
            # syndicated copies that share the headline but have
            # differing publisher prefixes / suffixes.
            if title_tokens and other_title_tokens:
                title_sim = jaccard_similarity(title_tokens, other_title_tokens)
                if title_sim >= self._TITLE_ONLY_THRESHOLD:
                    logger.info(
                        "semantic dedup (title-only %.2f): '%s' ~ '%s' — skipping",
                        title_sim,
                        (article.get("title") or "")[:60],
                        other_url,
                    )
                    return True, other_url

            # Fallback: full title+summary check at the configured threshold
            sim = jaccard_similarity(tokens, other_tokens)
            if sim >= self.threshold:
                logger.info(
                    "semantic dedup (combined %.2f): '%s' ~ '%s' — skipping",
                    sim,
                    (article.get("title") or "")[:60],
                    other_url,
                )
                return True, other_url

        self._seen.append((ts, tokens, article.get("url", ""), title_tokens))
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
