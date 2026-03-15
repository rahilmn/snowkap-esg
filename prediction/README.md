# MiroFish Prediction Engine

Multi-agent ESG prediction simulation engine. Runs as a separate microservice on port 5001 (AGPL-3.0 process isolation).

## Architecture

```
[News Event + Causal Chain] → Celery Task → POST /predict/simulate → MiroFish
    ↓
[Graph Builder] → Extract company subgraph from Jena
    ↓
[Config Generator] → Build simulation config from impact severity + tenant settings
    ↓
[Profile Generator] → Create 20-50 ESG agent personas (CEO, CSO, CFO, Regulator, etc.)
    ↓
[Simulation Runner] → Multi-round agent deliberation (10-40 rounds)
    ↓
[Report Agent] → Generate structured prediction report
    ↓
[Results] → PostgreSQL (prediction_reports) + Jena triples
```

## Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI service entry point (port 5001) |
| `config.py` | MiroFish-specific settings |
| `graph_builder.py` | Extracts company subgraph from Jena for seed data |
| `ontology_generator.py` | ESG dimensions, scenario archetypes, classification |
| `simulation_config_generator.py` | Builds simulation config from tenant + news context |
| `oasis_profile_generator.py` | 10 ESG agent templates (CEO, CSO, CFO, Regulator, etc.) |
| `simulation_runner.py` | Multi-round agent simulation with convergence detection |
| `simulation_manager.py` | Orchestrates the full prediction pipeline |
| `report_agent.py` | Generates structured prediction reports |
| `zep_entity_reader.py` | Reads/writes company memory in Zep Cloud |
| `zep_graph_memory_updater.py` | Stores prediction results back into Jena |

## Trigger Conditions

MiroFish is NOT run on every article. Per CLAUDE.md Rule #3:

```python
TRIGGER_CONDITIONS = {
    "impact_score_threshold": 70,
    "causal_chain_hops": 2,
    "financial_exposure_min": 1_000_000,  # ₹10L+
    "user_requested": True,  # Manual trigger always allowed
}
```

## Running

```bash
# Via Docker
docker compose up mirofish

# Standalone
cd prediction && uvicorn prediction.app:app --host 0.0.0.0 --port 5001
```
