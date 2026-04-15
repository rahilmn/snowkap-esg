"""Self-evolving ontology discovery module (Phase 19).

Examines pipeline output after each article, identifies genuinely new
knowledge (entities, themes, events, causal edges, frameworks), and
promotes qualifying discoveries into ``data/ontology/discovered.ttl``.

Usage::

    from engine.ontology.discovery.collector import collect_discoveries
    from engine.ontology.discovery.promoter import batch_promote

    # After pipeline writes output:
    candidates = collect_discoveries(result, insight, company)

    # Periodically (every 30 min) or manually:
    promoted = batch_promote()
"""
