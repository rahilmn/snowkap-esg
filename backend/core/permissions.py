"""ESG permission sets and RBAC.

Per MASTER_BUILD_PLAN Phase 7:
- ESG permission sets (view_emissions, edit_analysis, verify_reports, etc.)
- Designation → role mapping (auto from login)
- Permission-gated route decorators
"""

from enum import StrEnum
from functools import wraps
from typing import Any, Callable

from fastapi import HTTPException, status

import structlog

logger = structlog.get_logger()


class Permission(StrEnum):
    """All ESG platform permissions."""
    # Dashboard
    VIEW_DASHBOARD = "view_dashboard"

    # News
    VIEW_NEWS = "view_news"
    MANAGE_NEWS_SOURCES = "manage_news_sources"

    # Analysis
    VIEW_ANALYSIS = "view_analysis"
    EDIT_ANALYSIS = "edit_analysis"
    VERIFY_REPORTS = "verify_reports"
    EXPORT_DATA = "export_data"

    # Predictions (MiroFish)
    VIEW_PREDICTIONS = "view_predictions"
    TRIGGER_PREDICTIONS = "trigger_predictions"

    # Ontology
    VIEW_ONTOLOGY = "view_ontology"
    MANAGE_ONTOLOGY = "manage_ontology"
    MANAGE_RULES = "manage_rules"
    MANAGE_ASSERTIONS = "manage_assertions"

    # Campaigns
    VIEW_CAMPAIGNS = "view_campaigns"
    MANAGE_CAMPAIGNS = "manage_campaigns"

    # Reports
    VIEW_REPORTS = "view_reports"
    GENERATE_REPORTS = "generate_reports"

    # Tenant admin
    MANAGE_USERS = "manage_users"
    MANAGE_TENANT = "manage_tenant"
    MANAGE_ROLES = "manage_roles"

    # Platform admin (Snowkap staff only)
    PLATFORM_ADMIN = "platform_admin"
    IMPERSONATE_USER = "impersonate_user"
    VIEW_ALL_TENANTS = "view_all_tenants"

    # Phase 10: sales super-admin
    SUPER_ADMIN = "super_admin"
    OVERRIDE_TENANT_CONTEXT = "override_tenant_context"
    MANAGE_DRIP_CAMPAIGNS = "manage_drip_campaigns"


class Role(StrEnum):
    """Platform roles mapped from designation at login."""
    EXECUTIVE = "executive_view"
    SUSTAINABILITY_MANAGER = "sustainability_manager"
    ANALYST = "data_entry_analyst"
    MEMBER = "member"
    TENANT_ADMIN = "admin"
    PLATFORM_ADMIN = "platform_admin"
    # Phase 10: internal Snowkap sales users with cross-tenant + campaign perms
    SUPER_ADMIN = "super_admin"


# Role → Permissions mapping
ROLE_PERMISSIONS: dict[str, list[str]] = {
    Role.MEMBER: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_NEWS,
        Permission.VIEW_ANALYSIS,
    ],
    Role.ANALYST: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_NEWS,
        Permission.VIEW_ANALYSIS,
        Permission.EDIT_ANALYSIS,
        Permission.VIEW_PREDICTIONS,
        Permission.VIEW_ONTOLOGY,
        Permission.VIEW_CAMPAIGNS,
        Permission.VIEW_REPORTS,
    ],
    Role.EXECUTIVE: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_NEWS,
        Permission.VIEW_ANALYSIS,
        Permission.VIEW_PREDICTIONS,
        Permission.VIEW_REPORTS,
        Permission.EXPORT_DATA,
        Permission.VIEW_ONTOLOGY,
        Permission.VIEW_CAMPAIGNS,
    ],
    Role.SUSTAINABILITY_MANAGER: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_NEWS,
        Permission.VIEW_ANALYSIS,
        Permission.EDIT_ANALYSIS,
        Permission.VERIFY_REPORTS,
        Permission.VIEW_PREDICTIONS,
        Permission.TRIGGER_PREDICTIONS,
        Permission.VIEW_ONTOLOGY,
        Permission.MANAGE_ONTOLOGY,
        Permission.MANAGE_RULES,
        Permission.MANAGE_ASSERTIONS,
        Permission.VIEW_CAMPAIGNS,
        Permission.MANAGE_CAMPAIGNS,
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORTS,
        Permission.EXPORT_DATA,
    ],
    Role.TENANT_ADMIN: [
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_NEWS,
        Permission.MANAGE_NEWS_SOURCES,
        Permission.VIEW_ANALYSIS,
        Permission.EDIT_ANALYSIS,
        Permission.VERIFY_REPORTS,
        Permission.VIEW_PREDICTIONS,
        Permission.TRIGGER_PREDICTIONS,
        Permission.VIEW_ONTOLOGY,
        Permission.MANAGE_ONTOLOGY,
        Permission.MANAGE_RULES,
        Permission.MANAGE_ASSERTIONS,
        Permission.VIEW_CAMPAIGNS,
        Permission.MANAGE_CAMPAIGNS,
        Permission.VIEW_REPORTS,
        Permission.GENERATE_REPORTS,
        Permission.EXPORT_DATA,
        Permission.MANAGE_USERS,
        Permission.MANAGE_TENANT,
        Permission.MANAGE_ROLES,
    ],
    Role.PLATFORM_ADMIN: [
        # Platform admins get everything
        p.value for p in Permission
    ],
    Role.SUPER_ADMIN: [
        # Snowkap sales super-admins: every platform permission + cross-tenant
        # switching + drip-campaign management. Role is granted only via the
        # SNOWKAP_INTERNAL_EMAILS allowlist in the auth flow.
        p.value for p in Permission
    ],
}

# Designation → Role mapping (used during auth)
DESIGNATION_ROLE_MAP: dict[str, str] = {
    # Module 6: Maps designations to role_curation.py profile keys
    "ceo": "ceo",
    "cfo": "cfo",
    "cto": "ceo",
    "coo": "ceo",
    "managing director": "ceo",
    "board member": "board_member",
    "board director": "board_member",
    "independent director": "board_member",
    "chairman": "board_member",
    "head of sustainability": "cso",
    "sustainability manager": "cso",
    "sustainability officer": "cso",
    "chief sustainability officer": "cso",
    "esg manager": "cso",
    "esg head": "cso",
    "compliance officer": "compliance",
    "compliance head": "compliance",
    "legal head": "compliance",
    "general counsel": "compliance",
    "supply chain head": "supply_chain",
    "supply chain manager": "supply_chain",
    "procurement head": "supply_chain",
    "operations head": "supply_chain",
    "esg analyst": Role.ANALYST,
    "analyst": Role.ANALYST,
    "data analyst": Role.ANALYST,
    "consultant": Role.ANALYST,
    "research analyst": Role.ANALYST,
}


def map_designation_to_role(designation: str) -> str:
    """Map a designation string to a platform role."""
    return DESIGNATION_ROLE_MAP.get(designation.lower().strip(), Role.MEMBER)


def get_permissions_for_role(role: str) -> list[str]:
    """Return permission set for a given role."""
    return ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS[Role.MEMBER])


def require_permission(*required: str) -> Callable:
    """Dependency factory: raises 403 if user lacks any of the required permissions.

    Usage:
        @router.get("/", dependencies=[Depends(require_permission("view_analysis"))])
    """
    from fastapi import Depends as _Depends

    from backend.core.dependencies import TenantContext, get_tenant_context

    async def _check(ctx: TenantContext = _Depends(get_tenant_context)) -> None:
        missing = [p for p in required if p not in ctx.user.permissions]
        if missing:
            logger.warning(
                "permission_denied",
                user_id=ctx.user.user_id,
                required=list(required),
                missing=missing,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing)}",
            )

    return _check


def require_any_permission(*required: str) -> Callable:
    """Dependency factory: raises 403 if user has NONE of the required permissions."""
    from fastapi import Depends as _Depends

    from backend.core.dependencies import TenantContext, get_tenant_context

    async def _check(ctx: TenantContext = _Depends(get_tenant_context)) -> None:
        if not any(p in ctx.user.permissions for p in required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(required)}",
            )

    return _check
