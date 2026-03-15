"""UserAgentContext — auth-parity context for agent conversations.

Per MASTER_BUILD_PLAN Phase 11:
- UserAgentContext with auth-parity (Three Parity Rules)
- Confirmation-gated writes
- Conversation threads with Zep memory

Three Parity Rules:
1. Agent sees only what the user can see (tenant-scoped)
2. Agent can only modify what the user has permission to modify
3. Agent actions are logged with the user's identity
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.core.dependencies import CurrentUser

logger = structlog.get_logger()


@dataclass
class UserAgentContext:
    """Auth-parity context for agent operations.

    The agent operates with the same permissions as the user who
    initiated the conversation. All operations are tenant-scoped
    and permission-gated.
    """
    tenant_id: str
    user: CurrentUser
    conversation_id: str
    db: Any = None  # AsyncSession

    # Pending writes that need user confirmation
    pending_actions: list[dict] = field(default_factory=list)

    # Actions already confirmed and executed
    executed_actions: list[dict] = field(default_factory=list)

    def has_permission(self, perm: str) -> bool:
        """Check if the user has a specific permission."""
        return perm in self.user.permissions

    def can_read(self, resource: str) -> bool:
        """Check if the user can read a resource type."""
        read_perms = {
            "companies": "view_dashboard",
            "articles": "view_news",
            "analysis": "view_analysis",
            "predictions": "view_predictions",
            "ontology": "view_ontology",
            "media": "view_dashboard",
        }
        required = read_perms.get(resource, "view_dashboard")
        return self.has_permission(required)

    def can_write(self, resource: str) -> bool:
        """Check if the user can modify a resource type."""
        write_perms = {
            "analysis": "edit_analysis",
            "ontology_rules": "manage_rules",
            "predictions": "trigger_predictions",
            "reports": "verify_reports",
            "campaigns": "manage_campaigns",
        }
        required = write_perms.get(resource)
        if not required:
            return False
        return self.has_permission(required)

    def request_confirmation(self, action: dict) -> dict:
        """Queue a write action that needs user confirmation.

        Returns a confirmation request to display to the user.
        """
        action_id = f"action_{len(self.pending_actions)}"
        pending = {
            "id": action_id,
            "type": action["type"],
            "description": action["description"],
            "resource": action.get("resource", "unknown"),
            "data": action.get("data", {}),
            "status": "pending_confirmation",
        }
        self.pending_actions.append(pending)

        logger.info(
            "agent_action_pending",
            action_id=action_id,
            type=action["type"],
            tenant_id=self.tenant_id,
            user_id=self.user.user_id,
        )

        return pending

    async def execute_confirmed_action(self, action_id: str) -> dict:
        """Execute a previously confirmed action."""
        action = next((a for a in self.pending_actions if a["id"] == action_id), None)
        if not action:
            return {"error": "Action not found"}

        if not self.can_write(action["resource"]):
            return {"error": f"No permission to modify {action['resource']}"}

        # Execute based on type
        result = await self._dispatch_action(action)

        action["status"] = "executed"
        self.executed_actions.append(action)
        self.pending_actions = [a for a in self.pending_actions if a["id"] != action_id]

        logger.info(
            "agent_action_executed",
            action_id=action_id,
            type=action["type"],
            tenant_id=self.tenant_id,
            user_id=self.user.user_id,
        )

        return result

    async def _dispatch_action(self, action: dict) -> dict:
        """Dispatch a confirmed action to the appropriate handler."""
        action_type = action["type"]

        if action_type == "trigger_prediction":
            from backend.tasks.prediction_tasks import trigger_simulation_task
            task = trigger_simulation_task.delay(
                tenant_id=self.tenant_id,
                article_id=action["data"].get("article_id"),
                company_id=action["data"].get("company_id"),
                user_requested=True,
            )
            return {"status": "queued", "task_id": task.id}

        elif action_type == "create_ontology_rule":
            from backend.ontology.rule_compiler import compile_and_deploy_rule
            result = await compile_and_deploy_rule(
                tenant_id=self.tenant_id,
                rule_definition=action["data"],
            )
            return {"status": "created", "result": result}

        elif action_type == "analyze_article":
            from backend.tasks.ontology_tasks import analyze_article_impact_task
            task = analyze_article_impact_task.delay(
                article_id=action["data"].get("article_id"),
                tenant_id=self.tenant_id,
            )
            return {"status": "queued", "task_id": task.id}

        return {"error": f"Unknown action type: {action_type}"}

    def reject_action(self, action_id: str) -> dict:
        """Reject a pending action."""
        action = next((a for a in self.pending_actions if a["id"] == action_id), None)
        if not action:
            return {"error": "Action not found"}

        action["status"] = "rejected"
        self.pending_actions = [a for a in self.pending_actions if a["id"] != action_id]

        logger.info(
            "agent_action_rejected",
            action_id=action_id,
            tenant_id=self.tenant_id,
            user_id=self.user.user_id,
        )

        return {"status": "rejected", "action_id": action_id}
