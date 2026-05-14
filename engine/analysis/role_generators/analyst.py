"""Phase 3 §5.2/§5.3 — ESG Analyst role generator (deterministic baseline).

Consumes a shared EvidencePack and emits an Analyst RoleDistinctPayload.
Locked-contract version: deterministic composition. The LLM-prompt swap
(per §5.3 analyst_generator.txt) replaces the body.

Analyst constraints (§5.3):
  - Every material claim maps to a framework section code (BRSR P6 Q14,
    GRI 207, TCFD-Strategy-c, ESRS E1-9). Drawn from pack.frameworks[].
  - Surface confidence bounds explicitly on every quantitative claim:
    cite β, lag, functional form. Drawn from pack.confidence_bounds + cascade.
  - Flag unverified claims with "[unverified]" — applies when pack lacks
    confidence_bounds.method.
  - Include regulatory checklist of 3-5 entries with framework_section,
    deadline, action_verb, severity.
  - role_paragraph ≤ 100 words; role_takeaways ≤ 5 sentences
"""
from __future__ import annotations

from engine.analysis.evidence_pack import EvidencePack
from engine.analysis.role_generators.types import (
    HeroMetric,
    RecommendationStub,
    RoleDistinctPayload,
)

# Analyst panel order (plan §5.6 / W4d)
ANALYST_VISIBLE_PANELS: tuple[str, ...] = (
    "personal_stakes",
    "crisp_insight",
    "kpi_table",
    "framework_alignment_v2",
    "causal_chain_viz",
    "audit_trail",
    "recommendations_list",
)
ANALYST_HIDDEN_PANELS: tuple[str, ...] = (
    "board_paragraph",
    "three_year_trajectory",
)

ANALYST_ALLOWED_REC_TYPES: frozenset[str] = frozenset({
    "framework", "disclosure", "kpi_tracking", "audit",
})


def _earliest_framework_hit(pack: EvidencePack) -> str:
    """Pick the most-specific framework citation for the hero metric."""
    if not pack.frameworks:
        return ""
    # Mandatory > optional; specific section codes preferred over family-only
    sorted_hits = sorted(
        pack.frameworks,
        key=lambda h: (
            0 if h.is_mandatory else 1,
            0 if ":" in (h.code or "") else 1,
        ),
    )
    return sorted_hits[0].code or ""


def _earliest_deadline(pack: EvidencePack) -> str:
    """Earliest hard deadline from decision_windows (Analyst hero)."""
    if not pack.decision_windows:
        return ""
    hard = [w for w in pack.decision_windows if w.severity == "hard"]
    pool = hard or list(pack.decision_windows)
    return pool[0].deadline if pool else ""


def _confidence_phrase(pack: EvidencePack) -> str:
    """Compose 'β=X · lag Y · method' from cascade hops + bounds.

    Lets every quantitative claim carry its provenance per §5.3.
    """
    cascade = pack.cascade
    bits: list[str] = []
    if cascade.hops:
        h = cascade.hops[0]
        if h.beta is not None:
            bits.append(f"β={h.beta:.2f}")
        if h.lag_months is not None:
            bits.append(f"lag {h.lag_months}mo")
    elif cascade.dominant_lag_months is not None:
        bits.append(f"lag {cascade.dominant_lag_months}mo")
    if pack.confidence_bounds.method:
        bits.append(pack.confidence_bounds.method)
    return " · ".join(bits)


def _is_unverified(pack: EvidencePack) -> bool:
    """An Analyst claim is [unverified] when the pack has no
    confidence_bounds.method AND no cascade hops with β."""
    if pack.confidence_bounds.method:
        return False
    if pack.cascade.hops and any(h.beta is not None for h in pack.cascade.hops):
        return False
    return True


def _regulatory_checklist(pack: EvidencePack) -> list[dict[str, str]]:
    """Compose a 0-5 entry checklist from framework hits + decision windows.

    Each entry: {framework_section, deadline, action_verb, severity}.
    Plan asks for 3-5 entries; we emit as many as the pack supports.
    """
    out: list[dict[str, str]] = []
    deadlines_by_label = {w.label.lower(): w for w in pack.decision_windows}
    for fh in pack.frameworks[:5]:
        # Try to match a deadline by framework code or family
        deadline_str = ""
        severity = "soft"
        family = (fh.code or "").split(":", 1)[0].lower()
        for label_key, w in deadlines_by_label.items():
            if family and family in label_key:
                deadline_str = w.deadline
                severity = w.severity or "soft"
                break
        # Fall back to overall earliest hard deadline so checklist always has a date
        if not deadline_str:
            deadline_str = _earliest_deadline(pack)
        verb = "Disclose" if fh.is_mandatory else "Review"
        out.append({
            "framework_section": fh.code,
            "deadline": deadline_str,
            "action_verb": verb,
            "severity": severity if fh.is_mandatory else severity,
        })
    return out


def generate_analyst_payload(
    pack: EvidencePack,
    recommendations: list[RecommendationStub] | None = None,
) -> RoleDistinctPayload:
    """Deterministic ESG Analyst Stage 11 generator.

    Output is the §5.2 RoleDistinctPayload contract.
    """
    recommendations = recommendations or []
    primary_fw = _earliest_framework_hit(pack)
    deadline = _earliest_deadline(pack)
    confidence = _confidence_phrase(pack)
    unverified = _is_unverified(pack)

    # Headline — framework + deadline (or framework + [unverified] tag)
    if primary_fw and deadline:
        headline = f"{primary_fw} disclosure trigger — due {deadline}"
    elif primary_fw:
        headline = f"{primary_fw} disclosure trigger"
    elif deadline:
        headline = f"Disclosure deadline: {deadline}"
    else:
        headline = "Framework alignment review"
    if unverified:
        headline += " [unverified]"

    # Hero metric — framework deadline as the primary "what to act on"
    hero = HeroMetric(
        value=primary_fw or "Framework gap",
        label="Disclosure trigger",
        deadline=deadline,
    )

    # Role takeaways — every material claim cites a section + confidence
    takeaways: list[str] = []
    if primary_fw:
        first = f"{primary_fw}: action required"
        if deadline:
            first += f" by {deadline}"
        if confidence:
            first += f" ({confidence})"
        if unverified:
            first += " [unverified]"
        takeaways.append(first + ".")
    if pack.cascade.total_cr > 0:
        cascade_bullet = f"Cascade ₹{pack.cascade.total_cr:,.1f} Cr"
        if confidence:
            cascade_bullet += f" ({confidence})"
        if unverified:
            cascade_bullet += " [unverified]"
        takeaways.append(cascade_bullet + ".")
    checklist = _regulatory_checklist(pack)
    if checklist:
        takeaways.append(
            f"Checklist: {len(checklist)} framework section(s) flagged."
        )
    if not takeaways:
        takeaways = ["No framework hits surfaced; review article scope."]

    # Role paragraph — analyst-tone, ≤ 100 words per §5.3
    pieces: list[str] = []
    if primary_fw:
        pieces.append(
            f"{primary_fw} is the dominant disclosure trigger"
            + (f" with deadline {deadline}" if deadline else "")
            + "."
        )
    if confidence:
        pieces.append(f"Confidence: {confidence}.")
    elif unverified:
        pieces.append("Quantitative claims [unverified] — request methodology.")
    if checklist:
        pieces.append(
            f"Regulatory checklist: {len(checklist)} entries; "
            + ", ".join(c["framework_section"] for c in checklist[:3])
            + "."
        )
    if pack.cascade.total_cr > 0:
        pieces.append(
            f"Computed cascade: ₹{pack.cascade.total_cr:,.1f} Cr."
        )
    paragraph = " ".join(pieces) or "Analyst review pending — insufficient evidence."
    words = paragraph.split()
    if len(words) > 100:
        paragraph = " ".join(words[:100]).rstrip(",;:") + "…"

    # Analyst whitelist on rec stubs — framework / disclosure /
    # kpi_tracking / audit (untyped flows through)
    analyst_recs: list[RecommendationStub] = []
    for r in recommendations:
        rt = (r.type or "").lower()
        if rt in ANALYST_ALLOWED_REC_TYPES or not rt:
            analyst_recs.append(r)

    from engine.analysis.role_generators.llm_upgrade import maybe_apply_llm_polish
    polished = maybe_apply_llm_polish(
        pack, "esg-analyst",
        {
            "headline": headline,
            "role_takeaways": takeaways,
            "role_paragraph": paragraph,
        },
    )

    return RoleDistinctPayload(
        role="esg-analyst",
        headline=polished["headline"],
        hero_metric=hero,
        role_takeaways=polished["role_takeaways"],
        role_paragraph=polished["role_paragraph"],
        recommendations=analyst_recs,
        visible_panels=list(ANALYST_VISIBLE_PANELS),
        hidden_panels=list(ANALYST_HIDDEN_PANELS),
    )
