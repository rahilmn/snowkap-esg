"""Phase 13 B1 — Event-specific recommendation archetypes.

Before this module, every HOME-tier article — regardless of event type —
got the same 5-rec template (compliance disclosure + monitoring + strategic
capex + assurance + operational hedging). An editorial CFO scrolling
through a contract win, a SEBI penalty, and a capacity addition would see
near-identical recommendations and (correctly) conclude the system is
boilerplate.

This module maps each of the 22 ontology event types to a list of
*recommendation archetypes* — categories of action that genuinely fit the
event. The archetype list is injected into the LLM generator prompt so
the model picks event-appropriate levers instead of defaulting to "file
BRSR + monitor compliance" for everything.

V1 keeps the map in Python for visibility + editability. V2 will move it
into the ontology (knowledge_expansion.ttl) so an analyst can edit the
archetype taxonomy without touching code.
"""

from __future__ import annotations

# Each entry: (label, one-line description). Labels are short — they show
# up directly in the LLM prompt so brevity matters.
Archetype = tuple[str, str]

# Positive / growth events — recommendations should leverage the upside,
# NOT default to defensive disclosure-remediation.
_POSITIVE_GROWTH = [
    ("Operational readiness", "scale capacity / supply chain to meet new commitments"),
    ("Investor communication", "leverage event as a market signal in IR / earnings call"),
    ("Pipeline momentum", "convert this win into next-tender / next-bid pipeline"),
    ("KPI monitoring", "track delivery against committed milestones + thresholds"),
    ("Working-capital optimization", "fund the ramp without stressing balance sheet"),
    ("Premium-pricing capture", "use ESG/quality differentiation in pricing"),
]

_CAPACITY_ADD = [
    ("Utilization ramp plan", "load curve + commissioning checkpoints"),
    ("Investor day disclosure", "EBITDA bridge from new asset"),
    ("Supply-chain readiness", "input commodity / fuel hedging"),
    ("Service-level monitoring", "uptime, dispatch, customer SLA"),
    ("Workforce mobilisation", "skilled-staff hire + safety training"),
]

_ESG_CERT = [
    ("Investor-comms leverage", "press release + ESG-fund pitch update"),
    ("Premium pricing on certified output", "B2B procurement positioning"),
    ("Roadmap to next tier", "Platinum / AA / DJSI Top-10 ladder"),
    ("Case-study publication", "credible third-party endorsement"),
    ("Audit-cycle institutionalisation", "annual recurrence vs one-off"),
]

_GREEN_FINANCE = [
    ("Use-of-proceeds disclosure", "GBP/CBI-aligned ringfencing"),
    ("Capex-deployment cadence", "tranche-wise drawdown vs project milestones"),
    ("ESG fund-flow campaign", "investor outreach to climate-aligned funds"),
    ("Refinancing optionality", "lower coupon vs vanilla bond"),
]

# Negative / disruption events — recommendations should remediate AND
# get ahead of regulator / investor / NGO follow-on.
_SUPPLY_CHAIN_NEG = [
    ("Alternative sourcing", "qualify second supplier / region"),
    ("Inventory hedging", "safety-stock raise + commodity hedge"),
    ("Operational continuity audit", "single-points-of-failure assessment"),
    ("Physical-risk disclosure", "TCFD scenarios + climate-cascade map"),
    ("Customer / SLA communication", "transparency on delivery impact"),
]

_SOCIAL_VIOLATION = [
    ("Independent third-party audit", "ETI Base Code / Sedex SMETA scope"),
    ("Supplier remediation programme", "corrective action plans + timelines"),
    ("Stakeholder engagement", "civil-society dialogue + community redress"),
    ("GRI:408 / 409 disclosure", "child / forced labour transparency"),
    ("Worker-voice mechanism", "anonymous reporting + grievance channel"),
]

_LABOUR_STRIKE = [
    ("Union / collective dialogue", "structured negotiation cadence"),
    ("Production continuity plan", "alternate site / shift coverage"),
    ("Workforce engagement disclosure", "BRSR P3 / GRI 401 transparency"),
    ("Wage-equity analysis", "leading-indicator review"),
]

_CYBER = [
    ("Incident response activation", "DFIR engagement + scope containment"),
    ("Customer notification", "GDPR / DPDP-aligned breach communication"),
    ("Regulator notification", "SEBI / RBI / CERT-In within mandate window"),
    ("Third-party assurance", "ISO 27001 / SOC 2 audit refresh"),
    ("Cyber-insurance review", "cover sufficiency + premium update"),
]

_COMMUNITY_PROTEST = [
    ("Structured stakeholder dialogue", "community council + facilitator"),
    ("Social-impact assessment", "ESIA refresh with Q&A loop"),
    ("Land / permits review", "title clarity + consent re-confirmation"),
    ("Grievance redressal mechanism", "third-party-administered channel"),
]

_NGO_REPORT = [
    ("Formal response within 30 days", "fact-based rebuttal or acknowledgement"),
    ("Independent third-party validation", "supplier / facility re-audit"),
    ("Public transparency disclosure", "facts, gaps, remediation timeline"),
    ("Investor + regulator briefing", "pre-empt misreading of NGO claim"),
]

_LICENSE_REVOCATION = [
    ("Legal challenge / appeal", "stay-of-execution + evidence pack"),
    ("Alternate-permit pathway", "parallel-track regulatory engagement"),
    ("Operations contingency plan", "other-asset utilisation, customer notification"),
    ("Stakeholder + investor disclosure", "stranded-asset risk transparency"),
]

# Regulatory / governance events
_REGULATORY_POLICY = [
    ("Compliance-gap analysis", "rule-by-rule mapping + impact estimate"),
    ("Framework-alignment refresh", "BRSR / GRI / TCFD section update"),
    ("Regulator engagement", "consultation response + bilateral meeting"),
    ("Industry coalition advocacy", "association / FICCI joint position"),
]

_BOARD_CHANGE = [
    ("Governance review", "committee composition + independence test"),
    ("ISS / Glass Lewis disclosure", "succession + skills-matrix update"),
    ("Investor briefing", "rationale + stewardship narrative"),
    ("Stakeholder communication", "media / employee comms plan"),
]

_CREDIT_RATING = [
    ("Bondholder engagement", "rationale + remediation roadmap"),
    ("Balance-sheet optimization", "leverage / coverage improvement plan"),
    ("Rating-action precedent disclosure", "comparable-issuer benchmark"),
    ("Investor day update", "guidance + rating-recovery KPIs"),
]

# Financial / routine events — keep recommendations focused on
# narrative + disclosure, not invented crisis remediation.
_QUARTERLY = [
    ("Earnings-call narrative refresh", "ESG-tied profitability framing"),
    ("Investor Q&A prep", "anticipated questions on margin / outlook"),
    ("Framework-aligned disclosure", "BRSR / GRI sections matching results"),
    ("Guidance update", "FY trajectory + assumptions"),
]

_ANALYST_OUTLOOK = [
    ("IR outreach", "model-deck refresh + analyst meetings"),
    ("Sell-side narrative refresh", "FAQ on the analyst note's themes"),
    ("Buyback / dividend signalling", "capital-return policy reaffirmation"),
]

_DIVIDEND = [
    ("Board communication", "rationale + payout-ratio policy"),
    ("Investor announcement", "ex-date + record-date schedule"),
    ("Tax-efficiency disclosure", "DDT / Section 80 implications"),
]

_MA_DEAL = [
    ("Integration plan", "100-day plan + synergy tracker"),
    ("Synergy disclosure", "cost / revenue split with timing"),
    ("Regulatory filing", "CCI / SEBI / NCLT timeline"),
    ("Stakeholder communication", "employee / customer / regulator script"),
]

_CLIMATE_INDEX = [
    ("Framework gap analysis", "DJSI / CDP / MSCI score-driver mapping"),
    ("Disclosure roadmap", "12-month uplift to next quartile"),
    ("Investor + ESG-fund briefing", "story for inclusion vs exclusion"),
]

_TRANSITION = [
    ("Milestone delivery tracking", "SBTi-aligned annual scorecard"),
    ("Investor-day update", "transition capex + abatement curve"),
    ("Third-party validation", "SBTi / CDP submission"),
    ("Customer / supplier engagement", "Scope 3 reduction ladder"),
]

_PARTNERSHIP = [
    ("Co-marketing programme", "joint case studies + media"),
    ("Joint metrics disclosure", "shared KPI dashboard"),
    ("Partnership scale-up", "phase-2 rollout + investor case"),
]

_AWARD = [
    ("Marketing leverage", "B2B / customer-facing comms"),
    ("Talent acquisition campaign", "recruiter + EVP refresh"),
    ("Supplier validation", "use as preferred-vendor signal"),
]


_ARCHETYPE_MAP: dict[str, list[Archetype]] = {
    # Positive growth
    "event_contract_win": _POSITIVE_GROWTH,
    "event_capacity_addition": _CAPACITY_ADD,
    "event_esg_certification": _ESG_CERT,
    "event_order_book_update": _POSITIVE_GROWTH,
    "event_green_finance_milestone": _GREEN_FINANCE,
    "event_transition_announcement": _TRANSITION,
    "event_esg_partnership": _PARTNERSHIP,
    "event_award_recognition": _AWARD,

    # Negative / disruption
    "event_supply_chain_disruption": _SUPPLY_CHAIN_NEG,
    "event_social_violation": _SOCIAL_VIOLATION,
    "event_labour_strike": _LABOUR_STRIKE,
    "event_cyber_incident": _CYBER,
    "event_community_protest": _COMMUNITY_PROTEST,
    "event_ngo_report": _NGO_REPORT,
    "event_license_revocation": _LICENSE_REVOCATION,

    # Regulatory / governance
    "event_regulatory_policy": _REGULATORY_POLICY,
    "event_board_change": _BOARD_CHANGE,
    "event_credit_rating": _CREDIT_RATING,

    # Financial / routine
    "event_quarterly_results": _QUARTERLY,
    "event_analyst_outlook": _ANALYST_OUTLOOK,
    "event_dividend_policy": _DIVIDEND,
    "event_ma_deal": _MA_DEAL,
    "event_climate_disclosure_index": _CLIMATE_INDEX,
}


def get_archetypes_for_event(event_id: str) -> list[Archetype]:
    """Return the list of recommendation archetypes appropriate for the
    given event_id. Returns [] for unknown events — caller falls through
    to the generic prompt (preserves backwards behaviour).
    """
    return _ARCHETYPE_MAP.get(event_id or "", [])


# Phase 17 — theme-driven fallback when event classification is empty.
# Pre-fix, an article whose ontology event-keyword scan came up empty
# (e.g. an esoteric story whose keywords don't match any of the 22
# event types) would fall through to the generic 5-rec disclosure
# template. Now we route by `themes.primary_theme` to keep the rec set
# at least theme-appropriate. Mapped to existing archetype lists rather
# than inventing new ones — the universe of possibilities is the same.
_THEME_TO_ARCHETYPES: dict[str, list[Archetype]] = {
    # Environmental
    "climate change": _TRANSITION,
    "ghg emissions": _TRANSITION,
    "water": _CAPACITY_ADD,
    "biodiversity": _NGO_REPORT,
    "circular economy": _CAPACITY_ADD,
    "pollution": _NGO_REPORT,
    "energy": _TRANSITION,
    "renewable energy": _CAPACITY_ADD,
    # Social
    "labour rights": _SOCIAL_VIOLATION,
    "human rights": _SOCIAL_VIOLATION,
    "community impact": _COMMUNITY_PROTEST,
    "supply chain": _SUPPLY_CHAIN_NEG,
    "supply chain labor": _SOCIAL_VIOLATION,
    "diversity & inclusion": _BOARD_CHANGE,
    "health & safety": _LABOUR_STRIKE,
    # Governance
    "board & leadership": _BOARD_CHANGE,
    "transparency & disclosure": _REGULATORY_POLICY,
    "risk management": _CYBER,
    "anti-corruption": _NGO_REPORT,
    "tax compliance": _REGULATORY_POLICY,
    "financial performance": _QUARTERLY,
    "data privacy": _CYBER,
}


def get_archetypes_for_theme(primary_theme: str) -> list[Archetype]:
    """Theme-keyed archetype fallback (Phase 17).

    Used when `get_archetypes_for_event(event_id)` returns []. Keeps the
    LLM rec set thematically anchored even when the event keyword scan
    didn't hit any of the 22 known event types. Returns [] if the theme
    is also unmapped — caller should then fall through to the generic
    prompt (last-resort behaviour).
    """
    if not primary_theme:
        return []
    return _THEME_TO_ARCHETYPES.get(primary_theme.strip().lower(), [])


# Phase 17 — events that can plausibly be positive OR negative depending
# on the article's tone. For these we route polarity by the NLP sentiment
# score rather than the static event_id list.
#
# Live-fail example (IDFC First Bank Q4 calendar article, 2026-04-24):
#   event_id = event_quarterly_results, sentiment = +1, profit +48% YoY,
#   NPA improving, provisions DOWN 12%. But event_quarterly_results was
#   in NEITHER the static positive NOR the static negative list, so the
#   dispatcher defaulted to the negative-event prompt and Stage 10
#   injected "190.5 bps margin compression / ₹500 Cr at risk" framing.
_AMBIGUOUS_EVENTS = frozenset({
    "event_quarterly_results",
    "event_dividend_policy",
    "event_ma_deal",
    "event_esg_rating_change",
    "event_climate_disclosure_index",
})


_STATIC_POSITIVE_EVENTS = frozenset({
    "event_contract_win",
    "event_capacity_addition",
    "event_esg_certification",
    "event_order_book_update",
    "event_green_finance_milestone",
    "event_transition_announcement",
    "event_esg_partnership",
    "event_award_recognition",
})


def is_positive_event(event_id: str, sentiment: int | float | None = None) -> bool:
    """True for events whose effective polarity is upside / growth.

    Phase 17: now sentiment-aware. For events in `_STATIC_POSITIVE_EVENTS`
    the answer is always True. For events in `_AMBIGUOUS_EVENTS` (e.g.
    quarterly results, M&A deals, rating changes — which can be positive
    or negative), polarity is decided by `sentiment >= 1` from the NLP
    extraction. For everything else, returns False.

    `sentiment` is optional for back-compat. When omitted, ambiguous
    events stay treated as negative (preserves prior behaviour).

    Used by:
      - insight_generator.py to pick _SYSTEM_PROMPT vs _SYSTEM_PROMPT + _POSITIVE_INSIGHT_DIRECTIVE
      - recommendation_engine.py to pick _GENERATOR_SYSTEM vs _POSITIVE_GENERATOR_SYSTEM
      - ceo_narrative_generator.py to pick stakeholder polarity flavour
      - output_verifier.py to seed `event_sign` for narrative-coherence check
    """
    if not event_id:
        return False
    if event_id in _STATIC_POSITIVE_EVENTS:
        return True
    if event_id in _AMBIGUOUS_EVENTS and sentiment is not None:
        try:
            return float(sentiment) >= 1
        except (TypeError, ValueError):
            return False
    return False


def is_ambiguous_event(event_id: str) -> bool:
    """True for events whose polarity depends on sentiment (Phase 17).

    Used by the verifier's narrative-coherence check so it knows to
    consult sentiment instead of just the static event-list when
    deciding whether output framing is consistent."""
    return event_id in _AMBIGUOUS_EVENTS
