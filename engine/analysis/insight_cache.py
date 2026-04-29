"""Stage 10 insight caching (Phase 6).

Cache key = hash(theme, event_type, industry, company_size_tier).
Cache value = lightweight skeleton of a DeepInsight — the *shared structural*
fields that repeat across similar articles (framework citations, compliance
discussion, SDG mappings) — NOT the article-specific ₹ figures.

The cache is advisory. When a Stage 10 call hits a cached skeleton, the
generator can inject it as a hint into the prompt ("these framework sections
triggered last time a similar event hit this industry") — letting the LLM
reuse boilerplate and focus tokens on the article-specific narrative.

Invalidation: keyed entries older than 30 days are treated as stale and
re-filled on next hit. Cache stored at `data/cache/insight_skeletons.json`.

This is a *cost-optimisation advisory layer*, not a correctness layer.
If the cache fails, calls still succeed — just at full token cost.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/cache/insight_skeletons.json")
CACHE_TTL_DAYS = 30


@dataclass
class CachedSkeleton:
    """Structural / boilerplate parts of a DeepInsight that repeat across
    similar (theme × event × industry × size) articles.
    """
    cache_key: str
    theme: str
    event_type: str
    industry: str
    cap_tier: str
    cached_at: str

    # Fields deemed reusable as hints (not final answers)
    typical_frameworks: list[str] = field(default_factory=list)
    typical_risk_categories: list[str] = field(default_factory=list)
    typical_stakeholders: list[str] = field(default_factory=list)
    typical_sdg_codes: list[str] = field(default_factory=list)
    example_headline_template: str = ""
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "theme": self.theme,
            "event_type": self.event_type,
            "industry": self.industry,
            "cap_tier": self.cap_tier,
            "cached_at": self.cached_at,
            "typical_frameworks": self.typical_frameworks,
            "typical_risk_categories": self.typical_risk_categories,
            "typical_stakeholders": self.typical_stakeholders,
            "typical_sdg_codes": self.typical_sdg_codes,
            "example_headline_template": self.example_headline_template,
            "hit_count": self.hit_count,
        }

    def is_fresh(self, ttl_days: int = CACHE_TTL_DAYS) -> bool:
        try:
            ts = datetime.fromisoformat(self.cached_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts) < timedelta(days=ttl_days)
        except (TypeError, ValueError):
            return False


def _make_key(theme: str, event_type: str, industry: str, cap_tier: str) -> str:
    raw = f"{theme.lower().strip()}|{event_type.lower().strip()}|{industry.lower().strip()}|{cap_tier.lower().strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_cache() -> dict[str, CachedSkeleton]:
    if not CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("cache file corrupt — starting fresh")
        return {}
    out: dict[str, CachedSkeleton] = {}
    for k, v in raw.items():
        try:
            out[k] = CachedSkeleton(**v)
        except TypeError:
            continue
    return out


def _save_cache(cache: dict[str, CachedSkeleton]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v.to_dict() for k, v in cache.items()}
    CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_skeleton(
    theme: str,
    event_type: str,
    industry: str,
    cap_tier: str,
) -> CachedSkeleton | None:
    """Look up a cached skeleton. Returns None if miss or stale."""
    key = _make_key(theme, event_type, industry, cap_tier)
    cache = _load_cache()
    entry = cache.get(key)
    if entry is None:
        return None
    if not entry.is_fresh():
        logger.debug("skeleton %s is stale — returning None", key)
        return None
    entry.hit_count += 1
    cache[key] = entry
    _save_cache(cache)
    return entry


def put_skeleton(
    theme: str,
    event_type: str,
    industry: str,
    cap_tier: str,
    typical_frameworks: list[str] | None = None,
    typical_risk_categories: list[str] | None = None,
    typical_stakeholders: list[str] | None = None,
    typical_sdg_codes: list[str] | None = None,
    example_headline_template: str = "",
) -> CachedSkeleton:
    """Add/update a skeleton in the cache. Overwrites existing entry for the key."""
    key = _make_key(theme, event_type, industry, cap_tier)
    cache = _load_cache()
    existing = cache.get(key)
    entry = CachedSkeleton(
        cache_key=key,
        theme=theme,
        event_type=event_type,
        industry=industry,
        cap_tier=cap_tier,
        cached_at=datetime.now(timezone.utc).isoformat(),
        typical_frameworks=typical_frameworks or [],
        typical_risk_categories=typical_risk_categories or [],
        typical_stakeholders=typical_stakeholders or [],
        typical_sdg_codes=typical_sdg_codes or [],
        example_headline_template=example_headline_template,
        hit_count=existing.hit_count if existing else 0,
    )
    cache[key] = entry
    _save_cache(cache)
    return entry


def cache_stats() -> dict[str, Any]:
    cache = _load_cache()
    total = len(cache)
    hits = sum(c.hit_count for c in cache.values())
    fresh = sum(1 for c in cache.values() if c.is_fresh())
    return {
        "total_entries": total,
        "fresh_entries": fresh,
        "stale_entries": total - fresh,
        "cumulative_hits": hits,
    }


def clear_cache() -> int:
    """Return count of cleared entries."""
    cache = _load_cache()
    n = len(cache)
    _save_cache({})
    return n
