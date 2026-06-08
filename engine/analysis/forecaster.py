"""W3.1 — Sentiment trajectory forecaster (OpenAI-native).

This is the Snowkap-native alternative to embedding MiroFish (avoids
AGPL exposure, no Zep Cloud reintroduction). Given a company's recent
insight history, projects 3 / 6 / 12-month sentiment direction with
confidence bands.

Consumers (all wired as of Phase 27):

  - ``engine.analysis.criticality_scorer`` — output feeds the 7th
    component ``sentiment_trajectory`` via
    ``score(..., forecaster_output=...)``.
  - ``engine.analysis.insight_generator`` — calls this for every
    HOME-tier article, stamps the result on the insight dict under
    ``sentiment_trajectory``, and passes it through
    ``criticality_integration.score_at_insight_time`` so the score
    incorporates it.
  - ``engine.governance.belief_revision`` rule R5 — caller passes
    ``forecaster_output=...`` to ``revise_from_article`` (the
    ``CompanyAgent`` orchestrator does this from
    ``insight_generator``) so the BeliefCoach can propose a HIGH
    risk-band when 3m AND 6m horizons both decline at confidence
    ≥ moderate.
  - Frontend ``TrajectoryChart`` + ``StrategicHorizonPanel`` (read
    ``insight.sentiment_trajectory`` from the JSON payload, written
    at schema_version ``2.3-trajectory-stamped``).

The function is pure-ish: a module-level cache keyed by
(company_slug, content-hash) prevents redundant LLM calls. Cache is
cleared by tests via `clear_cache()`.

Failure modes (all fall back to deterministic baseline):
  - OpenAI API error / timeout
  - Malformed JSON response
  - Schema-violating LLM output (unknown direction enum, etc.)
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


Direction = Literal["improving", "stable", "declining"]
Confidence = Literal["low", "moderate", "high"]


@dataclass
class TrajectoryPoint:
    """One projected point in the future trajectory (per month)."""
    month: str          # YYYY-MM
    central: float      # mean projected polarity in [-1, 1]
    lo: float           # 68% lower bound
    hi: float           # 68% upper bound


# ---------------------------------------------------------------------------
# Polarity series — deterministic preprocessor
# ---------------------------------------------------------------------------


_POLARITY_MAP = {
    "negative": -1.0,
    "positive": 1.0,
    "neutral": 0.0,
    "mixed": 0.0,
}


def _polarity_value(raw: Any) -> float:
    """Map polarity string → numeric value."""
    if isinstance(raw, (int, float)):
        return float(raw)
    return _POLARITY_MAP.get(str(raw or "").lower().strip(), 0.0)


def rolling_polarity_series(
    insights: Iterable[dict[str, Any]],
    *,
    window_months: int = 24,
) -> list[dict[str, Any]]:
    """Group insights by month, compute mean polarity per month.

    Returns a list of {month, polarity_mean, count} dicts, ordered
    by month ascending. Insights with invalid `published_at` are skipped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_months * 31)

    by_month: dict[str, list[float]] = defaultdict(list)
    for ins in insights:
        if not isinstance(ins, dict):
            continue
        ts_raw = ins.get("published_at") or ""
        try:
            # Tolerate trailing Z and tz suffixes
            ts = ts_raw.rstrip("Z")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if dt < cutoff:
            continue
        month_key = f"{dt.year:04d}-{dt.month:02d}"
        by_month[month_key].append(_polarity_value(ins.get("event_polarity")))

    series: list[dict[str, Any]] = []
    for month_key in sorted(by_month):
        vals = by_month[month_key]
        series.append({
            "month": month_key,
            "polarity_mean": sum(vals) / len(vals),
            "count": len(vals),
        })
    return series


# ---------------------------------------------------------------------------
# Trajectory projection (deterministic fallback)
# ---------------------------------------------------------------------------


def _deterministic_direction(series: list[dict[str, Any]]) -> Direction:
    """Linear-trend heuristic when LLM is unavailable.

    Compares the latest 3-month average to the prior 3-month average:
      > +0.15 → improving
      < -0.15 → declining
      else    → stable
    """
    if len(series) < 2:
        return "stable"
    recent = series[-3:]
    older = series[-6:-3] if len(series) >= 6 else series[:-3]
    if not recent or not older:
        return "stable"
    recent_avg = sum(s["polarity_mean"] for s in recent) / len(recent)
    older_avg = sum(s["polarity_mean"] for s in older) / len(older)
    delta = recent_avg - older_avg
    if delta > 0.15:
        return "improving"
    if delta < -0.15:
        return "declining"
    return "stable"


def _deterministic_horizons(series: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a horizons dict from the deterministic direction.

    The same direction is repeated across 3m / 6m / 12m with decaying
    confidence — a crude but honest fallback when no LLM is available.
    """
    direction = _deterministic_direction(series)
    return {
        "3m": {"direction": direction, "confidence": "moderate", "rationale": "deterministic linear-trend fallback"},
        "6m": {"direction": direction, "confidence": "low", "rationale": "deterministic linear-trend fallback"},
        "12m": {"direction": "stable", "confidence": "low", "rationale": "horizon too distant for deterministic projection"},
    }


def _build_trajectory_points(
    series: list[dict[str, Any]],
    horizons: dict[str, dict[str, Any]],
) -> list[TrajectoryPoint]:
    """Project per-month trajectory points 12 months into the future.

    Central path interpolates linearly between the latest observed
    polarity and the 12-month horizon target. Confidence bands widen
    linearly with horizon distance.
    """
    if not series:
        return []

    last = series[-1]
    last_year, last_month = (int(x) for x in last["month"].split("-"))
    start = last["polarity_mean"]

    direction_3m = horizons.get("3m", {}).get("direction", "stable")
    direction_12m = horizons.get("12m", {}).get("direction", "stable")

    target_3m = start + _direction_delta(direction_3m, magnitude=0.3)
    target_12m = start + _direction_delta(direction_12m, magnitude=0.5)

    points: list[TrajectoryPoint] = []
    for i in range(1, 13):
        # Linear interp from start through 3m target to 12m target
        if i <= 3:
            ratio = i / 3
            central = start + (target_3m - start) * ratio
        else:
            ratio = (i - 3) / 9
            central = target_3m + (target_12m - target_3m) * ratio
        # Confidence bands widen with horizon
        band = 0.05 + 0.04 * i
        ay = last_year
        am = last_month + i
        while am > 12:
            am -= 12
            ay += 1
        points.append(TrajectoryPoint(
            month=f"{ay:04d}-{am:02d}",
            central=max(-1.0, min(1.0, central)),
            lo=max(-1.0, central - band),
            hi=min(1.0, central + band),
        ))
    return points


def _direction_delta(direction: Direction, *, magnitude: float = 0.3) -> float:
    return {"improving": magnitude, "declining": -magnitude, "stable": 0.0}.get(direction, 0.0)


# ---------------------------------------------------------------------------
# LLM call + prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You forecast ESG sentiment trajectory for a listed
company over 3, 6, and 12-month horizons.

Inputs you will see:
  - company_slug (string)
  - polarity_series: list of {month, polarity_mean (in [-1,1]), count}
    representing the rolling monthly average of ESG event polarity over
    the last 24 months

Your output must be a single JSON object with ONE key `horizons` mapping
to {3m, 6m, 12m}, each containing:
  - direction: "improving" | "stable" | "declining"
  - confidence: "low" | "moderate" | "high"
  - rationale: one-line explanation (≤ 30 words)

OUTPUT JSON ONLY. No prose outside the JSON.
"""

_VALID_DIRECTIONS = {"improving", "stable", "declining"}
_VALID_CONFIDENCES = {"low", "moderate", "high"}


def _build_user_prompt(company_slug: str, series: list[dict[str, Any]]) -> str:
    return (
        f"company_slug: {company_slug}\n\n"
        f"polarity_series:\n{json.dumps(series, indent=2)}\n"
    )


def _parse_horizons(raw: str) -> dict[str, dict[str, Any]] | None:
    """Validate the LLM response. Returns None on any schema violation."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    horizons = parsed.get("horizons") if isinstance(parsed, dict) else None
    if not isinstance(horizons, dict):
        return None
    out: dict[str, dict[str, Any]] = {}
    for key in ("3m", "6m", "12m"):
        h = horizons.get(key)
        if not isinstance(h, dict):
            return None
        d = h.get("direction")
        c = h.get("confidence")
        if d not in _VALID_DIRECTIONS or c not in _VALID_CONFIDENCES:
            return None
        out[key] = {
            "direction": d,
            "confidence": c,
            "rationale": str(h.get("rationale") or "")[:200],
        }
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


_CACHE: dict[str, dict[str, Any]] = {}


def clear_cache() -> None:
    """Used by tests to force a fresh LLM call."""
    _CACHE.clear()


def _cache_key(company_slug: str, series: list[dict[str, Any]]) -> str:
    payload = json.dumps({"slug": company_slug, "series": series}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def forecast_sentiment_trajectory(
    *,
    company_slug: str,
    insights: Iterable[dict[str, Any]],
    window_months: int = 24,
    model: str = "gpt-4.1-mini",
    temperature: float = 0.1,
    max_tokens: int = 400,
    client: Any = None,
) -> dict[str, Any]:
    """Forecast 3 / 6 / 12-month sentiment direction for a tenant.

    Returns dict shape:
        {
          "company_slug": str,
          "polarity_series": list[{month, polarity_mean, count}],
          "horizons": {3m, 6m, 12m: {direction, confidence, rationale}},
          "trajectory": list[TrajectoryPoint as dict],
          "llm_used": bool,
        }
    """
    series = rolling_polarity_series(insights, window_months=window_months)

    # Cache lookup
    key = _cache_key(company_slug, series)
    if key in _CACHE:
        return _CACHE[key]

    horizons: dict[str, dict[str, Any]] | None = None
    llm_used = False

    # No history → skip LLM entirely
    if not series:
        horizons = _deterministic_horizons(series)
    else:
        # Phase v1.1 — route sentiment-trajectory forecasting through the
        # search_aided task class. When OPENROUTER_API_KEY is set this
        # routes to Perplexity Sonar-pro which grounds the forecast in
        # live web search results — material precision win over training-
        # data extrapolation for ESG context that changes weekly. Falls
        # back to direct OpenAI when the key is absent.
        if client is None:
            try:
                from engine.llm import get_llm_client
                _llm = get_llm_client(task_class="search_aided")
                client = _llm.sync
                # Override the model so Perplexity's identifier wins when
                # OpenRouter is active; falls back to gpt-4.1 on direct
                # OpenAI mode per the legacy routing table.
                model = _llm.model_for()
            except Exception as exc:  # noqa: BLE001
                logger.debug("forecaster: client init failed: %s", exc)
                client = None

        if client is not None:
            try:
                completion = client.chat.completions.create(
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": _build_user_prompt(company_slug, series)},
                    ],
                    response_format={"type": "json_object"},
                )
                from engine.models.llm_calls import log_openai_usage
                log_openai_usage(completion, model=model, stage="forecaster")
                raw = completion.choices[0].message.content or ""
                parsed = _parse_horizons(raw)
                if parsed is not None:
                    horizons = parsed
                    llm_used = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("forecaster: LLM call failed for %s: %s", company_slug, exc)

        if horizons is None:
            horizons = _deterministic_horizons(series)

    trajectory = [
        {"month": p.month, "central": p.central, "lo": p.lo, "hi": p.hi}
        for p in _build_trajectory_points(series, horizons)
    ]

    result = {
        "company_slug": company_slug,
        "polarity_series": series,
        "horizons": horizons,
        "trajectory": trajectory,
        "llm_used": llm_used,
    }
    _CACHE[key] = result
    return result
