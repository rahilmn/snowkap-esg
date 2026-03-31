"""User preference CRUD for feed personalization.

Phase 2D: GET/PUT/PATCH endpoints for user feed preferences.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.user_preference import UserPreference

router = APIRouter()


class PreferenceResponse(BaseModel):
    preferred_frameworks: list[str] = []
    preferred_pillars: list[str] = []
    preferred_topics: list[str] = []
    alert_threshold: int = 70
    content_depth: str = "standard"
    companies_of_interest: list[str] = []
    dismissed_topics: list[str] = []


class PreferenceUpdate(BaseModel):
    # BUG-15: Bounded list sizes to prevent unbounded storage
    preferred_frameworks: list[str] | None = Field(default=None, max_length=50)
    preferred_pillars: list[str] | None = Field(default=None, max_length=10)
    preferred_topics: list[str] | None = Field(default=None, max_length=100)
    alert_threshold: int | None = None
    content_depth: str | None = None
    companies_of_interest: list[str] | None = None
    dismissed_topics: list[str] | None = Field(default=None, max_length=200)


@router.get("", response_model=PreferenceResponse)
@router.get("/", response_model=PreferenceResponse, include_in_schema=False)
async def get_preferences(
    ctx: TenantContext = Depends(get_tenant_context),
) -> PreferenceResponse:
    """Get current user's preferences, or defaults if none set."""
    result = await ctx.db.execute(
        select(UserPreference).where(
            UserPreference.user_id == ctx.user.user_id,
            UserPreference.tenant_id == ctx.tenant_id,
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        # Return role-based defaults
        from backend.core.permissions import map_designation_to_role
        from backend.services.role_curation import get_role_profile

        role = map_designation_to_role(ctx.user.designation or "")
        profile = get_role_profile(role)
        return PreferenceResponse(
            preferred_frameworks=profile.get("priority_frameworks", []),
            content_depth=profile.get("content_depth", "standard"),
            alert_threshold=profile.get("alert_threshold", 70),
        )

    return PreferenceResponse(
        preferred_frameworks=pref.preferred_frameworks or [],
        preferred_pillars=pref.preferred_pillars or [],
        preferred_topics=pref.preferred_topics or [],
        alert_threshold=pref.alert_threshold or 70,
        content_depth=pref.content_depth or "standard",
        companies_of_interest=pref.companies_of_interest or [],
        dismissed_topics=pref.dismissed_topics or [],
    )


@router.put("", response_model=PreferenceResponse)
@router.put("/", response_model=PreferenceResponse, include_in_schema=False)
async def upsert_preferences(
    body: PreferenceUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> PreferenceResponse:
    """Create or fully update user preferences."""
    result = await ctx.db.execute(
        select(UserPreference).where(
            UserPreference.user_id == ctx.user.user_id,
            UserPreference.tenant_id == ctx.tenant_id,
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        pref = UserPreference(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user.user_id,
        )
        ctx.db.add(pref)

    if body.preferred_frameworks is not None:
        pref.preferred_frameworks = body.preferred_frameworks
    if body.preferred_pillars is not None:
        pref.preferred_pillars = body.preferred_pillars
    if body.preferred_topics is not None:
        pref.preferred_topics = body.preferred_topics
    if body.alert_threshold is not None:
        pref.alert_threshold = body.alert_threshold
    if body.content_depth is not None:
        pref.content_depth = body.content_depth
    if body.companies_of_interest is not None:
        pref.companies_of_interest = body.companies_of_interest
    if body.dismissed_topics is not None:
        pref.dismissed_topics = body.dismissed_topics

    await ctx.db.flush()

    return PreferenceResponse(
        preferred_frameworks=pref.preferred_frameworks or [],
        preferred_pillars=pref.preferred_pillars or [],
        preferred_topics=pref.preferred_topics or [],
        alert_threshold=pref.alert_threshold or 70,
        content_depth=pref.content_depth or "standard",
        companies_of_interest=pref.companies_of_interest or [],
        dismissed_topics=pref.dismissed_topics or [],
    )


@router.patch("/", response_model=PreferenceResponse)
async def patch_preferences(
    body: PreferenceUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> PreferenceResponse:
    """Partially update user preferences (same logic as PUT)."""
    return await upsert_preferences(body, ctx)
