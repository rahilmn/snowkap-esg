"""Phase 3 §5.2 — Stage 11 role generators.

Each generator consumes a shared EvidencePack and emits a
RoleDistinctPayload tailored to its role. This package will house
three generators (CFO / CEO / Analyst); today only CFO is shipped as
a deterministic baseline so the contract is locked before the LLM
prompts land.
"""
from engine.analysis.role_generators.types import (
    HeroMetric,
    RecommendationStub,
    RoleDistinctPayload,
)
from engine.analysis.role_generators.cfo import generate_cfo_payload
from engine.analysis.role_generators.ceo import generate_ceo_payload
from engine.analysis.role_generators.analyst import generate_analyst_payload
from engine.analysis.role_generators.dispatcher import (
    dispatch_role_payloads,
    dispatch_role_payloads_as_dict,
    role_keys,
)

__all__ = [
    "HeroMetric",
    "RecommendationStub",
    "RoleDistinctPayload",
    "generate_cfo_payload",
    "generate_ceo_payload",
    "generate_analyst_payload",
    "dispatch_role_payloads",
    "dispatch_role_payloads_as_dict",
    "role_keys",
]
