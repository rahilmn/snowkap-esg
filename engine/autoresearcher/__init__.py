"""Autoresearcher — Karpathy-style autonomous calibration loop.

Continuously proposes small perturbations to the prediction machinery
(ontology weights, scorer components, primitive cascade β, keyword
sets, ordinal mappings), replays them against a held-out corpus, and
keeps changes that improve a scalar calibration metric.

Architecture:
  - knobs.py            — Knob ABC (apply/revert/bounds/serialise)
  - knob_kinds/         — 10 concrete knob kinds (~555 atomic knobs total)
  - ontology_introspector.py — auto-discovers all atomic knobs from TTL + scorer
  - metrics.py          — composite calibration metric
  - corpus.py           — held-out article corpus + gold labels from audit logs
  - ledger.py           — append-only experiment journal
  - experimenter.py     — deterministic structured random walk
  - evaluator.py        — replay loop (snapshot-state, idempotent)
  - loop.py             — outer keep/discard with budget
  - tier0/              — system-tier specifics (corpus + metric + promoter)
"""
