"""Phase 51.L — severity/negativity-aware priority-brief selection.

The overnight batch runs the full pipeline (deep insight + recommendations) on
only the top-N (3) articles per company/day, chosen by
``article_selector.select_top_n_for_pipeline``. The old score was a flat ESG
keyword-density heuristic — every keyword weighed the same — so a green-growth
story ("solar/renewable/capacity/climate") could outrank a genuinely critical
NEGATIVE event (penalty, violation, spill, fraud, community harm) and steal the
priority-brief slot. This adds a deterministic (no-LLM) severity + negativity
boost so business-impacting downside wins the slot.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from engine.analysis.article_selector import (
    select_top_n_for_pipeline,
    _score_article,
    _negativity_density,
    _event_severity_excess,
)
from engine.analysis.deck_builder import _rank_composite


@dataclass
class _FakeArticle:
    id: str
    title: str = ""
    content: str = ""
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: str = "2026-06-10T00:00:00+00:00"


# A green-growth story stuffed with ESG keywords (high flat-relevance, no harm).
_GREEN = _FakeArticle(
    id="green",
    title="Adani Power solar renewable wind capacity expansion",
    content=("solar renewable wind climate carbon emission scope 1 scope 2 "
             "scope 3 circular recycle sustainability green energy capacity"),
)
# A genuinely critical NEGATIVE event (regulatory enforcement + disclosure harm).
_NEGATIVE = _FakeArticle(
    id="negative",
    title="SEBI fines Adani Power, issues show cause notice for disclosure violation",
    content=("The regulator imposed a penalty and flagged a compliance "
             "violation over an alleged disclosure breach."),
)


class TestNegativityWins:
    def test_negative_event_outranks_green_growth(self):
        # Even though the green story has far more ESG keywords, the negative
        # enforcement event must win the single priority-brief slot.
        result = select_top_n_for_pipeline([_GREEN, _NEGATIVE], n=1)
        assert len(result) == 1
        assert result[0].id == "negative"

    def test_both_kept_when_two_slots(self):
        result = select_top_n_for_pipeline([_GREEN, _NEGATIVE], n=2)
        ids = {getattr(a, "id", None) for a in result}
        assert ids == {"green", "negative"}

    def test_negative_scores_higher(self):
        sg = _score_article(_GREEN, None)
        sn = _score_article(_NEGATIVE, None)
        assert sn.score > sg.score
        assert sn.criticality_boost > sg.criticality_boost


class TestRoutinePreserved:
    def test_among_neutral_higher_keyword_density_still_wins(self):
        rich = _FakeArticle(
            id="rich",
            title="climate carbon emission scope 3 water biodiversity renewable",
            content="ghg scope 1 scope 2 pollution waste recycle",
        )
        sparse = _FakeArticle(id="sparse", title="company holds annual general meeting")
        result = select_top_n_for_pipeline([sparse, rich], n=1)
        assert result[0].id == "rich"

    def test_empty_input(self):
        assert select_top_n_for_pipeline([], n=3) == []


class TestSignalFunctions:
    def test_negativity_density_flags_harm(self):
        assert _negativity_density("oil spill contaminates river", "", "leak penalty fine") > 0.5
        assert _negativity_density("child labour found in supply chain", "", "") > 0.0

    def test_negativity_density_zero_for_positive(self):
        assert _negativity_density("new solar plant boosts renewable capacity", "", "") == 0.0

    def test_negativity_density_empty(self):
        assert _negativity_density("", "", "") == 0.0

    def test_event_severity_excess_in_range_and_safe(self):
        # Never raises; always in [0,1]. (Exact value depends on ontology rules.)
        v = _event_severity_excess("SEBI imposes heavy penalty for violation", "")
        assert 0.0 <= v <= 1.0
        v2 = _event_severity_excess("", "")
        assert 0.0 <= v2 <= 1.0

    def test_scored_article_exposes_boost(self):
        sa = _score_article(_NEGATIVE, None)
        assert 0.0 <= sa.criticality_boost <= 1.0
        assert "crit_boost" in sa.rank_reason


def _proc(band: str, *, sentiment: int = 0, floor: float = 2.0, score: float = 0.5):
    """A minimal post-stages-1-9 PipelineResult stub for _rank_composite."""
    return SimpleNamespace(
        criticality={"band": band, "score": score},
        nlp=SimpleNamespace(sentiment=sentiment),
        event=SimpleNamespace(score_floor=floor),
    )


class TestRankComposite:
    """The deck's final top-3 sort: band dominates, then event-severity +
    negative-sentiment, then score."""

    def test_band_dominates_over_severity(self):
        # A HIGH-band routine article still outranks a MEDIUM-band severe one —
        # the band reflects severity already; the boost only breaks within-band ties.
        high_routine = _proc("HIGH", floor=2, score=0.1)
        medium_severe = _proc("MEDIUM", floor=8, sentiment=-1, score=0.9)
        assert _rank_composite(high_routine) > _rank_composite(medium_severe)

    def test_severity_breaks_tie_within_band(self):
        bland = _proc("MEDIUM", floor=2, score=0.5)        # routine
        severe = _proc("MEDIUM", floor=8, score=0.5)       # criminal indictment-grade
        assert _rank_composite(severe) > _rank_composite(bland)

    def test_negative_sentiment_lifts_within_band(self):
        neutral = _proc("HIGH", sentiment=0, floor=2, score=0.5)
        negative = _proc("HIGH", sentiment=-1, floor=2, score=0.5)
        assert _rank_composite(negative) > _rank_composite(neutral)

    def test_missing_event_safe(self):
        r = SimpleNamespace(criticality={"band": "LOW", "score": 0.2}, nlp=None, event=None)
        assert _rank_composite(r) >= 0.0
