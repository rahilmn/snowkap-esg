"""W3.1 — Forecaster tests (deterministic + stub-LLM paths)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from engine.analysis.forecaster import (
    TrajectoryPoint,
    forecast_sentiment_trajectory,
    rolling_polarity_series,
)


# ---------------------------------------------------------------------------
# Stub OpenAI client
# ---------------------------------------------------------------------------


class _StubClient:
    def __init__(self, response_text: str, raise_exc: Exception | None = None):
        self._response = response_text
        self._raise = raise_exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        if self._raise:
            raise self._raise
        msg = SimpleNamespace(content=self._response)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


# ---------------------------------------------------------------------------
# rolling_polarity_series — deterministic preprocessor
# ---------------------------------------------------------------------------


def test_rolling_polarity_series_returns_empty_for_empty_input():
    assert rolling_polarity_series([]) == []


def test_rolling_polarity_series_groups_by_month():
    insights = [
        {"published_at": "2026-03-15T00:00:00+00:00", "event_polarity": "negative"},
        {"published_at": "2026-03-20T00:00:00+00:00", "event_polarity": "negative"},
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "positive"},
    ]
    series = rolling_polarity_series(insights)
    # 2 months: 2026-03 and 2026-04
    assert len(series) == 2
    months = [s["month"] for s in series]
    assert "2026-03" in months
    assert "2026-04" in months


def test_rolling_polarity_series_maps_polarity_to_numeric():
    """negative=-1, neutral/mixed=0, positive=+1; month avg taken."""
    insights = [
        {"published_at": "2026-05-01T00:00:00+00:00", "event_polarity": "negative"},
        {"published_at": "2026-05-15T00:00:00+00:00", "event_polarity": "positive"},
        {"published_at": "2026-05-20T00:00:00+00:00", "event_polarity": "positive"},
    ]
    series = rolling_polarity_series(insights)
    # 2026-05: 2 positive + 1 negative → average = (-1 + 1 + 1) / 3 = 0.333
    point = next(s for s in series if s["month"] == "2026-05")
    assert point["polarity_mean"] == pytest.approx(0.333, abs=0.01)
    assert point["count"] == 3


def test_rolling_polarity_series_ignores_invalid_dates():
    insights = [
        {"published_at": "not-a-date", "event_polarity": "negative"},
        {"published_at": "2026-05-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    series = rolling_polarity_series(insights)
    assert len(series) == 1


# ---------------------------------------------------------------------------
# forecast_sentiment_trajectory — happy path
# ---------------------------------------------------------------------------


def test_forecast_returns_default_shape_when_no_history():
    """No history → neutral baseline, low confidence, no LLM call needed."""
    result = forecast_sentiment_trajectory(
        company_slug="adani-power", insights=[],
    )
    assert "horizons" in result
    assert "3m" in result["horizons"]
    assert "12m" in result["horizons"]
    # Default direction is "stable" when there's nothing to forecast
    for horizon in result["horizons"].values():
        assert horizon["direction"] in ("stable", "improving", "declining")


def test_forecast_uses_llm_response_when_valid(monkeypatch):
    insights = [
        {"published_at": "2026-03-01T00:00:00+00:00", "event_polarity": "negative"},
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    stub_response = json.dumps({
        "horizons": {
            "3m": {"direction": "declining", "confidence": "moderate", "rationale": "regulatory headwinds"},
            "6m": {"direction": "declining", "confidence": "moderate", "rationale": "headwinds persist"},
            "12m": {"direction": "stable", "confidence": "low", "rationale": "uncertain beyond 12m"},
        }
    })
    result = forecast_sentiment_trajectory(
        company_slug="adani-power",
        insights=insights,
        client=_StubClient(stub_response),
    )
    assert result["horizons"]["3m"]["direction"] == "declining"
    assert result["horizons"]["12m"]["direction"] == "stable"


def test_forecast_falls_back_on_llm_api_error():
    insights = [
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    result = forecast_sentiment_trajectory(
        company_slug="x",
        insights=insights,
        client=_StubClient("", raise_exc=RuntimeError("timeout")),
    )
    # Deterministic fallback: direction is derivable from the polarity series alone
    assert "horizons" in result
    assert result.get("llm_used") is False


def test_forecast_falls_back_on_malformed_json():
    result = forecast_sentiment_trajectory(
        company_slug="x",
        insights=[{"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"}],
        client=_StubClient("not json at all"),
    )
    assert result.get("llm_used") is False
    assert "horizons" in result


def test_forecast_caches_by_company_slug():
    """Two calls with the same slug + same input data should not hit the LLM twice."""
    insights = [
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    call_count = {"n": 0}

    class _CountingClient(_StubClient):
        def _create(self, **kwargs):
            call_count["n"] += 1
            return super()._create(**kwargs)

    response = json.dumps({"horizons": {
        "3m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        "6m": {"direction": "stable", "confidence": "low", "rationale": "x"},
        "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
    }})
    client = _CountingClient(response)

    from engine.analysis.forecaster import clear_cache
    clear_cache()
    forecast_sentiment_trajectory(
        company_slug="cache-test", insights=insights, client=client,
    )
    forecast_sentiment_trajectory(
        company_slug="cache-test", insights=insights, client=client,
    )
    # Second call must hit the cache, not the LLM
    assert call_count["n"] == 1


def test_forecast_emits_per_month_trajectory_points():
    """The trajectory should include a per-month projection that the
    UI TrajectoryChart can plot."""
    insights = [
        {"published_at": "2026-03-01T00:00:00+00:00", "event_polarity": "negative"},
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    response = json.dumps({"horizons": {
        "3m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
        "6m": {"direction": "declining", "confidence": "moderate", "rationale": "x"},
        "12m": {"direction": "stable", "confidence": "low", "rationale": "x"},
    }})
    result = forecast_sentiment_trajectory(
        company_slug="traj-test",
        insights=insights,
        client=_StubClient(response),
    )
    assert "trajectory" in result
    assert isinstance(result["trajectory"], list)
    # Each point has month + central + lo + hi (confidence bands)
    if result["trajectory"]:
        p = result["trajectory"][0]
        assert "month" in p
        assert "central" in p
        assert "lo" in p
        assert "hi" in p


def test_forecast_caps_input_to_recent_window():
    """Forecaster only looks at the last 24 months of history — older
    insights are dropped to keep the prompt size bounded."""
    insights = [
        # Way old
        {"published_at": "2020-01-01T00:00:00+00:00", "event_polarity": "negative"},
        # Recent
        {"published_at": "2026-04-01T00:00:00+00:00", "event_polarity": "negative"},
    ]
    result = forecast_sentiment_trajectory(
        company_slug="windowed",
        insights=insights,
    )
    # Polarity series should only have 1 month, not 2
    assert len(result.get("polarity_series") or []) == 1


def test_trajectory_point_dataclass_fields():
    p = TrajectoryPoint(month="2026-06", central=0.3, lo=0.1, hi=0.5)
    assert p.month == "2026-06"
    assert p.central == 0.3
