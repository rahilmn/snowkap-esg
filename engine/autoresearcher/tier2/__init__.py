"""Tier 2 — per-user autoresearcher specifics.

- knob: PersonaWeightKnob (one per persona × esg_focus / framework /
  geography affinity bin)
- corpus: per-user click affinity history (which articles they
  clicked / saved / shared)
- metric: top-K predicted-vs-clicked recall on held-out user actions
- promoter: writes the tuned affinity back to the persona store
  (per-user, isolated, no cross-user blast radius)
- runner: entry point invoked by `--tier user --user <id>`
"""
