"""Phase 10 — cadence math.

Pure functions, no DB. Given a cadence spec + a reference time, compute when
the next drip should fire. Unit-testable without mocking anything.

Conventions:
  * All times are ISO-8601 UTC strings (with or without fractional seconds,
    with or without timezone suffix — we parse leniently and always emit
    timezone-aware UTC).
  * `day_of_week` is 0-6, Monday=0 (Python's `weekday()` convention).
  * `day_of_month` is 1-28 (29/30/31 are rejected at the store layer).
  * `send_time_utc` is "HH:MM" in 24-hour UTC (no seconds).

Why three choices:
  * `once`  — fire exactly once, at (next_send_at already-computed by caller).
              Subsequent calls return None to signal "don't reschedule".
  * `weekly`  — every N-th day of the week, at send_time_utc.
  * `monthly` — day_of_month of every month, at send_time_utc.

`cadence_interval()` returns the gap between fires, which the runner halves
for its dedup window (so a cron retry within half a cadence doesn't
double-send).
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Literal

Cadence = Literal["once", "weekly", "monthly"]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_iso_utc(s: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a UTC-aware datetime.

    Accepts `2026-04-27T09:00:00+00:00`, `2026-04-27T09:00:00Z`, and naive
    `2026-04-27T09:00:00` (treated as UTC). Returns None on bad input."""
    if not s:
        return None
    s = s.strip()
    # Handle the trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_time(send_time_utc: str | None) -> time:
    """Parse 'HH:MM' → datetime.time. Defaults to 09:00 if None/invalid."""
    if send_time_utc:
        m = _TIME_RE.match(send_time_utc.strip())
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return time(hour=hh, minute=mm, tzinfo=timezone.utc)
    return time(hour=9, minute=0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    """UTC → ISO string without fractional seconds."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_next_send(
    cadence: Cadence,
    *,
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    send_time_utc: str | None = None,
    from_time: datetime | str | None = None,
) -> str | None:
    """Return the ISO-8601 UTC timestamp of the next fire, or None for 'done'.

    For `once`: returns the very next occurrence of `send_time_utc` at or
    after `from_time`. Caller is expected to call this ONCE (at create time)
    and NOT reschedule — the runner passes None back here after firing.

    For `weekly`: returns the next occurrence of `day_of_week` at
    `send_time_utc` strictly after `from_time`. If today IS the target
    day_of_week and send_time_utc is still in the future, that's the answer;
    otherwise we roll forward up to 7 days.

    For `monthly`: returns the next occurrence of `day_of_month` at
    `send_time_utc` strictly after `from_time`. Rolls over the month
    boundary as needed. Day 29-31 is unsupported (rejected at store layer).
    """
    if cadence not in ("once", "weekly", "monthly"):
        raise ValueError(f"invalid cadence: {cadence!r}")

    # Normalise reference time
    if from_time is None:
        now = datetime.now(timezone.utc)
    elif isinstance(from_time, str):
        parsed = _parse_iso_utc(from_time)
        if parsed is None:
            raise ValueError(f"invalid from_time ISO string: {from_time!r}")
        now = parsed
    else:
        now = from_time.astimezone(timezone.utc)

    target_time = _parse_time(send_time_utc)

    if cadence == "once":
        # Next occurrence at or after `now` at target_time. If we're before
        # target_time today, fire today; else tomorrow.
        candidate = datetime.combine(now.date(), target_time)
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return _iso(candidate)

    if cadence == "weekly":
        if day_of_week is None or not (0 <= day_of_week <= 6):
            raise ValueError("weekly cadence requires day_of_week in 0..6")
        # Find next matching weekday
        current_weekday = now.weekday()
        delta_days = (day_of_week - current_weekday) % 7
        candidate = datetime.combine(now.date(), target_time) + timedelta(days=delta_days)
        # If that's today and the time already passed, roll 7 days
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        return _iso(candidate)

    # cadence == "monthly"
    if day_of_month is None or not (1 <= day_of_month <= 28):
        raise ValueError("monthly cadence requires day_of_month in 1..28")
    # Try current month first
    try:
        candidate = datetime(
            year=now.year, month=now.month, day=day_of_month,
            hour=target_time.hour, minute=target_time.minute, tzinfo=timezone.utc,
        )
    except ValueError:
        # Shouldn't happen for day_of_month in 1..28, but be safe
        candidate = None  # type: ignore[assignment]
    if candidate is None or candidate <= now:
        # Roll to next month
        next_month = now.month + 1
        next_year = now.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        candidate = datetime(
            year=next_year, month=next_month, day=day_of_month,
            hour=target_time.hour, minute=target_time.minute, tzinfo=timezone.utc,
        )
    return _iso(candidate)


def cadence_interval(cadence: Cadence) -> timedelta:
    """Canonical gap between fires. Used by the runner for dedup halving.

    `once` returns a large delta (30 days) — once-fire campaigns shouldn't
    have dedup races, but if something weird happens we still want a window."""
    if cadence == "weekly":
        return timedelta(days=7)
    if cadence == "monthly":
        return timedelta(days=30)  # approximate; good enough for dedup
    if cadence == "once":
        return timedelta(days=30)
    raise ValueError(f"invalid cadence: {cadence!r}")


def dedup_window_start(cadence: Cadence, *, now_iso: str | None = None) -> str:
    """Return the ISO timestamp representing `now - cadence_interval/2`.

    Send-log rows newer than this for the same (campaign, recipient, article)
    mean 'we already sent — skip'."""
    if now_iso:
        now_dt = _parse_iso_utc(now_iso)
        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
    else:
        now_dt = datetime.now(timezone.utc)
    cutoff = now_dt - (cadence_interval(cadence) / 2)
    return _iso(cutoff)
