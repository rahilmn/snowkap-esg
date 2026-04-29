"""Phase 1 ingestion tests: freshness gate, semantic dedup, demo_ready."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.ingestion.dedup import (
    SemanticDedup,
    filter_duplicates,
    is_fresh,
    jaccard_similarity,
)


# ---------------------------------------------------------------------------
# Freshness gate
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def test_is_fresh_recent_article():
    now = datetime.now(timezone.utc)
    article = {"published_at": _iso(now - timedelta(days=10))}
    assert is_fresh(article, max_age_days=90) is True


def test_is_fresh_old_article_rejected():
    now = datetime.now(timezone.utc)
    article = {"published_at": _iso(now - timedelta(days=120))}
    assert is_fresh(article, max_age_days=90) is False


def test_is_fresh_boundary_exactly_90_days():
    now = datetime.now(timezone.utc)
    article = {"published_at": _iso(now - timedelta(days=90))}
    assert is_fresh(article, max_age_days=90, now=now) is True


def test_is_fresh_fails_open_on_bad_timestamp():
    article = {"published_at": "not-a-date"}
    assert is_fresh(article, max_age_days=90) is True


def test_is_fresh_handles_z_suffix():
    now = datetime.now(timezone.utc)
    article = {"published_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    assert is_fresh(article, max_age_days=90, now=now) is True


# ---------------------------------------------------------------------------
# Jaccard similarity primitive
# ---------------------------------------------------------------------------


def test_jaccard_identical():
    a = frozenset({"coal", "india", "regulation"})
    assert jaccard_similarity(a, a) == 1.0


def test_jaccard_disjoint():
    a = frozenset({"coal", "india"})
    b = frozenset({"wheat", "brazil"})
    assert jaccard_similarity(a, b) == 0.0


def test_jaccard_partial_overlap():
    a = frozenset({"coal", "india", "regulation"})
    b = frozenset({"coal", "india", "market"})
    # intersection 2, union 4 => 0.5
    assert jaccard_similarity(a, b) == 0.5


def test_jaccard_empty_inputs():
    assert jaccard_similarity(frozenset(), frozenset({"a"})) == 0.0


# ---------------------------------------------------------------------------
# Semantic dedup
# ---------------------------------------------------------------------------


def test_dedup_collapses_near_duplicates():
    """Typical syndicated wire republication — same story, minor edits."""
    now = datetime.now(timezone.utc)
    articles = [
        {
            "title": "SEBI fines Adani Power 100 crore for disclosure lapse",
            "summary": "Regulator imposes 100 crore penalty on Adani Power over disclosure lapse.",
            "url": "https://reuters.com/a1",
            "published_at": _iso(now),
        },
        {
            "title": "SEBI fines Adani Power 100 crore over disclosure lapse",
            "summary": "Regulator imposes penalty of 100 crore on Adani Power for disclosure lapse.",
            "url": "https://mint.com/a2",
            "published_at": _iso(now + timedelta(hours=2)),
        },
    ]
    result = filter_duplicates(articles, threshold=0.75, window_hours=48)
    assert len(result) == 1
    assert result[0]["url"] == "https://reuters.com/a1"


def test_dedup_conceptually_similar_not_same_story():
    """Different wordings of a similar concept should NOT be flagged at 0.75."""
    now = datetime.now(timezone.utc)
    articles = [
        {
            "title": "Power sector rides summer demand surge in India",
            "summary": "Demand for electricity hits new peak as temperatures climb.",
            "url": "https://example.com/a1",
            "published_at": _iso(now),
        },
        {
            "title": "Indian power companies see peak summer demand surge",
            "summary": "Electricity demand surges to new peak amid heat.",
            "url": "https://example.com/a2",
            "published_at": _iso(now + timedelta(hours=2)),
        },
    ]
    # Both kept at threshold 0.75 — conceptually similar but not wire-republication
    result = filter_duplicates(articles, threshold=0.75, window_hours=48)
    assert len(result) == 2


def test_dedup_keeps_distinct_articles():
    now = datetime.now(timezone.utc)
    articles = [
        {
            "title": "Adani Power announces coal capacity expansion",
            "summary": "The company plans 500 MW new coal unit.",
            "url": "https://example.com/a1",
            "published_at": _iso(now),
        },
        {
            "title": "JSW Energy wins green hydrogen tender",
            "summary": "JSW secures a major hydrogen production contract.",
            "url": "https://example.com/a2",
            "published_at": _iso(now + timedelta(hours=1)),
        },
    ]
    result = filter_duplicates(articles, threshold=0.75, window_hours=48)
    assert len(result) == 2


def test_dedup_respects_time_window():
    """Articles outside the window are not compared, even if near-identical."""
    now = datetime.now(timezone.utc)
    articles = [
        {
            "title": "ICICI Bank GST demand notice issued",
            "summary": "Tax authorities issue GST notice to ICICI Bank.",
            "url": "https://example.com/a1",
            "published_at": _iso(now - timedelta(hours=72)),  # outside 48h window
        },
        {
            "title": "ICICI Bank GST demand notice issued",
            "summary": "Tax authorities issue GST notice to ICICI Bank.",
            "url": "https://example.com/a2",
            "published_at": _iso(now),
        },
    ]
    result = filter_duplicates(articles, threshold=0.75, window_hours=48)
    # Both kept: first one is pruned from index before second is checked
    assert len(result) == 2


def test_dedup_same_title_different_time_within_window():
    now = datetime.now(timezone.utc)
    dedup = SemanticDedup(threshold=0.75, window_hours=48)
    art1 = {
        "title": "SEBI fines Adani Power for disclosure lapse",
        "summary": "Regulator imposes penalty over disclosure.",
        "url": "https://a.com/1",
        "published_at": _iso(now),
    }
    art2 = {
        "title": "SEBI fines Adani Power for disclosure lapse",
        "summary": "Regulator imposes penalty over disclosure.",
        "url": "https://b.com/1",
        "published_at": _iso(now + timedelta(hours=24)),
    }
    assert dedup.is_duplicate(art1) == (False, None)
    is_dup, match = dedup.is_duplicate(art2)
    assert is_dup is True
    assert match == "https://a.com/1"


def test_dedup_empty_title_no_false_positive():
    now = datetime.now(timezone.utc)
    dedup = SemanticDedup(threshold=0.75, window_hours=48)
    art1 = {"title": "", "summary": "", "url": "https://a.com/1", "published_at": _iso(now)}
    art2 = {"title": "", "summary": "", "url": "https://b.com/1", "published_at": _iso(now)}
    assert dedup.is_duplicate(art1) == (False, None)
    # With no tokens, we don't flag as dup
    assert dedup.is_duplicate(art2) == (False, None)


# ---------------------------------------------------------------------------
# demo_ready gate
# ---------------------------------------------------------------------------


def test_demo_ready_passes_full_criteria():
    from engine.analysis.relevance_scorer import (
        TIER_HOME,
        RelevanceScore,
        is_demo_ready,
    )

    now = datetime.now(timezone.utc)
    score = RelevanceScore(
        total=9,
        tier=TIER_HOME,
        esg_correlation=2,
        financial_impact=2,
        compliance_risk=2,
        supply_chain_impact=2,
        people_impact=1,
        materiality_weight=0.9,
        adjusted_total=9.0,
    )
    ok, reason = is_demo_ready(
        score,
        published_at=_iso(now - timedelta(hours=24)),
        computed_exposure_cr=50.0,
        now=now,
    )
    assert ok is True
    assert reason == ""


def test_demo_ready_fails_on_stale_article():
    from engine.analysis.relevance_scorer import (
        TIER_HOME,
        RelevanceScore,
        is_demo_ready,
    )

    now = datetime.now(timezone.utc)
    score = RelevanceScore(
        total=9,
        tier=TIER_HOME,
        esg_correlation=2,
        financial_impact=2,
        compliance_risk=2,
        supply_chain_impact=2,
        people_impact=1,
        materiality_weight=0.9,
        adjusted_total=9.0,
    )
    ok, reason = is_demo_ready(
        score,
        published_at=_iso(now - timedelta(hours=96)),  # too old
        computed_exposure_cr=50.0,
        now=now,
    )
    assert ok is False
    assert "age" in reason


def test_demo_ready_fails_on_secondary_tier():
    from engine.analysis.relevance_scorer import (
        TIER_SECONDARY,
        RelevanceScore,
        is_demo_ready,
    )

    now = datetime.now(timezone.utc)
    score = RelevanceScore(
        total=5,
        tier=TIER_SECONDARY,
        esg_correlation=1,
        financial_impact=1,
        compliance_risk=1,
        supply_chain_impact=1,
        people_impact=1,
        materiality_weight=0.5,
        adjusted_total=5.0,
    )
    ok, reason = is_demo_ready(
        score,
        published_at=_iso(now),
        computed_exposure_cr=50.0,
        now=now,
    )
    assert ok is False
    assert "tier" in reason


def test_demo_ready_fails_on_low_exposure():
    from engine.analysis.relevance_scorer import (
        TIER_HOME,
        RelevanceScore,
        is_demo_ready,
    )

    now = datetime.now(timezone.utc)
    score = RelevanceScore(
        total=9,
        tier=TIER_HOME,
        esg_correlation=2,
        financial_impact=2,
        compliance_risk=2,
        supply_chain_impact=2,
        people_impact=1,
        materiality_weight=0.9,
        adjusted_total=9.0,
    )
    ok, reason = is_demo_ready(
        score,
        published_at=_iso(now),
        computed_exposure_cr=5.0,  # below min
        now=now,
    )
    assert ok is False
    assert "exposure" in reason


def test_demo_ready_skips_exposure_when_not_provided():
    """If caller hasn't run cascade yet, exposure check is skipped."""
    from engine.analysis.relevance_scorer import (
        TIER_HOME,
        RelevanceScore,
        is_demo_ready,
    )

    now = datetime.now(timezone.utc)
    score = RelevanceScore(
        total=9,
        tier=TIER_HOME,
        esg_correlation=2,
        financial_impact=2,
        compliance_risk=2,
        supply_chain_impact=2,
        people_impact=1,
        materiality_weight=0.9,
        adjusted_total=9.0,
    )
    ok, _ = is_demo_ready(
        score,
        published_at=_iso(now),
        computed_exposure_cr=None,
        now=now,
    )
    assert ok is True
