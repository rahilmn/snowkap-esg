"""Concrete Knob subclasses.

Each module here exports one Knob class for one tunable predicate
family. The autoresearcher's `ontology_introspector` instantiates
them by inspecting the live ontology + scorer.

Kinds shipped in this session (Tier 0):
  - ontology_weight       — materiality + risk-weight + regional-boost
  - scorer_component      — criticality WEIGHTS_DEFAULT + per-role weights
  - ordinal_mapping       — quantitative-mapping TTL values
  - keyword_set           — event-type keyword membership
  - primitive_beta        — causal-edge β elasticity
  - penalty_magnitude     — criticality penalty thresholds
  - inaction_score        — risk-of-inaction baseScore + recTypeBonus

Kinds STUBBED (deferred to Tier 1/2 sessions):
  - primitive_lag         — placeholder
  - risk_threshold        — placeholder
  - set_membership        — placeholder
"""
