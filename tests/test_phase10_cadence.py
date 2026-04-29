"""Phase 10 — cadence math tests (pure functions, no DB).

Covers:
  * once/weekly/monthly next_send_at computation
  * UTC time-of-day handling
  * Same-day rollforward when target time has passed
  * Month rollover (Dec → Jan)
  * Invalid inputs raise ValueError
  * dedup_window_start = now - interval/2
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.output.cadence import (
    cadence_interval,
    compute_next_send,
    dedup_window_start,
)


def _dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# once
# ---------------------------------------------------------------------------


def test_once_fires_today_if_time_still_future():
    ref = _dt(2026, 4, 27, 8, 30)  # Monday 08:30 UTC
    r = compute_next_send("once", send_time_utc="09:00", from_time=ref)
    assert r == "2026-04-27T09:00:00+00:00"


def test_once_rolls_to_tomorrow_if_time_already_passed():
    ref = _dt(2026, 4, 27, 10, 0)  # Monday 10:00 UTC (past 09:00)
    r = compute_next_send("once", send_time_utc="09:00", from_time=ref)
    assert r == "2026-04-28T09:00:00+00:00"


# ---------------------------------------------------------------------------
# weekly
# ---------------------------------------------------------------------------


def test_weekly_monday_0900_from_monday_morning():
    ref = _dt(2026, 4, 27, 8, 0)  # Monday 08:00
    r = compute_next_send("weekly", day_of_week=0, send_time_utc="09:00", from_time=ref)
    assert r == "2026-04-27T09:00:00+00:00"


def test_weekly_monday_0900_after_fire_time_rolls_one_week():
    ref = _dt(2026, 4, 27, 9, 1)  # Monday 09:01 (just past fire time)
    r = compute_next_send("weekly", day_of_week=0, send_time_utc="09:00", from_time=ref)
    assert r == "2026-05-04T09:00:00+00:00"  # next Monday


def test_weekly_friday_from_tuesday():
    ref = _dt(2026, 4, 28, 12, 0)  # Tuesday
    # Friday = 4
    r = compute_next_send("weekly", day_of_week=4, send_time_utc="17:30", from_time=ref)
    assert r == "2026-05-01T17:30:00+00:00"


def test_weekly_sunday_from_saturday():
    ref = _dt(2026, 5, 2, 23, 0)  # Saturday
    # Sunday = 6
    r = compute_next_send("weekly", day_of_week=6, send_time_utc="10:00", from_time=ref)
    assert r == "2026-05-03T10:00:00+00:00"


# ---------------------------------------------------------------------------
# monthly
# ---------------------------------------------------------------------------


def test_monthly_same_month_future_day():
    ref = _dt(2026, 4, 10, 0, 0)
    r = compute_next_send("monthly", day_of_month=15, send_time_utc="09:00", from_time=ref)
    assert r == "2026-04-15T09:00:00+00:00"


def test_monthly_day_already_passed_rolls_next_month():
    ref = _dt(2026, 4, 20, 12, 0)
    r = compute_next_send("monthly", day_of_month=15, send_time_utc="09:00", from_time=ref)
    assert r == "2026-05-15T09:00:00+00:00"


def test_monthly_december_rolls_to_january():
    ref = _dt(2026, 12, 20, 0, 0)
    r = compute_next_send("monthly", day_of_month=5, send_time_utc="09:00", from_time=ref)
    assert r == "2027-01-05T09:00:00+00:00"


def test_monthly_28th_boundary_safe():
    """Day 28 is always valid (Feb has it). We reject 29+ at the store layer."""
    ref = _dt(2026, 2, 1, 0, 0)
    r = compute_next_send("monthly", day_of_month=28, send_time_utc="09:00", from_time=ref)
    assert r == "2026-02-28T09:00:00+00:00"


# ---------------------------------------------------------------------------
# Defaults + invalid inputs
# ---------------------------------------------------------------------------


def test_default_send_time_is_0900_utc_when_missing():
    ref = _dt(2026, 4, 27, 0, 0)
    r = compute_next_send("weekly", day_of_week=0, from_time=ref)
    assert r == "2026-04-27T09:00:00+00:00"


def test_weekly_requires_day_of_week():
    with pytest.raises(ValueError, match="day_of_week"):
        compute_next_send("weekly", send_time_utc="09:00", from_time=_dt(2026, 4, 27))


def test_weekly_day_out_of_range_rejected():
    with pytest.raises(ValueError):
        compute_next_send("weekly", day_of_week=7, from_time=_dt(2026, 4, 27))
    with pytest.raises(ValueError):
        compute_next_send("weekly", day_of_week=-1, from_time=_dt(2026, 4, 27))


def test_monthly_requires_day_of_month():
    with pytest.raises(ValueError, match="day_of_month"):
        compute_next_send("monthly", send_time_utc="09:00", from_time=_dt(2026, 4, 27))


def test_monthly_day_29plus_rejected():
    with pytest.raises(ValueError):
        compute_next_send("monthly", day_of_month=29, from_time=_dt(2026, 4, 27))
    with pytest.raises(ValueError):
        compute_next_send("monthly", day_of_month=31, from_time=_dt(2026, 4, 27))
    with pytest.raises(ValueError):
        compute_next_send("monthly", day_of_month=0, from_time=_dt(2026, 4, 27))


def test_invalid_cadence_rejected():
    with pytest.raises(ValueError):
        compute_next_send("daily", from_time=_dt(2026, 4, 27))  # type: ignore[arg-type]


def test_iso_string_from_time_accepted():
    r = compute_next_send("weekly", day_of_week=0, send_time_utc="09:00",
                          from_time="2026-04-27T08:00:00+00:00")
    assert r == "2026-04-27T09:00:00+00:00"


def test_iso_z_suffix_accepted():
    r = compute_next_send("weekly", day_of_week=0, send_time_utc="09:00",
                          from_time="2026-04-27T08:00:00Z")
    assert r == "2026-04-27T09:00:00+00:00"


# ---------------------------------------------------------------------------
# cadence_interval + dedup window
# ---------------------------------------------------------------------------


def test_cadence_intervals():
    assert cadence_interval("weekly") == timedelta(days=7)
    assert cadence_interval("monthly") == timedelta(days=30)
    assert cadence_interval("once") == timedelta(days=30)
    with pytest.raises(ValueError):
        cadence_interval("daily")  # type: ignore[arg-type]


def test_dedup_window_is_half_cadence_before_now():
    # weekly = 7 days, so half-window = 3.5 days
    start = dedup_window_start("weekly", now_iso="2026-04-27T09:00:00+00:00")
    # 3.5 days before = 2026-04-23T21:00:00
    assert start == "2026-04-23T21:00:00+00:00"


def test_dedup_window_monthly():
    # monthly = 30 days, half = 15 days
    start = dedup_window_start("monthly", now_iso="2026-04-27T00:00:00+00:00")
    assert start == "2026-04-12T00:00:00+00:00"
