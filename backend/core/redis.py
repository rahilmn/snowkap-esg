"""Redis client with tenant-namespaced caching.

Per MASTER_BUILD_PLAN Phase 2B:
- Redis: tenant-namespaced caching (config TTL 5min, news TTL 15min)
- Used for: cache, pub/sub, Celery broker
"""

import json
from typing import Any

import redis.asyncio as aioredis
import structlog

from backend.core.config import settings

logger = structlog.get_logger()

# TTL constants per MASTER_BUILD_PLAN
CACHE_TTL_CONFIG = 300       # 5 minutes
CACHE_TTL_NEWS = 900         # 15 minutes
CACHE_TTL_COMPANY = 600      # 10 minutes
CACHE_TTL_ANALYSIS = 86400   # 24 hours — deep insight, causal chains, risk matrix
CACHE_TTL_PREDICTION = 3600  # 1 hour

_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


def _tenant_key(tenant_id: str, namespace: str, key: str) -> str:
    """Build a tenant-namespaced cache key.

    Format: tenant:{tenant_id}:{namespace}:{key}
    Per MASTER_BUILD_PLAN: tenant-namespaced caching.
    """
    return f"tenant:{tenant_id}:{namespace}:{key}"


def make_cache_key(tenant_id: str, namespace: str, key: str) -> str:
    """Public alias for _tenant_key — use outside of redis.py."""
    return _tenant_key(tenant_id, namespace, key)


async def cache_get(tenant_id: str, namespace: str, key: str) -> Any | None:
    """Get a cached value scoped to a tenant namespace."""
    r = await get_redis()
    full_key = _tenant_key(tenant_id, namespace, key)
    raw = await r.get(full_key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def cache_set(
    tenant_id: str,
    namespace: str,
    key: str,
    value: Any,
    ttl: int = CACHE_TTL_CONFIG,
) -> None:
    """Set a cached value scoped to a tenant namespace."""
    r = await get_redis()
    full_key = _tenant_key(tenant_id, namespace, key)
    serialized = json.dumps(value) if not isinstance(value, str) else value
    await r.set(full_key, serialized, ex=ttl)
    logger.debug("cache_set", key=full_key, ttl=ttl)


async def cache_delete(tenant_id: str, namespace: str, key: str) -> None:
    """Delete a cached value."""
    r = await get_redis()
    full_key = _tenant_key(tenant_id, namespace, key)
    await r.delete(full_key)


async def cache_invalidate_tenant(tenant_id: str, namespace: str | None = None) -> int:
    """Invalidate all cache entries for a tenant, optionally scoped to a namespace."""
    r = await get_redis()
    pattern = f"tenant:{tenant_id}:{namespace}:*" if namespace else f"tenant:{tenant_id}:*"
    keys = []
    async for key in r.scan_iter(match=pattern, count=100):
        keys.append(key)
    if keys:
        await r.delete(*keys)
    logger.info("cache_invalidated", tenant_id=tenant_id, namespace=namespace, count=len(keys))
    return len(keys)


async def publish_tenant_event(tenant_id: str, event_type: str, data: dict) -> None:
    """Publish an event to a tenant-scoped Redis pub/sub channel.

    Per MASTER_BUILD_PLAN Phase 2B: Socket.IO tenant-scoped rooms via Redis pub/sub.
    """
    r = await get_redis()
    channel = f"tenant:{tenant_id}:events"
    message = json.dumps({"type": event_type, "data": data})
    await r.publish(channel, message)
    logger.debug("event_published", tenant_id=tenant_id, event_type=event_type)
