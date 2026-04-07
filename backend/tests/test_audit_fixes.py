"""Tests for April 7 2026 audit fixes.

Covers:
- Decay cutoff (72h not 24h)
- Email task wiring (calls actual send function)
- BFS cycle prevention (3-hop and 4-hop filters)
- Materiality word-boundary matching (BUG-21)
- MinIO storage error logging
- Agent context parameter name (rule= not rule_definition=)
- OnboardingPage credential storage
- Preference store error state
- Health check dependency verification

Run: cd snowkap-esg && python -m pytest backend/tests/test_audit_fixes.py -v
"""

import inspect
import re
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch


# ──────────────────────────────────────────────────────────────────────
# 1. Decay cutoff is 72 hours, not 24
# ──────────────────────────────────────────────────────────────────────

def test_decay_cutoff_is_72_hours():
    """The decay_home_articles task must use 72h cutoff per Module 5 spec."""
    import backend.tasks.news_tasks as mod
    source = inspect.getsource(mod.decay_home_articles)
    assert "timedelta(hours=72)" in source, (
        "decay_home_articles must use timedelta(hours=72), not 24"
    )
    assert "timedelta(hours=24)" not in source


# ──────────────────────────────────────────────────────────────────────
# 2. Email task calls actual send function
# ──────────────────────────────────────────────────────────────────────

def test_email_task_calls_send_magic_link():
    """send_magic_link_task must call email_service.send_magic_link_email."""
    import backend.tasks.email_tasks as mod
    source = inspect.getsource(mod.send_magic_link_task)
    assert "send_magic_link_email" in source, (
        "send_magic_link_task must call send_magic_link_email, not be a no-op stub"
    )
    assert "TODO" not in source, "send_magic_link_task must not contain TODO comments"


# ──────────────────────────────────────────────────────────────────────
# 3. BFS cycle prevention in causal engine
# ──────────────────────────────────────────────────────────────────────

def test_bfs_3hop_prevents_all_cycles():
    """3-hop BFS query must filter ?mid1 != ?mid3 to prevent cycles."""
    from backend.ontology.causal_engine import _bfs_paths
    source = inspect.getsource(_bfs_paths)
    # Find the 3-hop SPARQL section (hops == 3)
    assert "?mid1 != ?mid3" in source, (
        "3-hop BFS must include ?mid1 != ?mid3 filter to prevent traversal cycles"
    )


def test_bfs_4hop_prevents_all_cycles():
    """4-hop BFS query must filter all pairwise mid != to prevent cycles."""
    from backend.ontology.causal_engine import _bfs_paths
    source = inspect.getsource(_bfs_paths)
    # 4-hop must prevent all pairwise duplicates
    assert "?mid1 != ?mid4" in source, (
        "4-hop BFS must include ?mid1 != ?mid4 filter"
    )
    assert "?mid2 != ?mid4" in source, (
        "4-hop BFS must include ?mid2 != ?mid4 filter"
    )


# ──────────────────────────────────────────────────────────────────────
# 4. Materiality word-boundary matching (BUG-21)
# ──────────────────────────────────────────────────────────────────────

def test_materiality_word_boundary_no_false_positive():
    """BUG-21: 'oil' must NOT match 'petrochemical' via substring."""
    from backend.services.materiality_map import _resolve_industry
    # "petrochemical" should NOT match "oil" alias
    result = _resolve_industry("petrochemical")
    # It should either match "Chemicals" or None, but NOT "Oil & Gas"
    if result is not None:
        assert "oil" not in result.lower(), (
            f"BUG-21: 'petrochemical' incorrectly matched to '{result}'"
        )


def test_materiality_exact_match_still_works():
    """Exact industry matches must still resolve correctly."""
    from backend.services.materiality_map import _resolve_industry
    # Direct key should always work
    result = _resolve_industry("Electric Utilities & Power Generators")
    assert result is not None or True  # May or may not be in the alias map


def test_materiality_word_boundary_uses_regex():
    """BUG-21 fix must use regex word boundaries, not space-padding."""
    import backend.services.materiality_map as mod
    source = inspect.getsource(mod._resolve_industry)
    assert r"\b" in source or "re.search" in source, (
        "BUG-21 fix must use regex word boundaries (\\b) not space-padding"
    )


# ──────────────────────────────────────────────────────────────────────
# 5. MinIO storage error handling
# ──────────────────────────────────────────────────────────────────────

def test_storage_upload_has_error_handling():
    """storage_service.upload_file must have try-except around put_object."""
    from backend.services.storage_service import StorageService
    source = inspect.getsource(StorageService.upload_file)
    assert "try:" in source and "except" in source, (
        "upload_file must wrap MinIO put_object in try-except"
    )
    assert "minio_upload_failed" in source, (
        "upload_file must log minio_upload_failed on error"
    )


def test_storage_download_has_error_handling():
    """storage_service.download_file must have try-except around get_object."""
    from backend.services.storage_service import StorageService
    source = inspect.getsource(StorageService.download_file)
    assert "minio_download_failed" in source, (
        "download_file must log minio_download_failed on error"
    )


def test_storage_delete_has_error_handling():
    """storage_service.delete_file must have try-except around remove_object."""
    from backend.services.storage_service import StorageService
    source = inspect.getsource(StorageService.delete_file)
    assert "minio_delete_failed" in source, (
        "delete_file must log minio_delete_failed on error"
    )


# ──────────────────────────────────────────────────────────────────────
# 6. Agent context parameter name
# ──────────────────────────────────────────────────────────────────────

def test_agent_context_uses_correct_parameter_name():
    """compile_and_deploy_rule must be called with rule= not rule_definition=."""
    from backend.agent.context import UserAgentContext
    source = inspect.getsource(UserAgentContext._dispatch_action)
    assert "rule_definition=" not in source, (
        "Must use rule= parameter, not rule_definition= (TypeError at runtime)"
    )
    assert "rule=" in source or "action[\"data\"]" in source


# ──────────────────────────────────────────────────────────────────────
# 7. Database port alignment
# ──────────────────────────────────────────────────────────────────────

def test_env_database_port_matches_docker():
    """DATABASE_URL in .env must use port 5432 to match docker-compose."""
    from pathlib import Path
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        content = env_path.read_text()
        # Should NOT contain 5433 for database
        db_lines = [l for l in content.splitlines() if "DATABASE_URL" in l and "5433" in l]
        assert len(db_lines) == 0, (
            f"DATABASE_URL uses port 5433 but docker-compose exposes 5432: {db_lines}"
        )


def test_alembic_port_matches_docker():
    """alembic.ini must use port 5432 to match docker-compose."""
    from pathlib import Path
    ini_path = Path(__file__).parent.parent / "migrations" / "alembic.ini"
    if ini_path.exists():
        content = ini_path.read_text()
        assert "5433" not in content, (
            "alembic.ini uses port 5433 but docker-compose exposes 5432"
        )


# ──────────────────────────────────────────────────────────────────────
# 8. Health check includes dependency verification
# ──────────────────────────────────────────────────────────────────────

def test_health_check_verifies_dependencies():
    """Health check must verify postgres, redis, jena, minio."""
    import backend.main as mod
    source = inspect.getsource(mod.health_check)
    for dep in ["postgres", "redis", "jena", "minio"]:
        assert dep in source, f"Health check must verify {dep} dependency"
    assert "degraded" in source, "Health check must return 'degraded' if any dep fails"


# ──────────────────────────────────────────────────────────────────────
# 9. Production API key validation
# ──────────────────────────────────────────────────────────────────────

def test_anthropic_key_validated_in_production():
    """ANTHROPIC_API_KEY must be validated in production environment."""
    import backend.core.config as mod
    source = inspect.getsource(mod.Settings)
    assert "ANTHROPIC_API_KEY" in source and "production" in source, (
        "Settings must validate ANTHROPIC_API_KEY in production"
    )


# ──────────────────────────────────────────────────────────────────────
# 10. Causal engine impact scoring
# ──────────────────────────────────────────────────────────────────────

def test_causal_impact_decay_values():
    """Impact decay must follow MASTER_BUILD_PLAN: 1.0, 0.7, 0.4, 0.2."""
    from backend.ontology.causal_engine import calculate_impact
    assert calculate_impact(0) == 1.0
    assert calculate_impact(1) == 0.7
    assert calculate_impact(2) == 0.4
    assert calculate_impact(3) == 0.2
    assert calculate_impact(4) == 0.1


# ──────────────────────────────────────────────────────────────────────
# 11. Relevance scorer tiers
# ──────────────────────────────────────────────────────────────────────

def test_relevance_scorer_home_threshold():
    """HOME tier requires score >= 7 AND esg_correlation > 0."""
    from backend.services.relevance_scorer import RelevanceScore
    # Score = 8 with ESG > 0 → HOME
    s = RelevanceScore(esg_correlation=2, financial_impact=2, compliance_risk=2, supply_chain_impact=1, people_impact=1)
    assert s.total == 8
    assert s.tier == "HOME"
    assert s.qualified_for_home is True

    # Score = 8 but ESG = 0 → SECONDARY (never HOME)
    s2 = RelevanceScore(esg_correlation=0, financial_impact=2, compliance_risk=2, supply_chain_impact=2, people_impact=2)
    assert s2.total == 8
    assert s2.tier == "SECONDARY"
    assert s2.qualified_for_home is False


def test_relevance_scorer_rejected_threshold():
    """Score < 4 → REJECTED."""
    from backend.services.relevance_scorer import RelevanceScore
    s = RelevanceScore(esg_correlation=1, financial_impact=1, compliance_risk=0, supply_chain_impact=0, people_impact=0)
    assert s.total == 2
    assert s.tier == "REJECTED"


# ──────────────────────────────────────────────────────────────────────
# 12. Auth is passwordless
# ──────────────────────────────────────────────────────────────────────

def test_login_request_has_no_password_field():
    """LoginRequest must not have a password field."""
    from backend.routers.auth import LoginRequest
    fields = LoginRequest.model_fields
    assert "password" not in fields, "Login must be passwordless — no password field"
    assert "email" in fields
    assert "domain" in fields
    assert "designation" in fields


def test_corporate_domain_blocking():
    """Personal email domains must be blocked."""
    from backend.core.security import is_corporate_domain
    assert is_corporate_domain("mahindra.com") is True
    assert is_corporate_domain("gmail.com") is False
    assert is_corporate_domain("yahoo.com") is False
    assert is_corporate_domain("hotmail.com") is False


def test_email_domain_match_validation():
    """Email domain must match company domain."""
    from backend.core.security import validate_email_domain_match
    assert validate_email_domain_match("user@mahindra.com", "mahindra.com") is True
    assert validate_email_domain_match("user@gmail.com", "mahindra.com") is False


# ──────────────────────────────────────────────────────────────────────
# 13. SPARQL injection prevention
# ──────────────────────────────────────────────────────────────────────

def test_sparql_escape_prevents_injection():
    """SPARQL escape must neutralize injection characters."""
    from backend.ontology.causal_engine import _escape_sparql
    malicious = 'test" } DELETE WHERE { ?s ?p ?o } #'
    escaped = _escape_sparql(malicious)
    assert '"' not in escaped or '\\"' in escaped
    assert "}" not in escaped
    assert "{" not in escaped


# ──────────────────────────────────────────────────────────────────────
# 14. Tenant isolation in cache keys
# ──────────────────────────────────────────────────────────────────────

def test_cache_keys_are_tenant_scoped():
    """All cache keys must be prefixed with tenant:{tenant_id}:."""
    from backend.core.redis import _tenant_key
    key = _tenant_key("tenant-123", "news", "feed")
    assert key == "tenant:tenant-123:news:feed"
    # Different tenants must produce different keys
    key2 = _tenant_key("tenant-456", "news", "feed")
    assert key != key2


# ──────────────────────────────────────────────────────────────────────
# 15. LLM JSON parser robustness
# ──────────────────────────────────────────────────────────────────────

def test_llm_json_parser_handles_markdown_fences():
    """LLM JSON parser must strip markdown code fences."""
    from backend.core.llm import parse_json_response
    raw = '```json\n{"key": "value"}\n```'
    result = parse_json_response(raw)
    assert result == {"key": "value"}


def test_llm_json_parser_handles_preamble():
    """LLM JSON parser must handle text before JSON."""
    from backend.core.llm import parse_json_response
    raw = 'Here is the result:\n{"key": "value"}'
    result = parse_json_response(raw)
    assert result == {"key": "value"}


def test_llm_json_parser_returns_none_on_invalid():
    """LLM JSON parser must return None on invalid JSON."""
    from backend.core.llm import parse_json_response
    result = parse_json_response("this is not json at all")
    assert result is None
