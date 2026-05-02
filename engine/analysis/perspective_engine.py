"""Perspective transformation (ontology-driven, deterministic).

Takes a :class:`DeepInsight` and reshapes it for a specific executive lens
(ESG Analyst / CFO / CEO). Uses SPARQL to discover which impact dimensions
matter for a given (topic × perspective) pair — NO hardcoded "CFO sees X"
logic in Python.

This is pure post-processing — no LLM calls. The result is a
:class:`CrispOutput` suitable for the Bloomberg-meets-McKinsey UI.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.pipeline import PipelineResult
from engine.config import load_perspectives
from engine.ontology.intelligence import (
    get_perspective_config,
    query_dim_to_insight_keys,
    query_grid_column_map,
    query_headline_rules,
    query_perspective_impacts,
)

logger = logging.getLogger(__name__)

# Grid column mapping and dim-to-key mapping are now ontology-driven.
# See knowledge_expansion.ttl: gridColumn / insightKey triples.


@dataclass
class CrispOutput:
    perspective: str  # esg-analyst | cfo | ceo
    headline: str
    impact_grid: dict[str, str]  # {financial/regulatory/strategic: HIGH/MEDIUM/LOW}
    what_matters: list[str]  # 2-3 bullets
    action: list[str]  # 1-2 bullets OR ["No action required"]
    materiality: str  # CRITICAL | HIGH | MODERATE | LOW | NON-MATERIAL
    do_nothing: bool
    active_impact_dimensions: list[str]  # ontology-derived
    full_insight: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _classify_grid_level(score: float) -> str:
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


def _build_impact_grid(
    insight: DeepInsight, active_dims: set[str]
) -> dict[str, str]:
    """Aggregate relevance sub-scores into the 3-column grid (ontology-driven)."""
    _grid_map = query_grid_column_map()  # noqa: F841 — used for future dim-aware grids
    sub = insight.esg_relevance_score or {}

    def _score(key: str) -> float:
        entry = sub.get(key) or {}
        try:
            return float(entry.get("score", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    columns = {
        "financial": _score("financial_materiality"),
        "regulatory": _score("regulatory_exposure"),
        "strategic": max(
            _score("environment"), _score("social"), _score("governance"),
            _score("stakeholder_impact"),
        ),
    }
    return {col: _classify_grid_level(score) for col, score in columns.items()}


_NEGATIVE_LEAD_PATTERNS = (
    "no supply chain",
    "no direct",
    "no immediate",
    "no material",
    "not material",
    "no impact",
    "no exposure",
    "no regulatory",
    "no governance",
    "no people",
    "n/a",
)


def _is_negative_lead(value: str) -> bool:
    """A bullet 'leads negative' if its first ~80 chars say the dimension does
    not apply (e.g., 'No supply chain transmission; ...'). These are factual
    but make weak top-of-list bullets for CFO/CEO summaries — they should be
    pushed to the bottom rather than dropped, so we still surface them as the
    last bullet when there's room."""
    head = value.strip().lower()[:80]
    return any(head.startswith(p) for p in _NEGATIVE_LEAD_PATTERNS)


def _extract_what_matters(
    insight: DeepInsight, active_dims: set[str], max_items: int = 3
) -> list[str]:
    """Pick 2-3 impact_analysis entries aligned with the active perspective.

    Phase 22.5: bullets that lead with a negative finding ('No supply chain
    transmission', 'No direct revenue at risk') are stable-sorted to the
    bottom so the top of the list is always positive/material content.
    """
    impact_analysis = insight.impact_analysis or {}
    dim_to_key = query_dim_to_insight_keys()
    priority_keys: list[str] = []
    for dim in active_dims:
        for key in dim_to_key.get(dim, []):
            if key not in priority_keys:
                priority_keys.append(key)
    # If perspective has no ontology dims, fall back to all
    if not priority_keys:
        priority_keys = list(impact_analysis.keys())

    positive: list[str] = []
    negative: list[str] = []
    for key in priority_keys:
        value = impact_analysis.get(key)
        if not value or value == "N/A":
            continue
        s = str(value)
        (negative if _is_negative_lead(s) else positive).append(s)
        if len(positive) + len(negative) >= max_items * 2:
            break

    ordered = (positive + negative)[:max_items]
    return ordered


def _extract_action(
    insight: DeepInsight, do_nothing: bool, perspective: str = "esg-analyst"
) -> list[str]:
    if do_nothing:
        # Even do-nothing gets perspective-specific monitoring guidance
        decision = insight.decision_summary or {}
        timeline = decision.get("timeline", "next quarterly review")
        if perspective == "cfo":
            return [f"No P&L action — flag for {timeline} if exposure changes"]
        if perspective == "ceo":
            return [f"No board action — monitor competitive landscape through {timeline}"]
        return [f"No compliance action — review at {timeline}"]

    decision = insight.decision_summary or {}
    verdict = decision.get("verdict", "")
    top = decision.get("top_opportunity", "")
    key_risk = decision.get("key_risk", "")
    fin_exposure = decision.get("financial_exposure", "")
    timeline_obj = insight.financial_timeline or {}

    if perspective == "cfo":
        actions = []
        if fin_exposure and fin_exposure not in ("N/A", "None", ""):
            actions.append(f"Quantify exposure: {fin_exposure}")
        immediate = timeline_obj.get("immediate", {}) if isinstance(timeline_obj, dict) else {}
        rev_risk = immediate.get("revenue_at_risk", "") if isinstance(immediate, dict) else ""
        if rev_risk and rev_risk not in ("N/A", "None", ""):
            actions.append(f"Revenue at risk: {rev_risk}")
        if not actions and verdict:
            actions.append(str(verdict))
        return actions[:2] or ["Review financial exposure at next earnings prep"]

    if perspective == "ceo":
        actions = []
        if top and top not in ("None", "N/A", ""):
            actions.append(f"Strategic opportunity: {top}")
        structural = timeline_obj.get("structural", {}) if isinstance(timeline_obj, dict) else {}
        comp_pos = structural.get("competitive_position", "") if isinstance(structural, dict) else ""
        if comp_pos and comp_pos not in ("N/A", "None", ""):
            actions.append(f"Competitive position: {comp_pos}")
        if not actions and verdict:
            actions.append(str(verdict))
        return actions[:2] or ["Assess strategic implications at next board sync"]

    # ESG Analyst — full detail
    actions = []
    if verdict:
        actions.append(str(verdict))
    if key_risk and key_risk not in ("N/A", "None", ""):
        actions.append(f"Key risk: {key_risk}")
    if top and top not in ("None", "N/A", ""):
        actions.append(f"Opportunity: {top}")
    return actions[:3] or ["Review at next leadership sync"]


def _resolve_field(insight: DeepInsight, dot_path: str) -> str | None:
    """Resolve a dot-path field from the insight (e.g. 'decision_summary.financial_exposure')."""
    _NA = {"N/A", "None", "none", "null", ""}
    parts = dot_path.split(".")
    obj: Any = insight
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif hasattr(obj, part):
            obj = getattr(obj, part, None)
        else:
            return None
        if obj is None:
            return None
    s = str(obj).strip()
    return s if s and s not in _NA else None


def _perspective_headline(
    insight: DeepInsight, perspective: str
) -> str:
    """Reframe headline using ontology-sourced HeadlineRules.

    Rules are sorted by priority (1=highest). Each rule specifies a source
    field to check and a template with {value}/{base} placeholders.
    Falls through to next rule if source field is empty/N/A.
    """
    base = insight.headline

    if perspective == "esg-analyst":
        return base

    rules = query_headline_rules(perspective)
    if not rules:
        # Ontology didn't return rules — return base with perspective prefix
        prefix = {"cfo": "P&L signal", "ceo": "Board-level signal"}.get(perspective, "")
        return f"{prefix} — {base}"[:280] if prefix else base

    for rule in rules:
        if rule.is_fallback:
            return rule.template.replace("{value}", "").replace("{base}", base).strip()[:280]
        value = _resolve_field(insight, rule.source_field)
        if value:
            return rule.template.replace("{value}", value).replace("{base}", base)[:280]

    return base


def transform_for_perspective(
    insight: DeepInsight,
    result: PipelineResult,
    perspective: str,
) -> CrispOutput:
    """Produce the crisp output for a given perspective lens.

    Queries the ontology for the impact dimensions that matter for
    (primary_theme × perspective). Uses those to select the what_matters
    bullets and the grid emphasis.
    """
    perspective = perspective.lower().strip()
    topic = result.themes.primary_theme

    # ONTOLOGY QUERY: what impact dimensions does this perspective care about?
    active_dims_list = query_perspective_impacts(topic, perspective)
    active_dims = set(active_dims_list)

    config = get_perspective_config(perspective)
    if not config:
        # Ontology didn't return config — fall back to JSON config
        perspectives = load_perspectives()
        json_config = perspectives.get(perspective) or {}
        max_words = json_config.get("max_words")
    else:
        max_words = config.max_words

    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "MODERATE")).upper()
    action_kind = str(decision.get("action", "MONITOR")).upper()

    # "Do nothing" rule — only for truly non-material events
    do_nothing = (
        materiality in ("NON-MATERIAL", "NONMATERIAL")
        and action_kind == "IGNORE"
    )

    impact_grid = _build_impact_grid(insight, active_dims)
    what_matters = _extract_what_matters(insight, active_dims)
    action = _extract_action(insight, do_nothing, perspective)
    headline = _perspective_headline(insight, perspective)

    # Enforce perspective word cap for CFO/CEO by truncating bullets
    if max_words and perspective in ("cfo", "ceo"):
        total_words = sum(len(b.split()) for b in what_matters)
        while what_matters and total_words > max_words:
            what_matters.pop()
            total_words = sum(len(b.split()) for b in what_matters)

    # Phase 22.5: populate full_insight for ALL perspectives (was esg-analyst
    # only). The CFO + CEO lenses need it for the in-app drill-down panel
    # and for downstream renderers that want the canonical impact_analysis,
    # decision_summary, financial_timeline, etc. without an extra round trip.
    full = insight.to_dict()

    return CrispOutput(
        perspective=perspective,
        headline=headline,
        impact_grid=impact_grid,
        what_matters=what_matters,
        action=action,
        materiality=materiality,
        do_nothing=do_nothing,
        active_impact_dimensions=active_dims_list,
        full_insight=full,
    )
