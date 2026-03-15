"""FastAPI dependencies: TenantContext, current_user.

Per CLAUDE.md:
- TenantContext dependency injected into every route
- tenant_id filter on every SELECT/INSERT
- NEVER return data from Tenant A to Tenant B
"""

from dataclasses import dataclass

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.security import decode_jwt_token

logger = structlog.get_logger()
security_scheme = HTTPBearer()


@dataclass
class CurrentUser:
    """Authenticated user context extracted from JWT."""
    user_id: str
    tenant_id: str
    company_id: str
    designation: str
    permissions: list[str]
    domain: str


@dataclass
class TenantContext:
    """Tenant-scoped context injected into every route.

    Every database query MUST use this tenant_id to filter results.
    """
    tenant_id: str
    user: CurrentUser
    db: AsyncSession


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> CurrentUser:
    """Extract and validate user from JWT Bearer token."""
    try:
        payload = decode_jwt_token(credentials.credentials)
    except JWTError as e:
        logger.warning("jwt_validation_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        company_id=payload["company_id"],
        designation=payload["designation"],
        permissions=payload.get("permissions", []),
        domain=payload["domain"],
    )


async def get_tenant_context(
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """Build tenant-scoped context for route handlers.

    Per CLAUDE.md Rule #1: every query MUST filter by tenant_id.
    structlog is bound with tenant_id per Rule #7.
    """
    structlog.contextvars.bind_contextvars(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )
    return TenantContext(
        tenant_id=user.tenant_id,
        user=user,
        db=db,
    )
