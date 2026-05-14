"""Phase 3 §5.1 — Evidence Pack dataclass + builder.

Foundational scaffold for the deferred Stage 10 → EvidencePack refactor.
Today this module DOES NOT replace the LLM narrative path — it ships
the target shape (dataclass + builder) so the role generators in the
follow-up workstream have something stable to consume.

The plan §5.1:
  * Stage 10 stops producing narrative (no key_takeaways /
    net_impact_summary / executive_insight). Instead it returns a
    structured EvidencePack with the role-distinct material.
  * Stage 11 splits into three independent generators (CFO / CEO /
    Analyst) each consuming the same EvidencePack and emitting prose
    via three different prompts.

What this module ships:
  1. ``EvidencePack`` dataclass with the 9 fields from the plan
  2. Sub-types ``CascadeBlock``, ``FrameworkHit``, ``Stakeholder``,
     ``PainpointMatch``, ``CausalChain``, ``PeerEvent``,
     ``ConfidenceBounds``, ``DecisionWindow`` — narrow, JSON-friendly
  3. ``build_evidence_pack(pipeline_result, insight_dict)`` — pure
     function that ASSEMBLES an EvidencePack from already-computed
     pipeline + insight outputs. Zero LLM calls. Used by the role
     generators once the Stage 10 split lands.
  4. ``EvidencePack.to_dict()`` — JSON-friendly for stamping onto the
     pipeline output for downstream consumers / debugging

This is intentionally non-breaking. No existing call site changes;
the EvidencePack is built and discarded today. When the role
generators land, they'll consume it directly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


Polarity = Literal["positive", "negative", "mixed", "neutral"]


# ---------------------------------------------------------------------------
# Sub-types
# ---------------------------------------------------------------------------


@dataclass
class CascadeHop:
    """One step in a P→P or P→outcome cascade."""
    source: str        # e.g. "EP" (energy price) or event_id
    target: str        # e.g. "OX" (opex) or "GrossMargin"
    beta: float | None = None
    lag_months: int | None = None
    delta_cr: float | None = None
    confidence: str | None = None  # "high" | "medium" | "low"


@dataclass
class CascadeBlock:
    """Computed financial cascade — total exposure + per-hop breakdown.

    Mirrors what `engine.analysis.primitive_engine.compute_cascade`
    already produces, just shaped for the role generators.
    """
    total_cr: float = 0.0
    margin_bps: float | None = None
    dominant_lag_months: int | None = None
    hops: list[CascadeHop] = field(default_factory=list)
    source_flag: str = ""  # "from_article" | "engine_estimate" | ""


@dataclass
class FrameworkHit:
    """A single framework section triggered by the article."""
    code: str          # e.g. "BRSR:P6:Q14", "GRI:303", "TCFD-Strategy-c"
    name: str = ""
    rationale: str = ""
    region: str = ""   # ISO-region or empty
    is_mandatory: bool = False


@dataclass
class Stakeholder:
    """Polarity-aware stakeholder reaction expectation."""
    name: str          # "SEBI", "MSCI ESG", "BlackRock"
    stance: str = ""   # "positive" | "negative" | "neutral" | free-form
    precedent: str = ""  # cited prior case (Tata Power BRSR-leader, etc.)


@dataclass
class PainpointMatch:
    """Tenant painpoint hit with cosine similarity + severity."""
    topic: str
    similarity: float = 0.0   # 0..1 cosine
    severity: float = 0.0     # 0..1 from W3 painpoint discovery
    evidence: str = ""


@dataclass
class CausalChain:
    """Compact ESG causal chain (replaces the 17-relationship-type tree
    for downstream prompts — keep just what the role narrative needs)."""
    hops: int = 0
    relationship_type: str = ""
    explanation: str = ""
    impact_score: float = 0.0


@dataclass
class PeerEvent:
    """Comparable event from a real precedent (Tata Power SECI win,
    Vedanta SCN, etc.). Drawn from the precedents.ttl ontology."""
    company: str
    event_type: str = ""
    year: int | None = None
    polarity: Polarity = "neutral"
    summary: str = ""
    citation: str = ""


@dataclass
class ConfidenceBounds:
    """Per-figure confidence band carried forward to the role generators.

    The Analyst view in particular needs this on every quantitative claim.
    """
    figure_lo_cr: float | None = None
    figure_hi_cr: float | None = None
    method: str = ""       # "cascade", "from_article", "peer_benchmark"
    notes: str = ""


@dataclass
class DecisionWindow:
    """A date-anchored constraint the recipient must act before.

    Examples: "BRSR P6 disclosure due 2026-09-30", "Next earnings call
    2026-07-22", "Board meeting 2026-08-15".
    """
    label: str
    deadline: str         # ISO date or fuzzy ("Q3 FY27")
    severity: str = ""    # "hard" | "soft"


# ---------------------------------------------------------------------------
# EvidencePack
# ---------------------------------------------------------------------------


@dataclass
class EvidencePack:
    """Structured evidence consumed by the role-distinct generators
    (Stage 11 CFO / CEO / Analyst). NO PROSE — every field is either a
    structured value or a short factual string drawn from upstream
    deterministic stages.

    The plan calls this the "shared canonical block" — the role
    generators don't recompute these facts; they only choose which to
    emphasise + how to frame them per role.
    """
    cascade: CascadeBlock = field(default_factory=CascadeBlock)
    frameworks: list[FrameworkHit] = field(default_factory=list)
    stakeholders: list[Stakeholder] = field(default_factory=list)
    painpoint_matches: list[PainpointMatch] = field(default_factory=list)
    causal_chain: CausalChain = field(default_factory=CausalChain)
    comparables: list[PeerEvent] = field(default_factory=list)
    polarity: Polarity = "neutral"
    confidence_bounds: ConfidenceBounds = field(default_factory=ConfidenceBounds)
    decision_windows: list[DecisionWindow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Builder — assembles an EvidencePack from existing pipeline outputs
# ---------------------------------------------------------------------------


def _polarity_from_insight(insight: dict[str, Any]) -> Polarity:
    raw = (insight.get("event_polarity") or "").lower().strip()
    if raw in ("positive", "negative", "mixed", "neutral"):
        return raw  # type: ignore[return-value]
    # Derive from materiality + decision summary signals as a fallback
    materiality = ((insight.get("decision_summary") or {}).get("materiality") or "").upper()
    if materiality in ("CRITICAL", "HIGH"):
        return "negative"
    return "neutral"


def _extract_cascade_total_cr(insight: dict[str, Any]) -> float:
    """Pull the canonical ₹ figure from decision_summary."""
    import re
    decision = insight.get("decision_summary") or {}
    candidates = [
        decision.get("financial_exposure"),
        decision.get("key_risk"),
        decision.get("top_opportunity"),
        insight.get("net_impact_summary"),
    ]
    largest = 0.0
    for v in candidates:
        if not v:
            continue
        for m in re.finditer(r"(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)\s?Cr", str(v)):
            try:
                num = float(m.group(1).replace(",", ""))
                if num > largest:
                    largest = num
            except ValueError:
                continue
    return largest


def _build_frameworks(pipeline_frameworks: list[Any]) -> list[FrameworkHit]:
    out: list[FrameworkHit] = []
    for fm in pipeline_frameworks or []:
        if not fm:
            continue
        # FrameworkMatch dataclass — dot-attr access; or already-dict
        d = fm if isinstance(fm, dict) else getattr(fm, "to_dict", lambda: {})()
        if not d and hasattr(fm, "__dict__"):
            d = vars(fm)
        # Section codes
        sections = d.get("triggered_sections") or d.get("sections") or []
        framework_id = d.get("framework_id") or d.get("framework") or ""
        name = d.get("name") or framework_id
        rationale = d.get("rationale") or d.get("reason") or ""
        region = d.get("region") or ""
        is_mandatory = bool(d.get("is_mandatory") or d.get("mandatory"))
        if sections:
            for sec in sections:
                code = f"{framework_id}:{sec}" if framework_id and sec else (framework_id or str(sec))
                out.append(FrameworkHit(
                    code=code, name=name, rationale=rationale,
                    region=region, is_mandatory=is_mandatory,
                ))
        elif framework_id:
            out.append(FrameworkHit(
                code=framework_id, name=name, rationale=rationale,
                region=region, is_mandatory=is_mandatory,
            ))
    return out


def _build_stakeholders(insight: dict[str, Any]) -> list[Stakeholder]:
    """Parse `insight.perspectives.ceo.stakeholder_map` if present, else
    fall back to bare names from pipeline."""
    out: list[Stakeholder] = []
    perspectives = insight.get("perspectives") or {}
    ceo = perspectives.get("ceo") if isinstance(perspectives, dict) else None
    smap = (ceo or {}).get("stakeholder_map") if isinstance(ceo, dict) else None
    if isinstance(smap, list):
        for entry in smap:
            if not isinstance(entry, dict):
                continue
            out.append(Stakeholder(
                name=str(entry.get("stakeholder") or entry.get("name") or ""),
                stance=str(entry.get("stance") or ""),
                precedent=str(entry.get("precedent") or ""),
            ))
    return out


def _build_causal_chain(pipeline_chains: list[Any]) -> CausalChain:
    """Pick the highest-impact causal chain as the canonical one."""
    if not pipeline_chains:
        return CausalChain()
    best = max(
        pipeline_chains,
        key=lambda c: getattr(c, "impact_score", 0) or 0,
    )
    return CausalChain(
        hops=int(getattr(best, "hops", 0) or 0),
        relationship_type=str(getattr(best, "relationship_type", "") or ""),
        explanation=str(getattr(best, "explanation", "") or ""),
        impact_score=float(getattr(best, "impact_score", 0) or 0),
    )


def _build_comparables(insight: dict[str, Any]) -> list[PeerEvent]:
    """Read analogous_precedent + any explicit comparables list."""
    out: list[PeerEvent] = []
    decision = insight.get("decision_summary") or {}
    precedent_str = (
        decision.get("analogous_precedent")
        or insight.get("analogous_precedent")
        or ""
    )
    if precedent_str and precedent_str.lower() != "null":
        out.append(PeerEvent(
            company=str(precedent_str)[:120],
            event_type="",
            polarity=_polarity_from_insight(insight),
            summary=str(precedent_str),
        ))
    return out


def _build_decision_windows(insight: dict[str, Any]) -> list[DecisionWindow]:
    """Extract any deadline-anchored constraints from financial_timeline +
    decision_summary."""
    out: list[DecisionWindow] = []
    timeline = insight.get("financial_timeline") or {}
    if isinstance(timeline, dict):
        for label, value in timeline.items():
            if not value:
                continue
            out.append(DecisionWindow(
                label=str(label).replace("_", " ").title(),
                deadline=str(value)[:60],
                severity="soft",
            ))
    return out


def _build_painpoint_matches(insight: dict[str, Any]) -> list[PainpointMatch]:
    """Read painpoint matches from the criticality block (Phase 1)."""
    out: list[PainpointMatch] = []
    crit = insight.get("criticality") or {}
    if isinstance(crit, dict):
        components = crit.get("components") or {}
        # The scorer surfaces a single aggregate `painpoint_match` score;
        # without per-painpoint breakdown we can't decompose into named
        # matches. Surface a single placeholder when the score is non-zero.
        agg = components.get("painpoint_match") if isinstance(components, dict) else None
        try:
            agg_f = float(agg) if agg is not None else 0.0
        except (TypeError, ValueError):
            agg_f = 0.0
        if agg_f > 0:
            out.append(PainpointMatch(
                topic="aggregate", similarity=agg_f, severity=agg_f,
                evidence="aggregate similarity score from Stage 9.5 scorer",
            ))
    return out


def _build_confidence_bounds(insight: dict[str, Any]) -> ConfidenceBounds:
    """Pull confidence_bounds from the analyst perspective if present."""
    perspectives = insight.get("perspectives") or {}
    analyst = perspectives.get("esg-analyst") if isinstance(perspectives, dict) else None
    if isinstance(analyst, dict):
        bounds = analyst.get("confidence_bounds") or []
        if isinstance(bounds, list) and bounds:
            first = bounds[0] if isinstance(bounds[0], dict) else {}
            return ConfidenceBounds(
                method=str(first.get("source_type") or first.get("method") or ""),
                notes=str(first.get("rationale") or first.get("notes") or ""),
            )
    return ConfidenceBounds()


def build_evidence_pack(
    pipeline_result: Any,
    insight: dict[str, Any] | None = None,
) -> EvidencePack:
    """Assemble an EvidencePack from a PipelineResult + insight dict.

    All inputs are optional / tolerant — missing fields produce empty
    sub-blocks rather than raising. The function is pure (no side
    effects, no I/O).
    """
    insight = insight or {}

    cascade_total = _extract_cascade_total_cr(insight)
    cascade = CascadeBlock(
        total_cr=cascade_total,
        margin_bps=insight.get("margin_bps")
            if isinstance(insight.get("margin_bps"), (int, float)) else None,
    )

    frameworks = _build_frameworks(
        getattr(pipeline_result, "frameworks", []) or []
    )

    stakeholders = _build_stakeholders(insight)
    painpoint_matches = _build_painpoint_matches(insight)
    causal_chain = _build_causal_chain(
        getattr(pipeline_result, "causal_chains", []) or []
    )
    comparables = _build_comparables(insight)
    polarity = _polarity_from_insight(insight)
    confidence_bounds = _build_confidence_bounds(insight)
    decision_windows = _build_decision_windows(insight)

    return EvidencePack(
        cascade=cascade,
        frameworks=frameworks,
        stakeholders=stakeholders,
        painpoint_matches=painpoint_matches,
        causal_chain=causal_chain,
        comparables=comparables,
        polarity=polarity,
        confidence_bounds=confidence_bounds,
        decision_windows=decision_windows,
    )
