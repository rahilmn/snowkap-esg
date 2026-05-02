"""Phase 22.3 — Login rate limiting.

In-memory token bucket with two windows (per-minute + per-hour) keyed by
`(scope, identifier)`. Used to throttle /auth/login + /auth/verify so a
brute-force can't grind out OTPs.
"""

from __future__ import annotations

from api.rate_limit import RateLimiter


def test_under_limit_allows():
    rl = RateLimiter()
    for _ in range(4):
        ok, retry = rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
        assert ok and retry == 0


def test_over_minute_limit_blocks():
    rl = RateLimiter()
    for _ in range(5):
        ok, _ = rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
        assert ok
    ok, retry = rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
    assert not ok
    assert 1 <= retry <= 61


def test_separate_keys_isolated():
    rl = RateLimiter()
    for _ in range(5):
        rl.check("login", "alice@x.com", max_per_minute=5, max_per_hour=20)
    ok, _ = rl.check("login", "bob@x.com", max_per_minute=5, max_per_hour=20)
    assert ok


def test_separate_scopes_isolated():
    rl = RateLimiter()
    for _ in range(5):
        rl.check("login", "alice@x.com", max_per_minute=5, max_per_hour=20)
    ok, _ = rl.check("verify", "alice@x.com", max_per_minute=5, max_per_hour=20)
    assert ok


def test_email_normalized():
    """Case + whitespace should not let an attacker reset the bucket."""
    rl = RateLimiter()
    for _ in range(5):
        rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
    ok, _ = rl.check("login", " USER@X.COM ", max_per_minute=5, max_per_hour=20)
    assert not ok


def test_reset_clears_buckets():
    rl = RateLimiter()
    for _ in range(5):
        rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
    rl.reset()
    ok, _ = rl.check("login", "user@x.com", max_per_minute=5, max_per_hour=20)
    assert ok
