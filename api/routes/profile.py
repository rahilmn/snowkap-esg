"""W2 — Profile-driven self-service onboarding.

Replaces the admin-only `/api/admin/onboard` flow for end users. Any signed-in
user can onboard their own company by typing the domain in their profile page.

Security model:

  * Auth via `require_auth()` only — no `manage_drip_campaigns` permission
    needed. The endpoint runs in the user's session.
  * Domain-match guard: the requested onboarding domain must match the
    caller's email domain (so `pilot@acme.com` can only onboard `acme.com`,
    not `bigcorp.com`). This is the ONLY gate — there is NO super-admin
    bypass (removed 2026-06-23): self-service onboarding is purely
    domain-driven. Snowkap onboards a prospect's own domain via
    `/api/admin/onboard` or `/api/onboard/v3` instead.
  * Reuses the existing `engine.jobs.onboard_queue.enqueue()` so the work
    runs in the same separate worker process the admin endpoint uses; the
    API event loop is never blocked.
  * Reuses `engine.models.onboarding_status` so the status-poll endpoint
    `/api/admin/onboard/{slug}/status` works for both admin and self-service
    onboards (no new poll endpoint).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import get_bearer_claims
from engine.jobs import onboard_queue
from engine.models import onboarding_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["profile"])

# Phase 56 — onboarding resilience knobs. A flaky/slow company-resolution LLM
# call used to wedge the whole onboard in 'pending' with no recourse; these
# bound it (fail-fast + retry) and back it with a stuck-job watchdog.
_RESOLVE_ATTEMPTS = max(1, int(os.environ.get("SNOWKAP_ONBOARD_RESOLVE_ATTEMPTS", "3") or 3))
_RESOLVE_TIMEOUT_S = float(os.environ.get("SNOWKAP_ONBOARD_RESOLVE_TIMEOUT_S", "45") or 45)
_ONBOARD_MAX_MINUTES = float(os.environ.get("SNOWKAP_ONBOARD_MAX_MINUTES", "15") or 15)


class MeOnboardRequest(BaseModel):
    """Self-service onboarding payload — domain only."""

    domain: str = Field(..., min_length=3, max_length=253)
    limit: int = Field(default=10, ge=1, le=20)


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")


def _normalise_domain(raw: str) -> str:
    """Lowercase + strip protocol/path. Returns "" if not a parseable domain."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # Strip http(s):// and any trailing path
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0]
    s = s.split("?", 1)[0]
    s = s.removeprefix("www.")
    return s if _DOMAIN_RE.match(s) else ""


def _email_domain(email: str | None) -> str:
    """Extract `acme.com` from `pilot@acme.com`."""
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def _domain_matches_caller(target: str, caller_email: str) -> bool:
    """True when `target` is the caller's own email domain (or a subdomain).

    Onboarding is purely domain-driven: a caller may onboard only their own
    company's domain. The super-admin bypass was removed 2026-06-23 — Snowkap
    onboards a prospect's domain via `/api/admin/onboard` or `/api/onboard/v3`.
    """
    own = _email_domain(caller_email)
    if not own:
        return False
    return target == own or target.endswith("." + own) or own.endswith("." + target)


@router.post("/onboard", status_code=202)
def me_onboard(
    body: MeOnboardRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Kick off self-service onboarding for the caller's company.

    Phase 47.M: this endpoint now uses the v3 synchronous pipeline
    (Phase 46.E) running in a FastAPI BackgroundTask instead of the
    legacy worker-queue path. The user still gets 202 immediately;
    the frontend polls /onboard/{slug}/status to see when it lands.

    The old worker-queue path was producing 0 articles for every
    company because (a) it used a yfinance-based onboarder that
    couldn't recognise companies like Maruti Suzuki ("industry=Other"),
    (b) cross-entity gate rejected 161/161 fetched articles, (c) result
    was empty deck + 404s on /now/feed.

    v3 fixes all three: LLM resolver identifies the company correctly
    + writes rich painpoints/KPIs, then runs the full pipeline with
    Stage 10-12 + lede on every article.
    """
    domain = _normalise_domain(body.domain)
    if not domain:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse '{body.domain}' as a domain (e.g. 'acme.com').",
        )

    caller_email = (claims.get("sub") or claims.get("email") or "").strip().lower()
    if not _domain_matches_caller(domain, caller_email):
        logger.warning(
            "me_onboard: domain mismatch -- caller=%s tried to onboard domain=%s",
            caller_email or "<anon>", domain,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"You can only onboard your own company's domain. "
                f"Your email is on '{_email_domain(caller_email) or 'unknown'}', "
                f"but you requested '{domain}'."
            ),
        )

    # Lazy import the slugify helper for the placeholder slug.
    from engine.ingestion.company_onboarder import _domain_to_search_term, _slugify

    seed = _domain_to_search_term(domain) or domain
    expected_slug = _slugify(seed)

    # Pre-seed the status row so the frontend's polling never races. mark_kickoff
    # (not upsert) RESETS started_at so the watchdog clock measures THIS attempt.
    onboarding_status.mark_kickoff(expected_slug)

    # Run v3 in the background so the user gets 202 immediately.
    background_tasks.add_task(
        _run_v3_for_me_onboard,
        domain=domain,
        expected_slug=expected_slug,
        item_limit=int(body.limit),
        caller_email=caller_email,
    )

    logger.info(
        "me_onboard: started v3 onboard slug=%s domain=%s requested_by=%s",
        expected_slug, domain, caller_email or "<anon>",
    )

    return {
        "status": "queued",
        "slug": expected_slug,
        "domain": domain,
        "poll_url": f"/api/me/onboard/{expected_slug}/status",
    }


def _resolve_company_with_retry(domain: str, expected_slug: str):
    """Phase 56 — resolve the company with a hard per-attempt wall-clock cap +
    retries, so a flaky/slow resolver call self-heals instead of wedging the
    onboard in 'pending' (observed live: an onboard stuck pending ~20 min).

    Each attempt runs in a worker thread capped at ``_RESOLVE_TIMEOUT_S`` (a
    wall-clock bound that holds even if the LLM SDK ignores its own timeout);
    we retry up to ``_RESOLVE_ATTEMPTS`` times. On exhaustion we stamp
    ``state='failed'`` with a clear, retryable error and return ``None``.
    """
    import concurrent.futures

    from engine.ingestion.llm_company_resolver import resolve_company_from_domain

    last_err = "unknown error"
    for attempt in range(1, _RESOLVE_ATTEMPTS + 1):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(
                resolve_company_from_domain, domain, timeout=_RESOLVE_TIMEOUT_S,
            )
            info = fut.result(timeout=_RESOLVE_TIMEOUT_S + 15)
            if info is not None:
                if attempt > 1:
                    logger.info(
                        "_run_v3_for_me_onboard: resolve OK on attempt %d for %s",
                        attempt, domain,
                    )
                return info
            last_err = "resolver could not identify the company"
        except concurrent.futures.TimeoutError:
            last_err = f"resolve timed out (>{int(_RESOLVE_TIMEOUT_S)}s)"
            logger.warning(
                "_run_v3_for_me_onboard: resolve attempt %d/%d timed out for %s",
                attempt, _RESOLVE_ATTEMPTS, domain,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {str(exc)[:150]}"
            logger.warning(
                "_run_v3_for_me_onboard: resolve attempt %d/%d failed for %s: %s",
                attempt, _RESOLVE_ATTEMPTS, domain, last_err,
            )
        finally:
            # Don't block on a hung worker thread — the resolver's own timeout
            # frees it shortly; waiting here would re-introduce the hang.
            pool.shutdown(wait=False)

    onboarding_status.upsert(
        expected_slug, state="failed",
        error=(f"Couldn't identify your company after {_RESOLVE_ATTEMPTS} tries "
               f"({last_err}). Please retry.")[:480],
    )
    logger.warning(
        "_run_v3_for_me_onboard: resolve exhausted for %s -> failed (%s)",
        domain, last_err,
    )
    return None


def _run_v3_for_me_onboard(
    domain: str, expected_slug: str, item_limit: int, caller_email: str,
) -> None:
    """Background task that runs the Phase 46.E v3 pipeline.

    Updates onboarding_status as it progresses so the frontend's
    polling can pick up the canonical slug + ready state.

    Errors here are caught + logged (background tasks have no caller
    to receive the exception) but also stamped on the status row so
    the frontend's polling can show a meaningful failure UX.
    """
    try:
        # Resolve company via LLM — fail-fast + auto-retry (Phase 56) so a
        # flaky/slow resolver call self-heals instead of wedging the onboard
        # in 'pending' forever.
        info = _resolve_company_with_retry(domain, expected_slug)
        if info is None:
            return  # already stamped state='failed' with a retryable error

        # Mark fetching
        onboarding_status.upsert(expected_slug, state="fetching")

        # Upsert companies row + slug alias
        from engine.models import companies_store
        from engine.config import invalidate_companies_cache, Company
        from engine.ingestion.news_fetcher import fetch_for_company

        # Phase 56.B — opt every onboard into the FULL multi-lane ESG fetch.
        # Force the ESG-material body-fetch + the industry-thematic (sector ESG)
        # lane ON (they otherwise run only on the budget-gated "auto" default),
        # so a fresh tenant gets the same rich coverage as the 7 canonical
        # companies instead of a thin, market-news-only deck.
        _calibration = {
            "inferred_painpoints": info.inferred_painpoints,
            "inferred_kpis": info.inferred_kpis,
            "default_reader_role": info.default_reader_role,
            "esg_second_fetch": "on",
            "industry_thematic_fetch": "on",
        }

        companies_store.upsert(
            slug=info.slug, name=info.canonical_name, domain=domain,
            industry=info.industry, market_cap_tier=info.market_cap_tier,
            yfinance_ticker=info.primary_ticker,
            framework_region=info.framework_region,
            primitive_calibration=_calibration,
            created_by_user=caller_email or None, status="active",
        )
        invalidate_companies_cache()

        if expected_slug != info.slug:
            try:
                from engine.index import sqlite_index
                sqlite_index.register_alias(expected_slug, info.slug)
            except Exception:
                pass

        # Build Company dataclass + fetch news
        company_obj = Company(
            name=info.canonical_name, slug=info.slug, domain=domain,
            industry=info.industry, sasb_category=info.sasb_category,
            market_cap=info.market_cap_tier,
            listing_exchange="NSE/BSE",  # rough — v3 has a smarter resolver but not critical here
            headquarter_city=info.headquarter_city or "Unknown",
            headquarter_country=info.headquarter_country or "",
            headquarter_region=info.framework_region,
            news_queries=[
                f"{info.canonical_name} ESG",
                f"{info.canonical_name} sustainability",
                f"{info.canonical_name} regulatory",
                f"{info.canonical_name} disclosure",
            ],
            primitive_calibration=_calibration,
            yfinance_ticker=info.primary_ticker, eodhd_ticker=None,
            framework_region=info.framework_region,
            sustainability_query=None, general_query=None,
        )

        # Phase 56.B — inherit the default fetch depth (settings
        # `max_articles_per_company_per_run` = 18) instead of the old 3-cap, so
        # onboards pull ~6x more articles per lane (matching canonical companies).
        fresh = fetch_for_company(company_obj)
        logger.info(
            "_run_v3_for_me_onboard: fetched %d articles for %s",
            len(fresh), info.slug,
        )

        # Mark analysing
        onboarding_status.upsert(info.slug, state="analysing")
        # Also write under expected_slug so the frontend's poll still resolves
        if expected_slug != info.slug:
            onboarding_status.upsert(expected_slug, state="analysing")

        # Run the full pipeline on top-N in parallel (uses Phase 47.I lock)
        import concurrent.futures
        from api.routes.onboard_v3 import _run_full_pipeline_for_article

        # Phase 56.B — RANK the (now larger, multi-lane) fetched set by
        # materiality x relevance x criticality and analyse the best `item_limit`.
        # Without this, fresh[:item_limit] takes the first articles in FETCH order
        # (company-named market news, fetched first), crowding out the valuable
        # ESG-material + thematic (sector) articles that arrive later — defeating
        # the depth/lane win. Free Stage-4 scoring only (no LLM). Falls back to
        # fetch order if the selector ever chokes, so ranking can't break onboard.
        try:
            from engine.analysis.article_selector import select_top_n_for_pipeline
            top = select_top_n_for_pipeline(
                fresh, n=item_limit,
                company_slug=info.slug, primary_industry=info.industry,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_run_v3_for_me_onboard: selector fell back to fetch order: %s", exc,
            )
            top = fresh[:item_limit]

        def _safe_run(article):
            try:
                ad = {
                    "id": article.id, "title": article.title,
                    "content": article.content, "summary": article.summary,
                    "source": article.source, "url": article.url,
                    "published_at": article.published_at, "metadata": article.metadata,
                }
                return _run_full_pipeline_for_article(ad, company_obj)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_run_v3_for_me_onboard: pipeline crash for %s: %s",
                    getattr(article, "id", "?"), exc,
                )
                return exc

        analysed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_safe_run, a) for a in top]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    outcome = fut.result(timeout=120)
                    if not isinstance(outcome, Exception) and not outcome.get("rejected"):
                        analysed += 1
                except Exception:
                    pass

        # Mark ready under BOTH slugs so frontend polling works regardless
        onboarding_status.upsert(info.slug, state="ready")
        if expected_slug != info.slug:
            onboarding_status.upsert(expected_slug, state="ready")

        logger.info(
            "_run_v3_for_me_onboard: done %s -> %s, analysed=%d/%d",
            expected_slug, info.slug, analysed, len(top),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("_run_v3_for_me_onboard crashed: %s", exc)
        try:
            onboarding_status.upsert(
                expected_slug, state="failed",
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        except Exception:
            pass


@router.get("/onboard/{slug}/status")
def me_onboard_status(
    slug: str,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Tenant-scoped onboarding status poll.

    The admin counterpart at /api/admin/onboard/{slug}/status is gated
    by manage_drip_campaigns (super-admin only). Regular self-service
    users need a path that lets them poll THEIR OWN onboarding progress
    without that permission — that's this endpoint.

    Scope rule: the requested slug must match the caller's JWT-bound
    `company_id` (after alias resolution). A regular user can never
    poll another tenant's onboarding state via this path.
    """
    if not slug:
        raise HTTPException(status_code=422, detail="slug required")

    caller_slug = (claims.get("company_id") or "").strip()
    if not caller_slug:
        raise HTTPException(status_code=403, detail="No tenant context on token")

    # Resolve both the caller's slug + the requested slug to canonical
    # form so alias-vs-canonical mismatches still match (e.g. caller's
    # JWT has 'nestle' but the worker canonicalised to 'nestl-india-
    # limited'). Both paths route through resolve_slug.
    try:
        from engine.index.sqlite_index import resolve_slug
    except Exception:  # noqa: BLE001
        resolve_slug = lambda s: s  # noqa: E731

    caller_canonical = resolve_slug(caller_slug) or caller_slug
    target_canonical = resolve_slug(slug) or slug

    super_admin = "super_admin" in (claims.get("permissions") or [])
    if not super_admin and caller_canonical != target_canonical and caller_slug != slug:
        raise HTTPException(
            status_code=403,
            detail="You can only poll your own company's onboarding status.",
        )

    # Try the requested slug first; fall back to canonical if alias has
    # no row of its own yet (worker writes status against the canonical
    # slug, mirrors to alias at mark_ready time).
    # Phase 56 — stuck-job watchdog: flip any onboard wedged in a non-terminal
    # state past the budget to 'failed' on read, so the UI shows an error +
    # retry instead of an indefinite spinner.
    status = (
        onboarding_status.expire_if_stale(slug, max_minutes=_ONBOARD_MAX_MINUTES)
        or onboarding_status.expire_if_stale(target_canonical, max_minutes=_ONBOARD_MAX_MINUTES)
    )
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"No onboarding record for '{slug}' yet.",
        )
    return status.to_dict()


# ---------------------------------------------------------------------------
# Phase 6 — persona MCQ + upsert
# ---------------------------------------------------------------------------


class PersonaUpsertRequest(BaseModel):
    """MCQ submission. Every field is optional; missing fields keep the
    persona's existing value (or the role-default if no persona exists).
    """
    role: str | None = Field(default=None)
    esg_focus: list[str] | None = Field(default=None)
    frameworks: list[str] | None = Field(default=None)
    geographies: list[str] | None = Field(default=None)
    horizon: str | None = Field(default=None)
    decision_style: str | None = Field(default=None)
    risk_appetite: str | None = Field(default=None)


def _caller_user_id(claims: dict[str, Any] | None) -> str:
    if not isinstance(claims, dict):
        return ""
    sub = claims.get("sub") or claims.get("email") or ""
    return str(sub).strip().lower()


@router.get("/persona/questions")
def persona_questions() -> dict[str, Any]:
    """Phase 6 §8.2 — return the 6-question MCQ schema (static).

    Frontend renders this as a wizard. Field IDs match the keys on
    PersonaUpsertRequest so the response shape is round-trippable.
    No auth required: the schema is non-sensitive product copy.
    """
    from engine.persona import PERSONA_QUESTIONS
    return {"questions": PERSONA_QUESTIONS}


@router.get("/persona")
def get_my_persona(
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Return the caller's stored persona, or a role-default fall-back
    when none has been saved yet.

    Includes a `mcq_completed` flag so the UI can show the "complete your
    profile" banner only when the user actually skipped the MCQ.
    """
    from engine.persona import default_persona_for_role, get_persona

    user_id = _caller_user_id(claims)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing user identity")

    stored = get_persona(user_id)
    if stored is not None:
        return {"persona": stored.to_dict(), "mcq_completed": True}

    # Fall back to neutral default keyed by role hint (none in the JWT today
    # → "other" yields a safe-but-empty persona)
    role_hint = "other"
    default = default_persona_for_role(user_id, role_hint)
    return {"persona": default.to_dict(), "mcq_completed": False}


@router.put("/persona")
def upsert_my_persona(
    body: PersonaUpsertRequest,
    _: None = Depends(require_auth),
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Upsert the caller's persona from MCQ answers.

    - Caps multi-select fields at 3 entries (per plan §8.2)
    - Validates enum values via deserialise_persona (filters invalid
      tokens silently rather than 422-ing on a bad option label)
    - Bumps last_edited_at on every save
    - Returns the persona as stored
    """
    from engine.persona import (
        default_persona_for_role,
        deserialise_persona,
        get_persona,
        upsert_persona,
    )

    user_id = _caller_user_id(claims)
    if not user_id:
        raise HTTPException(status_code=401, detail="missing user identity")

    # Start from existing persona so partial updates don't blow away
    # untouched fields. Fall back to role-default for first-time saves.
    existing = get_persona(user_id) or default_persona_for_role(
        user_id, body.role or "other",
    )
    base = existing.to_dict()

    # Apply only the keys the caller actually sent — None means "leave alone"
    overrides: dict[str, Any] = {}
    for field_name in (
        "role", "esg_focus", "frameworks", "geographies",
        "horizon", "decision_style", "risk_appetite",
    ):
        v = getattr(body, field_name, None)
        if v is None:
            continue
        if isinstance(v, list):
            # Cap multi-select to 3 entries (plan §8.2)
            v = list(v)[:3]
        overrides[field_name] = v

    merged = {**base, **overrides, "user_id": user_id}
    persona = deserialise_persona(merged)
    saved = upsert_persona(persona)
    return {"persona": saved.to_dict(), "mcq_completed": True}
