"""Phase C — Advisor coach personas.

All 5 persona coaches: DataCoach, RiskCoach, ForecastCoach,
BeliefCoach, AutoresearcherCoach. Each consumes a specific
event subclass and returns 0+ AdvisorHints.
"""
from engine.advisor.personas.autoresearcher_coach import AutoresearcherCoach
from engine.advisor.personas.belief_coach import BeliefCoach
from engine.advisor.personas.data_coach import DataCoach
from engine.advisor.personas.forecast_coach import ForecastCoach
from engine.advisor.personas.risk_coach import RiskCoach

__all__ = [
    "AutoresearcherCoach",
    "BeliefCoach",
    "DataCoach",
    "ForecastCoach",
    "RiskCoach",
]
