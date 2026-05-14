"""Phase 6 §8.3 — feed re-ranker.

Applies persona modulation to a list of feed rows (returned by
``sqlite_index.query_feed``) using the row's stored ``criticality_score``
as the base. The ranking shifts but no row is dropped — CRITICAL articles
are guaranteed to remain visible (Phase 6 design choice: personalization
changes prominence, never discoverability).

Inputs:
  - rows: list of feed-row dicts (as returned by ``sqlite_index.query_feed``)
  - persona: the user's Persona
  - load_payload: callable ``(json_path) -> insight_payload | None``
    Caller owns caching. The helper extracts persona-relevant fields
    (theme tags, frameworks, regions, cascade lag, event polarity) from
    the loaded payload. Rows whose payload can't be loaded fall back to
    no-modulation (boost = 1.0, outside_focus = False).

Output:
  Each row gets four new keys added in-place:
    - ``personalised_score``: float, the modulated criticality score
    - ``persona_boost``: float, multiplier applied
    - ``outside_focus``: bool, true iff zero overlap with persona.esg_focus
    - ``base_criticality_score``: float, copied from criticality_score

Re-sort is by ``personalised_score`` DESC, with NULL/missing scored last.
Original ``criticality_score`` is preserved untouched.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from engine.persona.persona_model import Persona
from engine.persona.persona_scorer import (
    HOME_FLOOR,
    MAX_FINAL_SCORE,
    compute_persona_boost,
)

logger = logging.getLogger(__name__)


def _extract_topics(payload: dict) -> list[str]:
    """Pull theme tags from the insight payload. Tries multiple locations
    because the schema has evolved across phases."""
    pipeline = payload.get("pipeline") or {}
    themes = pipeline.get("themes") or {}
    raw = themes.get("theme_tags") or themes.get("themes") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            t = item.get("topic") or item.get("name") or item.get("label")
            if t:
                out.append(str(t))
    primary = themes.get("primary_theme")
    if primary and primary not in out:
        out.insert(0, str(primary))  # primary should be index 0
    return out


def _extract_frameworks(payload: dict) -> list[str]:
    """Pull the framework family codes (BRSR / GRI / TCFD …) from any
    framework hits in the insight."""
    pipeline = payload.get("pipeline") or {}
    fws = pipeline.get("frameworks") or pipeline.get("framework_matches") or []
    if not isinstance(fws, list):
        return []
    out: set[str] = set()
    for fw in fws:
        if not isinstance(fw, dict):
            continue
        code = fw.get("code") or fw.get("framework") or ""
        if not isinstance(code, str) or not code:
            continue
        # BRSR:P6:Q14 → BRSR
        family = code.split(":", 1)[0].strip().upper()
        if family:
            out.add(family)
    return sorted(out)


def _extract_regions(payload: dict) -> list[str]:
    """Geographic regions referenced by the article. Tolerant of multiple
    schema shapes."""
    pipeline = payload.get("pipeline") or {}
    geo = pipeline.get("geographic") or pipeline.get("geography") or {}
    if isinstance(geo, dict):
        raw = geo.get("regions") or geo.get("countries") or []
    else:
        raw = []
    if not isinstance(raw, list):
        return []
    return [str(r).lower() for r in raw if r]


def _extract_polarity(payload: dict) -> str | None:
    insight = payload.get("insight") or {}
    pol = insight.get("event_polarity")
    if isinstance(pol, str) and pol:
        return pol
    return None


def _extract_event_type(payload: dict) -> str | None:
    pipeline = payload.get("pipeline") or {}
    event = pipeline.get("event") or {}
    et = event.get("event_id") or event.get("event_type")
    return str(et) if et else None


def _extract_dominant_lag_months(payload: dict) -> int | None:
    """Pull the dominant cascade hop lag if present (Phase 17c output)."""
    insight = payload.get("insight") or {}
    cascade = insight.get("cascade") or insight.get("cascade_block") or {}
    if not isinstance(cascade, dict):
        return None
    lag = cascade.get("dominant_lag_months") or cascade.get("composed_lag_months")
    try:
        return int(lag) if lag is not None else None
    except (TypeError, ValueError):
        return None


def apply_persona_to_feed(
    rows: list[dict[str, Any]],
    persona: Persona,
    load_payload: Callable[[str], dict | None],
) -> list[dict[str, Any]]:
    """Re-rank `rows` per persona. Returns a NEW list (does not mutate
    the input list, but each row dict gets new keys added).

    Rows without a `criticality_score` are kept (sorted last) so the
    discoverability invariant holds. Failed payload loads fall back to
    no-modulation rather than dropping the row.
    """
    if not rows:
        return []
    enriched: list[dict[str, Any]] = []
    for row in rows:
        out = dict(row)
        base_score = row.get("criticality_score")
        out["base_criticality_score"] = base_score

        try:
            payload = load_payload(row.get("json_path") or "") or {}
        except Exception as exc:  # noqa: BLE001 — never break feed on payload load
            logger.debug("persona_rerank: payload load failed for %s: %s",
                         row.get("id"), exc)
            payload = {}

        boost, outside_focus = compute_persona_boost(
            persona,
            article_topics=_extract_topics(payload),
            article_frameworks=_extract_frameworks(payload),
            article_regions=_extract_regions(payload),
            cascade_dominant_lag_months=_extract_dominant_lag_months(payload),
            event_type=_extract_event_type(payload),
            polarity=_extract_polarity(payload),
        )
        out["persona_boost"] = boost
        out["outside_focus"] = outside_focus

        if base_score is None:
            out["personalised_score"] = None
        else:
            try:
                base_f = float(base_score)
            except (TypeError, ValueError):
                base_f = 0.0
            scored = min(MAX_FINAL_SCORE, base_f * boost)
            band = (row.get("criticality_band") or "").upper()
            if band == "CRITICAL":
                scored = max(scored, HOME_FLOOR)
            out["personalised_score"] = round(scored, 4)
        enriched.append(out)

    # Sort by personalised_score DESC; NULLs sort last
    def _sort_key(r: dict) -> tuple[int, float]:
        ps = r.get("personalised_score")
        if ps is None:
            return (1, 0.0)  # bucket 1 = sorts after bucket 0
        return (0, -float(ps))  # negative for DESC within bucket

    enriched.sort(key=_sort_key)
    return enriched
