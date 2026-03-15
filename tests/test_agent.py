"""Agent tests — routing, specialist selection, personality loading, context.

Covers:
- Agent routing by keyword matching
- LLM intent classification fallback
- Personality file loading
- Agent service roster completeness
- UserAgentContext auth-parity (Three Parity Rules)
- Confirmation-gated writes
"""

import pytest

from backend.services.agent_service import AGENT_ROSTER, load_personality, route_to_specialist
from backend.agent.context import UserAgentContext
from backend.core.dependencies import CurrentUser


# --- Agent Routing Tests ---

class TestAgentRouting:
    def test_supply_chain_keywords(self):
        assert route_to_specialist("What is my supply chain risk?") == "supply_chain"
        assert route_to_specialist("Show me scope 3 emissions") == "supply_chain"
        assert route_to_specialist("List our tier 1 suppliers") == "supply_chain"

    def test_compliance_keywords(self):
        assert route_to_specialist("Are we BRSR compliant?") == "compliance"
        assert route_to_specialist("Show GRI disclosure gaps") == "compliance"
        assert route_to_specialist("TCFD framework alignment") == "compliance"

    def test_executive_keywords(self):
        assert route_to_specialist("Create executive summary for CEO") == "executive"
        assert route_to_specialist("Board briefing on ESG performance") == "executive"

    def test_trend_keywords(self):
        assert route_to_specialist("What are emerging ESG trends?") == "trend"
        assert route_to_specialist("Forecast ESG outlook for next year") == "trend"

    def test_legal_keywords(self):
        assert route_to_specialist("What is CBAM and how does it affect us?") == "legal"
        assert route_to_specialist("SEBI regulatory compliance deadline") == "legal"

    def test_content_keywords(self):
        assert route_to_specialist("Draft a sustainability report section") == "content"
        assert route_to_specialist("Write our newsletter about ESG") == "content"

    def test_opportunity_keywords(self):
        assert route_to_specialist("What green revenue opportunities exist?") == "opportunity"
        assert route_to_specialist("ESG investment ROI analysis") == "opportunity"

    def test_default_fallback_is_analytics(self):
        assert route_to_specialist("Hello") == "analytics"
        assert route_to_specialist("Tell me something") == "analytics"


# --- Agent Roster Tests ---

class TestAgentRoster:
    EXPECTED_AGENTS = {
        "supply_chain", "compliance", "analytics", "executive",
        "trend", "stakeholder", "opportunity", "content", "legal",
    }

    def test_all_nine_agents_present(self):
        assert set(AGENT_ROSTER.keys()) == self.EXPECTED_AGENTS

    def test_each_agent_has_required_fields(self):
        for agent_id, config in AGENT_ROSTER.items():
            assert "name" in config, f"{agent_id} missing name"
            assert "personality_key" in config, f"{agent_id} missing personality_key"
            assert "keywords" in config, f"{agent_id} missing keywords"
            assert "tools" in config, f"{agent_id} missing tools"
            assert len(config["keywords"]) >= 3, f"{agent_id} needs at least 3 keywords"

    def test_no_duplicate_personality_keys(self):
        keys = [config["personality_key"] for config in AGENT_ROSTER.values()]
        assert len(keys) == len(set(keys)), "Duplicate personality keys found"


# --- Personality Loading Tests ---

class TestPersonalityLoading:
    @pytest.mark.parametrize("key", [
        "supply_chain", "compliance", "analytics", "executive",
        "trend", "stakeholder", "opportunity", "content", "legal",
    ])
    def test_personality_file_loads(self, key: str):
        personality = load_personality(key)
        assert len(personality) > 100, f"Personality for {key} seems too short"

    def test_missing_personality_returns_fallback(self):
        personality = load_personality("nonexistent_agent")
        assert "ESG specialist" in personality


# --- UserAgentContext Tests (Phase 11) ---

class TestUserAgentContext:
    def _make_user(self, permissions: list[str]) -> CurrentUser:
        return CurrentUser(
            user_id="u1",
            tenant_id="t1",
            company_id="c1",
            designation="Analyst",
            permissions=permissions,
            domain="test.com",
        )

    def test_can_read_with_permission(self):
        user = self._make_user(["view_dashboard", "view_news"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert ctx.can_read("companies")
        assert ctx.can_read("articles")

    def test_cannot_read_without_permission(self):
        user = self._make_user([])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert not ctx.can_read("companies")
        assert not ctx.can_read("articles")

    def test_can_write_with_permission(self):
        user = self._make_user(["edit_analysis", "manage_rules", "trigger_predictions"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert ctx.can_write("analysis")
        assert ctx.can_write("ontology_rules")
        assert ctx.can_write("predictions")

    def test_cannot_write_without_permission(self):
        user = self._make_user(["view_dashboard"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert not ctx.can_write("analysis")
        assert not ctx.can_write("ontology_rules")

    def test_cannot_write_unknown_resource(self):
        user = self._make_user(["edit_analysis"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert not ctx.can_write("unknown_resource")

    def test_request_confirmation_queues_action(self):
        user = self._make_user(["trigger_predictions"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")

        pending = ctx.request_confirmation({
            "type": "trigger_prediction",
            "description": "Run simulation for article X",
            "resource": "predictions",
            "data": {"article_id": "a1"},
        })

        assert pending["status"] == "pending_confirmation"
        assert pending["id"] == "action_0"
        assert len(ctx.pending_actions) == 1

    def test_reject_action_removes_from_pending(self):
        user = self._make_user(["trigger_predictions"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")

        ctx.request_confirmation({
            "type": "trigger_prediction",
            "description": "Test action",
            "resource": "predictions",
        })

        result = ctx.reject_action("action_0")
        assert result["status"] == "rejected"
        assert len(ctx.pending_actions) == 0

    def test_reject_nonexistent_action_returns_error(self):
        user = self._make_user([])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        result = ctx.reject_action("nonexistent")
        assert "error" in result

    def test_multiple_pending_actions(self):
        user = self._make_user(["trigger_predictions", "manage_rules"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")

        ctx.request_confirmation({"type": "trigger_prediction", "description": "Action 1", "resource": "predictions"})
        ctx.request_confirmation({"type": "create_rule", "description": "Action 2", "resource": "ontology_rules"})

        assert len(ctx.pending_actions) == 2
        assert ctx.pending_actions[0]["id"] == "action_0"
        assert ctx.pending_actions[1]["id"] == "action_1"

    def test_has_permission_checks_user_permissions(self):
        user = self._make_user(["view_dashboard", "view_news"])
        ctx = UserAgentContext(tenant_id="t1", user=user, conversation_id="conv1")
        assert ctx.has_permission("view_dashboard")
        assert ctx.has_permission("view_news")
        assert not ctx.has_permission("manage_users")
