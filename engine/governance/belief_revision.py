"""L7 — Belief revision (deterministic skeleton).

When an article ingests, the CompanyAgent decides which beliefs to
update. Today this is a rule-based skeleton; LLM-driven revision can
replace the rule body later without changing the public interface.

Interface contract (locked):
  - Input: article dict (NLP-tagged) + computed cascade + recent
    advisor events (filtered to the tenant)
  - Output: list of `BeliefProposal` objects with a `kind`, a
    `TypedBelief` payload, and a `confidence`

The function returns PROPOSALS — it does NOT apply them. The agent
(or an analyst review UI) decides whether to commit each one. This
matches L4's "audit the audit" pattern at the belief layer.

Today's deterministic rules (R1–R4):
  R1. Negative event + materiality HIGH/CRITICAL → propose RiskBandBelief
  R2. Cascade total_cr ≥ 5% of company revenue → propose
      FinancialExposureBelief
  R3. transition_announcement/capacity_addition events → propose
      TransitionStanceBelief (leader/fast_follower)
  R4. ≥1 high-uncertainty advisor event in the past 7d → confidence
      shifts one band lower (cascading discount on R1–R3)

LLM extension hook: the public function signature accepts an optional
`llm_callback`. When provided, the deterministic proposals are passed
through it for refinement / replacement. The callback's signature is
`Callable[[list[BeliefProposal], dict], list[BeliefProposal]]`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from engine.governance.belief_schema import (
    FinancialExposureBelief,
    RiskBandBelief,
    TransitionStanceBelief,
    TypedBelief,
)


# ---------------------------------------------------------------------------
# Proposal envelope
# ---------------------------------------------------------------------------


@dataclass
class BeliefProposal:
    """A single belief change the revision pass wants to make.

    Includes the typed belief payload + a rationale + the rule that
    triggered it (so an analyst can audit the source of the proposal).
    """
    belief: TypedBelief
    rationale: str
    rule_id: str             # "R1" | "R2" | "R3" | "R4" | "LLM"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CONFIDENCE_LADDER = ["low", "moderate", "high"]


def _downshift_confidence(band: str) -> str:
    """Move one rung down the confidence ladder; clamp at 'low'."""
    if band not in _CONFIDENCE_LADDER:
        return "low"
    idx = _CONFIDENCE_LADDER.index(band)
    return _CONFIDENCE_LADDER[max(0, idx - 1)]


def _recent_high_uncertainty_count(
    advisor_events: list[dict[str, Any]],
    days: int = 7,
) -> int:
    """Count high-uncertainty events in the past `days`."""
    if not advisor_events:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    for ev in advisor_events:
        if ev.get("event_type") != "high_uncertainty_decision":
            continue
        ts_raw = ev.get("ts")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Rule body
# ---------------------------------------------------------------------------


_TRANSITION_LEADER_EVENTS = frozenset({
    "event_transition_announcement",
    "event_capacity_addition",
    "event_green_finance_milestone",
    "event_esg_certification",
})

_MATERIALITY_BANDS_HIGH = frozenset({"HIGH", "CRITICAL"})


def revise_from_article(
    *,
    article: dict[str, Any],
    cascade_result: dict[str, Any] | None = None,
    advisor_events: list[dict[str, Any]] | None = None,
    company_revenue_cr: float = 0.0,
    forecaster_output: dict[str, Any] | None = None,
    autoresearcher_proposal: dict[str, Any] | None = None,
    llm_callback: Callable[[list[BeliefProposal], dict[str, Any]], list[BeliefProposal]] | None = None,
) -> list[BeliefProposal]:
    """Propose belief updates implied by a freshly-ingested article.

    Args:
        article: dict with at minimum `event_id`, `event_polarity`,
            `materiality`, `topic`. Same shape produced by Stage 3
            event classifier + Stage 4 relevance scorer.
        cascade_result: optional dict with `total_cr` (the engine
            cascade output). When present, drives Rule R2.
        advisor_events: optional list of L6 advisor queue entries
            (already filtered to the relevant tenant). When ≥1 entry
            in the past 7d has `event_type='high_uncertainty_decision'`,
            Rule R4 fires.
        company_revenue_cr: tenant revenue in ₹ Cr. Used by R2 to set
            the 5%-of-revenue exposure threshold. 0 disables R2.
        llm_callback: optional refinement hook. When provided, the
            deterministic proposals + context are passed through it
            and the callback's return list is used in place of the
            deterministic output.

    Returns:
        List of `BeliefProposal`. Empty when no rule fired.
    """
    proposals: list[BeliefProposal] = []
    event_id = article.get("event_id") or ""
    polarity = (article.get("event_polarity") or "").lower()
    materiality = (article.get("materiality") or article.get("decision_summary", {}).get("materiality") or "").upper()
    topic = article.get("topic") or article.get("primary_theme") or ""

    # R1 — negative + HIGH materiality → RiskBand upgrade
    if polarity == "negative" and materiality in _MATERIALITY_BANDS_HIGH and topic:
        band = "CRITICAL" if materiality == "CRITICAL" else "HIGH"
        proposals.append(BeliefProposal(
            belief=RiskBandBelief(
                topic=topic,
                band=band,
                confidence_band="moderate",
                last_evidence=str(article.get("id") or article.get("article_id") or ""),
            ),
            rationale=f"R1: {polarity} event ({event_id}) with materiality={materiality}",
            rule_id="R1",
        ))

    # R2 — cascade ≥ 5% of revenue → FinancialExposure
    if cascade_result and company_revenue_cr > 0:
        try:
            total_cr = float(cascade_result.get("total_cr") or 0.0)
        except (TypeError, ValueError):
            total_cr = 0.0
        threshold = company_revenue_cr * 0.05
        if total_cr >= threshold:
            # Treat ±20% as the confidence band around the point estimate
            lo = max(0.0, total_cr * 0.8)
            hi = total_cr * 1.2
            proposals.append(BeliefProposal(
                belief=FinancialExposureBelief(
                    scenario=event_id or "current_event",
                    exposure_cr_lo=lo,
                    exposure_cr_hi=hi,
                    method=str(cascade_result.get("method") or "cascade"),
                    confidence_band="moderate",
                    last_evidence=str(article.get("id") or article.get("article_id") or ""),
                ),
                rationale=(
                    f"R2: cascade ₹{total_cr:.1f} Cr exceeds 5%-of-revenue "
                    f"threshold (₹{threshold:.1f} Cr)"
                ),
                rule_id="R2",
            ))

    # R3 — positive transition event → TransitionStance
    if event_id in _TRANSITION_LEADER_EVENTS and polarity != "negative":
        # Default to fast_follower; LLM extension can promote to leader
        # when supporting evidence is strong.
        proposals.append(BeliefProposal(
            belief=TransitionStanceBelief(
                stance="fast_follower",
                horizon_fy="",  # caller decides the horizon
                confidence_band="moderate",
                last_evidence=str(article.get("id") or article.get("article_id") or ""),
            ),
            rationale=f"R3: positive transition event ({event_id})",
            rule_id="R3",
        ))

    # R4 — recent high-uncertainty → confidence one band lower on all proposals
    high_unc = _recent_high_uncertainty_count(advisor_events or [])
    if high_unc > 0 and proposals:
        for p in proposals:
            current = getattr(p.belief, "confidence_band", "moderate")
            new_band = _downshift_confidence(current)
            # Dataclasses are immutable-ish — use object.__setattr__ since
            # TypedBelief subclasses don't use frozen=True.
            object.__setattr__(p.belief, "confidence_band", new_band)
            p.rationale = (
                f"{p.rationale} (R4: confidence downshifted to {new_band} "
                f"due to {high_unc} recent high-uncertainty events)"
            )


    # R5 — Forecaster-driven risk-band proposal
    #
    # When `forecaster_output` is provided (output of
    # engine.analysis.forecaster.forecast_sentiment_trajectory), inspect
    # the 3m + 6m horizons. If BOTH project 'declining' with confidence
    # ≥ moderate AND no R1 risk_band proposal already exists for the
    # article's topic, propose an additional RiskBandBelief at HIGH.
    #
    # Conservative by design: only fires when the forecast is strong
    # enough to act on (two consecutive horizons declining) AND the
    # event itself didn't already trigger R1 (avoids double-counting).
    if forecaster_output and isinstance(forecaster_output, dict):
        horizons = forecaster_output.get("horizons") or {}
        h3 = horizons.get("3m") or {}
        h6 = horizons.get("6m") or {}
        if (
            h3.get("direction") == "declining"
            and h6.get("direction") == "declining"
            and h3.get("confidence") in {"moderate", "high"}
            and h6.get("confidence") in {"moderate", "high"}
        ):
            topic_for_r5 = article.get("topic") or article.get("primary_theme") or ""
            already_proposed = any(
                p.rule_id == "R1" and isinstance(p.belief, RiskBandBelief)
                and p.belief.topic == topic_for_r5
                for p in proposals
            )
            if topic_for_r5 and not already_proposed:
                proposals.append(BeliefProposal(
                    belief=RiskBandBelief(
                        topic=topic_for_r5,
                        band="HIGH",
                        confidence_band="moderate",
                        last_evidence=f"forecaster:{forecaster_output.get('company_slug', '?')}",
                    ),
                    rationale=(
                        "R5: forecaster projects declining sentiment in 3m + 6m "
                        f"horizons (rationale: {h3.get('rationale', 'n/a')})"
                    ),
                    rule_id="R5",
                ))

    # R6 — Autoresearcher-driven proposal (Tier 1+ wiring; no-op at Tier 0)
    #
    # When a Tier-1 (tenant) autoresearcher run promotes a knob change
    # for this tenant, the runner passes the experiment record through
    # `autoresearcher_proposal`. R6 surfaces a BeliefProposal so the
    # CompanyAgent's belief revision pipeline records the tenant-side
    # impact.
    #
    # The Tier-0 (system-wide) autoresearcher does NOT use R6 — its
    # promotions go through the advisor queue and ontology changes
    # propagate via SPARQL state.
    if autoresearcher_proposal and isinstance(autoresearcher_proposal, dict):
        knob_kind = autoresearcher_proposal.get("knob_kind") or ""
        knob_id = autoresearcher_proposal.get("knob_id") or ""
        delta = autoresearcher_proposal.get("metric_delta") or 0.0
        topic_for_r6 = article.get("topic") or article.get("primary_theme") or ""
        if topic_for_r6 and knob_id:
            # Conservative: only fires when the delta exceeds the Tier-1
            # autoresearcher's own keep threshold (the proposal carries it)
            min_delta = autoresearcher_proposal.get("keep_threshold", 0.02)
            if delta >= min_delta:
                proposals.append(BeliefProposal(
                    belief=RiskBandBelief(
                        topic=topic_for_r6,
                        band="HIGH",
                        confidence_band="moderate",
                        last_evidence=f"autoresearcher:{knob_id}",
                    ),
                    rationale=(
                        f"R6: tenant autoresearcher promoted knob {knob_kind}:{knob_id} "
                        f"with metric Δ={delta:+.4f} ≥ threshold {min_delta:.3f}"
                    ),
                    rule_id="R6",
                ))

    # LLM refinement hook
    if llm_callback is not None:
        context = {
            "article": article,
            "cascade_result": cascade_result,
            "advisor_events": advisor_events,
            "company_revenue_cr": company_revenue_cr,
            "forecaster_output": forecaster_output,
            "autoresearcher_proposal": autoresearcher_proposal,
        }
        try:
            refined = llm_callback(proposals, context)
            if isinstance(refined, list):
                proposals = [p for p in refined if isinstance(p, BeliefProposal)]
        except Exception:
            # LLM failure must not lose the deterministic baseline
            pass

    return proposals
