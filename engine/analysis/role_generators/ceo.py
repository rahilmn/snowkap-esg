"""Phase 3 §5.2/§5.3 — CEO role generator (deterministic baseline).

Consumes a shared EvidencePack and emits a CEO RoleDistinctPayload.
Locked-contract version: deterministic composition from EvidencePack
fields. The LLM-prompt swap (per §5.3 ceo_generator.txt) replaces the
body without changing the signature or downstream consumers.

CEO constraints (§5.3):
  - NEVER lead with a ₹ figure. Lead with competitive positioning,
    stakeholder signal, or strategic optionality.
  - 3-year horizon, not the next quarter
  - Reference at least one peer event (positive precedent for positive
    events, defensive precedent for negative events) — drawn from the
    pack.comparables[]
  - Stakeholder framing must be polarity-coherent
  - role_paragraph ≤ 80 words; role_takeaways ≤ 5 sentences
"""
from __future__ import annotations

from datetime import datetime

from engine.analysis.evidence_pack import EvidencePack
from engine.analysis.role_generators.types import (
    HeroMetric,
    RecommendationStub,
    RoleDistinctPayload,
)

# CEO panel order (plan §5.6 / W4d)
CEO_VISIBLE_PANELS: tuple[str, ...] = (
    "personal_stakes",
    "crisp_insight",
    "three_year_trajectory",
    "stakeholder_map",
    "board_paragraph",
    "recommendations_list",
)
CEO_HIDDEN_PANELS: tuple[str, ...] = (
    "kpi_table",
    "framework_alignment_v2",
    "audit_trail",
)

CEO_ALLOWED_REC_TYPES: frozenset[str] = frozenset({
    "strategic", "esg_positioning", "brand", "capital_allocation",
})


def _three_year_horizon() -> str:
    """Compose 'FY{n+1}-{n+3}' from the current year, matching the
    Phase 13 S2 dynamic-fiscal-year helper."""
    n = datetime.now().year - 2000  # FY27 etc.
    return f"FY{n + 1:02d}-{n + 3:02d}"


def _strategic_anchor(pack: EvidencePack) -> str:
    """Pick the strongest strategic-positioning phrase from the pack.

    Priority order:
      1. Top stakeholder name + stance ("MSCI ESG positive")
      2. Causal-chain relationship type ("supplyChainUpstream exposure")
      3. Polarity-aware fallback ("Defensive positioning needed")
    """
    if pack.stakeholders:
        s = pack.stakeholders[0]
        if s.name and s.stance:
            return f"{s.name} {s.stance.lower()}"
        if s.name:
            return s.name
    if pack.causal_chain.relationship_type:
        rel = pack.causal_chain.relationship_type
        # Camel → spaced
        spaced = "".join(
            (" " + c.lower()) if c.isupper() else c for c in rel
        ).strip().title()
        return f"{spaced} exposure"
    if pack.polarity == "positive":
        return "Strategic upside surfacing"
    if pack.polarity == "negative":
        return "Defensive positioning needed"
    return "Strategic positioning under review"


def _peer_with_polarity_match(pack: EvidencePack) -> str:
    """Pick the comparable whose polarity matches the article's. Plan
    §5.3: 'positive precedent for positive events, defensive precedent
    for negative events. Never mix.'"""
    if not pack.comparables:
        return ""
    for p in pack.comparables:
        if p.polarity == pack.polarity and p.company:
            return p.company
    # No polarity-match → fall back to the first one (better than nothing)
    return pack.comparables[0].company if pack.comparables[0].company else ""


def _trajectory_lines(pack: EvidencePack) -> dict[str, str]:
    """Build the {do_nothing, act_now} pair plan §5.3 wants."""
    horizon = _three_year_horizon()
    if pack.polarity == "positive":
        return {
            "do_nothing": (
                f"Risk losing first-mover advantage by {horizon}; peers "
                "compound the gap."
            ),
            "act_now": (
                f"Capture upside through {horizon} while window stays open; "
                "lock in narrative."
            ),
        }
    if pack.polarity == "negative":
        return {
            "do_nothing": (
                f"Compounding reputational + capital-cost drag through "
                f"{horizon} as peers reposition."
            ),
            "act_now": (
                f"Reframe board narrative + redirect capex by {horizon} "
                "to outflank peers."
            ),
        }
    return {
        "do_nothing": f"Status quo through {horizon}; competitive position drifts.",
        "act_now": f"Set explicit {horizon} stance to anchor stakeholder communication.",
    }


def generate_ceo_payload(
    pack: EvidencePack,
    recommendations: list[RecommendationStub] | None = None,
) -> RoleDistinctPayload:
    """Deterministic CEO Stage 11 generator.

    Output is the §5.2 RoleDistinctPayload contract. Future LLM version
    swaps the body — same signature, same shape.
    """
    recommendations = recommendations or []
    horizon = _three_year_horizon()
    anchor = _strategic_anchor(pack)
    peer = _peer_with_polarity_match(pack)
    traj = _trajectory_lines(pack)

    # Headline — strategic-led, NEVER ₹-led per §5.3
    if pack.polarity == "positive":
        headline = f"{anchor} — {horizon} window to compound the lead"
    elif pack.polarity == "negative":
        headline = f"{anchor} — {horizon} board narrative needs reframe"
    else:
        headline = f"{anchor} — {horizon} positioning under review"

    # Hero metric — strategic value + horizon, NEVER ₹
    hero = HeroMetric(
        value=anchor,
        label="Strategic position",
        horizon=horizon,
    )

    # Role takeaways — 3 strategic bullets; never ₹-led
    takeaways: list[str] = []
    takeaways.append(f"{anchor} reshapes the {horizon} competitive frame.")
    if peer:
        kind = "Positive precedent" if pack.polarity == "positive" else "Defensive precedent"
        takeaways.append(f"{kind}: {peer}.")
    takeaways.append(f"Trajectory if act now: {traj['act_now']}")

    # Role paragraph — board-tone, ≤ 80 words per §5.3
    pieces: list[str] = [
        f"{anchor} is the {horizon} story to tell the board.",
    ]
    if peer:
        pieces.append(f"Peer signal: {peer}.")
    pieces.append(f"Do nothing → {traj['do_nothing']}")
    pieces.append(f"Act now → {traj['act_now']}")
    paragraph = " ".join(pieces)
    words = paragraph.split()
    if len(words) > 80:
        paragraph = " ".join(words[:80]).rstrip(",;:") + "…"

    # CEO whitelist on rec stubs — strategic / esg_positioning / brand /
    # capital_allocation (untyped flows through)
    ceo_recs: list[RecommendationStub] = []
    for r in recommendations:
        rt = (r.type or "").lower()
        if rt in CEO_ALLOWED_REC_TYPES or not rt:
            ceo_recs.append(r)

    from engine.analysis.role_generators.llm_upgrade import maybe_apply_llm_polish
    polished = maybe_apply_llm_polish(
        pack, "ceo",
        {
            "headline": headline,
            "role_takeaways": takeaways,
            "role_paragraph": paragraph,
        },
    )

    return RoleDistinctPayload(
        role="ceo",
        headline=polished["headline"],
        hero_metric=hero,
        role_takeaways=polished["role_takeaways"],
        role_paragraph=polished["role_paragraph"],
        recommendations=ceo_recs,
        visible_panels=list(CEO_VISIBLE_PANELS),
        hidden_panels=list(CEO_HIDDEN_PANELS),
    )
