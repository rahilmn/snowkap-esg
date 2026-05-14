"""Phase 3 §5.2 — CFO role generator (deterministic baseline).

Consumes a shared EvidencePack and emits a CFO RoleDistinctPayload.
This is the LOCKED CONTRACT version: every output field is composed
from already-structured EvidencePack data using simple deterministic
rules. No LLM calls.

When the LLM-prompt version (per plan §5.3) lands, it will replace
the body of `generate_cfo_payload()` while keeping the same input/
output shape — every downstream consumer + every test in
test_phase26_cfo_role_generator.py keeps working.

The plan's CFO constraints (§5.3):
  - Lead with the ₹ figure rounded to 2 significant figures
  - Every sentence: quantify, compare to peer, OR recommend an action with payback
  - No strategic framing, no 3-year horizons, no "positioning"
  - No comms recommendations
  - Maximum 5 sentences for role_takeaways[0], 90 words for role_paragraph
"""
from __future__ import annotations

from engine.analysis.evidence_pack import EvidencePack
from engine.analysis.role_generators.types import (
    HeroMetric,
    RecommendationStub,
    RoleDistinctPayload,
)

# CFO panel order (plan §5.6 / W4d)
CFO_VISIBLE_PANELS: tuple[str, ...] = (
    "personal_stakes",
    "crisp_insight",
    "impact_metrics",
    "recommendations_list",
    "audit_trail",
)
CFO_HIDDEN_PANELS: tuple[str, ...] = (
    "narrative_intelligence",
    "sdg_map",
    "causal_chain_viz",
)


def _round_sig2_indian(value: float) -> str:
    """Round to 2 significant figures + format with Indian thousands.
    Mirrors the frontend renderer in client/src/lib/number_format.ts."""
    if value == 0 or value != value:  # NaN guard
        return "0"
    import math
    sign = -1 if value < 0 else 1
    abs_v = abs(value)
    magnitude = math.floor(math.log10(abs_v))
    factor = 10 ** (1 - magnitude)
    rounded = sign * round(abs_v * factor) / factor
    if rounded == int(rounded):
        return f"{int(rounded):,}"
    return f"{rounded:,.1f}"


def _format_rupee_headline(value_cr: float) -> str:
    """`~₹1,900 Cr` per Phase 2 protocol (sig2 + en-IN grouping + ₹ + Cr)."""
    if value_cr <= 0:
        return ""
    return f"~₹{_round_sig2_indian(value_cr)} Cr"


def _earliest_decision_window(pack: EvidencePack) -> str:
    """Return the soonest deadline string from the pack's decision_windows.

    The CFO hero metric shows "decide by [date]" so the recipient knows
    the action window. Prefers hard severity > soft. Falls back to the
    first window when none is hard."""
    if not pack.decision_windows:
        return ""
    hard = [w for w in pack.decision_windows if w.severity == "hard"]
    pool = hard or list(pack.decision_windows)
    return pool[0].deadline if pool else ""


def _peer_phrase(pack: EvidencePack) -> str:
    """One-line peer comparable line if present. CFO §5.3 allows
    'compare to peer' as a valid sentence type."""
    if not pack.comparables:
        return ""
    p = pack.comparables[0]
    if not p.company:
        return ""
    return f"Peer precedent: {p.company}"


def _action_phrase_from_recommendations(
    recommendations: list[RecommendationStub],
) -> str:
    """Pick the top CFO-allowed recommendation and frame it with payback."""
    for rec in recommendations:
        # CFO whitelist: financial / operational / compliance
        if rec.type and rec.type.lower() not in (
            "financial", "operational", "compliance",
        ):
            continue
        bits = [rec.title.rstrip(".")]
        if rec.budget_cr is not None and rec.budget_cr > 0:
            bits.append(f"budget ~₹{_round_sig2_indian(rec.budget_cr)} Cr")
        if rec.payback_months is not None and rec.payback_months > 0:
            bits.append(f"payback {int(rec.payback_months)}mo")
        return "; ".join(bits) + "."
    return ""


def generate_cfo_payload(
    pack: EvidencePack,
    recommendations: list[RecommendationStub] | None = None,
    company_revenue_cr: float | None = None,
) -> RoleDistinctPayload:
    """Deterministic CFO Stage 11 generator.

    Args:
        pack: shared evidence (built once per article via build_evidence_pack)
        recommendations: optional pre-generated rec stubs. CFO whitelist
                         (financial / operational / compliance) is applied
                         here so wrong-type recs never leak into this view.
        company_revenue_cr: optional, lets the generator compute "% of
                            revenue at stake" when present.

    Output is the RoleDistinctPayload contract from §5.2. Future LLM
    version replaces the body without changing the signature.
    """
    recommendations = recommendations or []
    cascade_total = pack.cascade.total_cr or 0.0
    deadline = _earliest_decision_window(pack)
    peer = _peer_phrase(pack)
    action_line = _action_phrase_from_recommendations(recommendations)

    rupee_str = _format_rupee_headline(cascade_total)

    # Headline: ₹-led, action verb derived from polarity
    if cascade_total > 0:
        verb = "compresses" if pack.polarity == "negative" else "lifts"
        headline = f"P&L {verb} {rupee_str}"
        if pack.cascade.margin_bps:
            headline += f" · {abs(pack.cascade.margin_bps):.0f} bps margin"
    else:
        headline = "P&L exposure: pending cascade"

    # Hero metric: ₹ value + decision window
    hero = HeroMetric(
        value=rupee_str or "TBD",
        label="P&L exposure",
        decision_window=deadline,
    )

    # Role takeaways — at most 3 bullets per §5.3 spirit (≤5 sentences total
    # in role_takeaways[0], but we ship 3 separate bullets for the UI to
    # render as a list; consumers can join when needed).
    takeaways: list[str] = []
    if cascade_total > 0:
        first = f"P&L exposure: {rupee_str}."
        if (
            company_revenue_cr is not None
            and company_revenue_cr > 0
        ):
            pct = (cascade_total / company_revenue_cr) * 100
            first = first.rstrip(".") + (
                f" — {pct:.1f}% of revenue at stake."
            )
        takeaways.append(first)
    if peer:
        takeaways.append(peer + ".")
    if action_line:
        takeaways.append(action_line)
    if not takeaways:
        # Defensive — never ship an empty list (UI renders a sad-empty card)
        takeaways = ["No quantified P&L exposure surfaced; monitor only."]

    # Role paragraph — composed from the takeaways + deadline. Capped at
    # 90 words per §5.3 (we count generously and trim if needed).
    paragraph_bits = [t.rstrip(".") for t in takeaways[:3]]
    paragraph = ". ".join(paragraph_bits) + "."
    if deadline:
        paragraph += f" Decide by {deadline}."
    # Word cap (90 words / §5.3)
    words = paragraph.split()
    if len(words) > 90:
        paragraph = " ".join(words[:90]).rstrip(",;:") + "…"

    # CFO whitelist on the rec stubs — drop forbidden types
    cfo_recs: list[RecommendationStub] = []
    for r in recommendations:
        rt = (r.type or "").lower()
        if rt in ("financial", "operational", "compliance") or not rt:
            cfo_recs.append(r)

    # Phase 3 §5.3 — optional LLM polish for headline + takeaways + paragraph
    # only. Hero metric, recs, panels stay deterministic. Default-off via
    # SNOWKAP_LLM_ROLE_GENERATORS env flag — existing tests stay green.
    from engine.analysis.role_generators.llm_upgrade import maybe_apply_llm_polish
    polished = maybe_apply_llm_polish(
        pack, "cfo",
        {
            "headline": headline,
            "role_takeaways": takeaways,
            "role_paragraph": paragraph,
        },
    )

    return RoleDistinctPayload(
        role="cfo",
        headline=polished["headline"],
        hero_metric=hero,
        role_takeaways=polished["role_takeaways"],
        role_paragraph=polished["role_paragraph"],
        recommendations=cfo_recs,
        visible_panels=list(CFO_VISIBLE_PANELS),
        hidden_panels=list(CFO_HIDDEN_PANELS),
    )
