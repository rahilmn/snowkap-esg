"""MiroFish prediction engine — Phase 4 implementation.

Per CLAUDE.md: Separate microservice on port 5001, AGPL-3.0 process isolation.
Per MASTER_BUILD_PLAN Phase 4:
- 20-50 agents per simulation, 10-40 rounds
- ESG-specific agent profiles (CEO, Sustainability Officer, etc.)
- Jena subgraph as seed data
- Results → PostgreSQL + Jena triples
"""
