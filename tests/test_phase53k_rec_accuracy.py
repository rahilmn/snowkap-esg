"""Phase 53 (K) — recommendation accuracy: no backdated deadlines in prose.

The live gpt-5 audit found criticals shipping recommendations whose TITLE/
description embedded a calendar date BEFORE the article's publish date ("File
supplementary BRSR P6 by 2026-06-15", "Create disclosure audit log by
2026-05-31"). _fix_deadline only repairs the structured ``deadline`` field, so
the prose date slipped through. _scrub_backdated_prose_dates rewrites any past
YYYY-MM-DD in the prose to the rec's fixed (future) deadline.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from engine.analysis.recommendation_engine import (
    Recommendation,
    _scrub_backdated_prose_dates,
)


def _rec(**over) -> Recommendation:
    vals = {}
    for f in dataclasses.fields(Recommendation):
        t = str(f.type)
        vals[f.name] = "" if "str" in t else (0 if ("int" in t or "float" in t) else [])
    vals.update(over)
    return Recommendation(**vals)


def test_backdated_prose_date_rewritten_to_future_deadline():
    future = (date.today() + timedelta(days=120)).isoformat()
    r = _rec(
        title="File supplementary BRSR P6 disclosure by 2020-01-15",
        description="Create disclosure audit log by 2019-05-31 and brief board",
        profitability_link="avoid penalty",
        deadline=future,
    )
    _scrub_backdated_prose_dates(r)
    assert "2020-01-15" not in r.title and future in r.title
    assert "2019-05-31" not in r.description and future in r.description


def test_future_prose_date_untouched():
    future = (date.today() + timedelta(days=200)).isoformat()
    keep = (date.today() + timedelta(days=30)).isoformat()
    r = _rec(title=f"Issue green bond by {keep}", description="", deadline=future)
    _scrub_backdated_prose_dates(r)
    assert keep in r.title  # a future date in prose is left as-is


def test_no_date_in_prose_is_noop():
    r = _rec(title="Commission third-party BRSR assurance", description="brief board",
             deadline=(date.today() + timedelta(days=90)).isoformat())
    _scrub_backdated_prose_dates(r)
    assert r.title == "Commission third-party BRSR assurance"
