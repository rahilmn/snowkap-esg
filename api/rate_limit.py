"""Phase 22.3 — Lightweight in-memory token bucket for endpoint rate-limiting.

Used by the magic-link OTP login flow to throttle brute-force / scraper
traffic. NOT a substitute for an edge WAF — when we move to Postgres /
multi-process gunicorn this needs to be replaced with a Redis-backed
implementation (the buckets here live in a single Python process).

The bucket key is `(scope, identifier)` so we can apply different policies
per endpoint (e.g. login-by-email vs share-by-recipient).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """Sliding-window counter. Records timestamps of recent attempts and
    drops anything older than the longest configured window."""

    timestamps: list[float] = field(default_factory=list)


class RateLimiter:
    """Per-process token bucket with two windows (e.g. per-minute + per-hour).

    Thread-safe (the API runs uvicorn workers but a single FastAPI dep
    can be hit concurrently from the same process). Memory grows linearly
    in the number of distinct keys; we reap stale buckets every `REAP_EVERY`
    calls so a runaway prospect domain can't OOM the box.
    """

    REAP_EVERY = 200

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()
        self._calls_since_reap = 0

    def check(
        self,
        scope: str,
        identifier: str,
        max_per_minute: int,
        max_per_hour: int,
    ) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``.

        ``allowed`` is False when EITHER the per-minute OR per-hour quota
        is exhausted. ``retry_after_seconds`` is the number of seconds the
        client must wait before the next attempt would succeed (suitable
        for the HTTP ``Retry-After`` header). ``0`` when allowed.
        """
        now = time.monotonic()
        key = (scope, identifier.lower().strip())
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            # Drop anything older than 1 hour (the larger of the two windows)
            cutoff = now - 3600.0
            bucket.timestamps = [t for t in bucket.timestamps if t > cutoff]

            in_last_minute = sum(1 for t in bucket.timestamps if t > now - 60.0)
            in_last_hour = len(bucket.timestamps)

            if in_last_minute >= max_per_minute:
                # Find the oldest minute-window timestamp; retry after it ages out
                oldest = min(t for t in bucket.timestamps if t > now - 60.0)
                retry = max(1, int(60.0 - (now - oldest)) + 1)
                return False, retry
            if in_last_hour >= max_per_hour:
                oldest = bucket.timestamps[0]
                retry = max(1, int(3600.0 - (now - oldest)) + 1)
                return False, retry

            bucket.timestamps.append(now)
            self._calls_since_reap += 1
            if self._calls_since_reap >= self.REAP_EVERY:
                self._reap_locked(now)
            return True, 0

    def _reap_locked(self, now: float) -> None:
        """Drop buckets whose newest entry is more than 1 hour old."""
        cutoff = now - 3600.0
        dead = [k for k, b in self._buckets.items() if not b.timestamps or b.timestamps[-1] < cutoff]
        for k in dead:
            self._buckets.pop(k, None)
        self._calls_since_reap = 0

    def reset(self) -> None:
        """Test helper — clear all buckets."""
        with self._lock:
            self._buckets.clear()
            self._calls_since_reap = 0


# Module-level singleton — every endpoint shares the same buckets so a
# brute-force across a tab + curl sees the combined attempt count.
LOGIN_LIMITER = RateLimiter()

# Default policy for /auth/login + /auth/verify (per-email, per-IP).
LOGIN_PER_MIN = 5
LOGIN_PER_HOUR = 20
