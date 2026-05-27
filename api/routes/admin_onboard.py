"""Phase 11B — Admin onboarding endpoint.

Fixes the "any company in 5 minutes" promise that Phase 10 broke: new
tenants were appearing in the super-admin's switcher as empty shells
because `tenant_registry` auto-registered domains on login but nothing
ever triggered the first pipeline pass.

New flow:

  1. Super-admin POSTs `/api/admin/onboard {name, ticker_hint?, domain?}`.
  2. Endpoint calls [engine.ingestion.company_onboarder.onboard_company]
     (existing Phase 8 flow — yfinance ticker resolution + financials +
     industry + news queries written to companies.json).
  3. On success, schedules a BackgroundTask that:
     - Calls `fetch_for_company(slug, limit=~10)` to pull ESG-filtered
       articles via NewsAPI.ai + Google News RSS.
     - Runs each article through the 12-stage pipeline (reuses
       [engine.main._run_article]).
     - Writes insight + perspectives + recommendations JSONs to
       `data/outputs/<slug>/...` and indexes them in SQLite.
  4. `onboarding_status` table tracks progress so the frontend modal can
     poll GET `/api/admin/onboard/{slug}/status` every 5s.

Gated by `manage_drip_campaigns` (super-admin only). Phase 23B — auto-detects
listings across NSE/BSE/NYSE/NASDAQ/LSE/Xetra/Euronext/HKEX and applies the
right framework region (INDIA / EU / US / UK / APAC / GLOBAL) so news
queries + mandatory frameworks match the company's home jurisdiction.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.auth_context import require_bearer_permission
from engine.jobs import onboard_queue
from engine.models import onboarding_status

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin",
    tags=["admin-onboard"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


class OnboardRequest(BaseModel):
    """Phase 16 — at least one of name / ticker_hint / domain must be set.
    Pre-Phase-16 the schema required `name`. Now domain-only entry is the
    fastest happy path (e.g. POST {"domain": "tatachemicals.com"}).
    """
    name: str | None = Field(default=None, max_length=100)
    ticker_hint: str | None = None
    domain: str | None = None
    limit: int = Field(default=10, ge=1, le=50)


def enqueue_onboarding(
    *,
    slug: str,
    name: str | None,
    ticker_hint: str | None,
    domain: str | None,
    limit: int,
) -> int:
    """Append an onboarding job to the SQLite queue drained by
    ``scripts/onboarding_worker.py``.

    This replaced ``BackgroundTasks.add_task(_background_onboard, ...)``
    so the API event loop is never on the hook for a slow yfinance
    lookup, NewsAPI rate-limit retry, or 12-stage LLM pipeline. The
    queue lives in the same SQLite DB as ``onboarding_status``, and
    the existing ``claim_pending`` / ``force_claim_pending`` checks on
    that table continue to gate against duplicate enqueues.

    Returns the new queue row id (handy for logging / future tracing).
    Falls back to a logged warning + still returns 0 if the enqueue
    write itself fails — the API request must never fail because of a
    queue error; the user can retry from the empty-Home state.
    """
    try:
        return onboard_queue.enqueue(
            slug=slug,
            name=name,
            ticker_hint=ticker_hint,
            domain=domain,
            item_limit=int(limit),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to enqueue onboarding job for slug=%s: %s", slug, exc)
        return 0


def _background_onboard(slug: str, name: str | None, ticker_hint: str | None, domain: str | None, limit: int) -> None:
    """Body of the onboarding pipeline.

    Pre-Phase-23: this was registered as a FastAPI ``BackgroundTask``
    so it ran inside the API process. That blocked event-loop threads
    on slow third-party calls. It now runs ONLY inside
    ``scripts/onboarding_worker.py`` (a separate Replit workflow); the
    API enqueues via :func:`enqueue_onboarding` and never invokes this
    function inline. The signature is preserved so the worker, the
    direct-call tests in ``tests/test_phase22_2_mirror_and_counter.py``,
    and any operational backfill scripts continue to work unchanged.

    ``slug`` is already computed by the HTTP handler and the row is
    already seeded (state=pending). All state changes happen on this
    same slug. Exceptions are written to ``onboarding_status.error``
    rather than raised.

    Phase 28 — emits progress events through ``onboarding_events`` at
    each stage transition so the SSE endpoint
    ``GET /api/me/onboard/{slug}/stream`` can stream skeleton-fill UI
    updates to the user.
    """
    # Lazy imports keep the HTTP request fast when the task is queued.
    from engine.config import load_companies
    from engine.index import tenant_registry
    from engine.ingestion.company_onboarder import onboard_company
    from engine.ingestion.news_fetcher import fetch_for_company
    from engine.main import _run_article
    from engine.models import onboarding_events

    onboarding_events.emit_event(slug, "onboard_started", {"slug": slug, "domain": domain})

    # Phase 30 — per-tenant LLM daily cap. Fail closed BEFORE we start
    # the expensive pipeline so a single tenant in a retry loop can't
    # burn the day's budget. Cheap check (one indexed SQL aggregate).
    # The user sees a clean "Daily limit reached" status instead of a
    # half-finished onboard.
    try:
        from engine.llm.budget import assert_under_cap, TenantBudgetExceeded
        assert_under_cap(slug)
    except TenantBudgetExceeded as exc:
        msg = (
            f"Daily LLM cap reached for this tenant: spent ${exc.spent:.2f} "
            f"of ${exc.cap:.2f} cap. Try again tomorrow or raise "
            f"SNOWKAP_PER_TENANT_DAILY_CAP_USD."
        )
        logger.warning("[onboard %s] %s", slug, msg)
        onboarding_status.mark_failed(slug, msg)
        onboarding_events.emit_event(slug, "onboard_failed", {
            "slug": slug,
            "error": "daily_cap_reached",
            "spent_usd": exc.spent,
            "cap_usd": exc.cap,
        })
        return

    try:
        # Stage 1: company_onboarder resolves ticker + writes companies.json
        onboarding_status.upsert(slug, state="fetching")
        result = onboard_company(
            company_name=name,
            ticker_hint=ticker_hint,
            domain=domain,
        )
        if result is None:
            onboarding_status.mark_failed(
                slug,
                "could not resolve ticker — pass an explicit ticker_hint "
                "(e.g. 'TATACHEM.NS' for NSE, 'AAPL' for NASDAQ, 'SAP.DE' "
                "for Xetra, 'BARC.L' for LSE).",
            )
            onboarding_events.emit_event(slug, "onboard_failed", {
                "slug": slug,
                "error": "ticker_resolution_failed",
            })
            return

        onboarding_events.emit_event(slug, "company_profile_ready", {
            "slug": result.slug,
            "name": result.name,
            "industry": result.industry,
            "region": getattr(result, "framework_region", None) or "GLOBAL",
        })

        # Sanity: the onboarder may have derived a slightly different slug
        # (e.g. strips "Limited" / "Ltd", or expands "tatachemicals" →
        # "tata-chemicals-limited"). Use its authoritative value going
        # forward — and migrate the status row if they differ. Note: we
        # do NOT mark the alias as ready here; we wait until the canonical
        # finishes and then mirror its final stats so the UI shows the
        # right counts (Phase 21 fix).
        canonical_slug = result.slug
        alias_slug = slug if slug != canonical_slug else None
        if alias_slug:
            logger.info("[onboard] slug adjusted: requested=%s actual=%s",
                        alias_slug, canonical_slug)
            # Mirror the canonical's "fetching" state to keep the alias-poll
            # progress smooth; clears any stale error from a prior failed run.
            onboarding_status.upsert(alias_slug, state="fetching")
            onboarding_status.upsert(canonical_slug, state="fetching")

            # Phase 42 fix (2026-05-27) — register the alias mapping
            # IMMEDIATELY, not at the end of the analysis loop. Otherwise
            # the user navigates to /now?company={typed_slug} during the
            # 3-6 minute analysis window and gets 404 because
            # resolve_slug("lululemon") returns "lululemon" (no alias),
            # then get_company("lululemon") raises KeyError (canonical is
            # "lululemon-athletica-inc"). Registering here means the alias
            # resolves cleanly from the moment company_onboarder writes
            # the canonical to companies.json. The deck starts empty +
            # fills as analysis completes (each pipeline write upserts
            # an article_pool + company_article_view row).
            try:
                from engine.index import sqlite_index
                sqlite_index.register_alias(alias_slug, canonical_slug)
                logger.info(
                    "[onboard %s] early alias registered: %s → %s",
                    canonical_slug, alias_slug, canonical_slug,
                )
            except Exception as exc:  # noqa: BLE001 — alias registration is
                # additive; failure should never block the onboard. The
                # later mirror_to_slug at the end of the analysis loop
                # acts as a safety net.
                logger.warning(
                    "[onboard %s] early alias registration failed (non-fatal): %s",
                    canonical_slug, exc,
                )

        # Register in tenant_registry so the super-admin switcher picks it up.
        tenant_registry.register_tenant(
            domain=(domain or result.slug + ".example.com"),
            name=result.name,
            industry=result.industry,
            source="onboarded",
        )

        # Stage 2: fetch ESG-filtered articles
        company_obj = next((c for c in load_companies() if c.slug == canonical_slug), None)
        if company_obj is None:
            onboarding_status.mark_failed(canonical_slug, "companies.json write succeeded but reload failed")
            return

        onboarding_events.emit_event(canonical_slug, "news_fetch_started", {"slug": canonical_slug})
        fresh = fetch_for_company(company_obj, max_per_query=3)
        onboarding_status.upsert(canonical_slug, state="analysing", fetched=len(fresh))
        logger.info("[onboard %s] fetched %d articles", canonical_slug, len(fresh))
        onboarding_events.emit_event(canonical_slug, "news_fetch_done", {
            "slug": canonical_slug, "n_articles": len(fresh),
        })

        # Phase 36 — Onboarding-time body-capture guarantee.
        #
        # `fetch_for_company` runs an inline body backfill but enforces a
        # per-company 45s budget across ALL fetched articles. For new
        # tenants with many queries, the budget can run out before the
        # top candidates are bodied → top-3 critical selection then runs
        # on headline-only articles → user's first impression of the
        # product is the safety-net cap output instead of body-grounded
        # analysis.
        #
        # This second pass focuses on the TOP 5 candidates only (the
        # top-3 critical + 2 buffer) and bypasses the per-company budget.
        # Hard 60s cap so the onboarding flow can't hang indefinitely.
        # Emits its own SSE event so the frontend progress strip can show
        # "Pulling full article text… N of M bodies captured".
        try:
            from engine.ingestion.full_text_extractor import extract_full_text
            import time as _time
            ftc_deadline = _time.monotonic() + 60.0
            top_candidates = fresh[:5]  # top-3 + 2 buffer
            ftc_checked = 0
            ftc_added = 0
            ftc_paywalled = 0
            ftc_already_grounded = 0
            for art in top_candidates:
                if _time.monotonic() > ftc_deadline:
                    logger.info(
                        "[onboard %s] full-text guarantee: 60s budget hit", canonical_slug,
                    )
                    break
                ftc_checked += 1
                existing = (art.content or "").strip()
                title = (art.title or "").strip()
                # Already body-grounded? skip
                if len(existing) >= 300 and existing != title and len(existing) > len(title) + 50:
                    ftc_already_grounded += 1
                    continue
                if not art.url:
                    continue
                try:
                    # use_cache=False to defeat any stale failure cached
                    # earlier in the same boot cycle by fetch_for_company.
                    result_ft = extract_full_text(art.url, use_cache=False, timeout=12.0)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "[onboard %s] guarantee extract failed for %s: %s",
                        canonical_slug, art.id, exc,
                    )
                    ftc_paywalled += 1
                    continue
                if result_ft is None or not result_ft.body:
                    ftc_paywalled += 1
                    continue
                # Mutate the IngestedArticle dataclass in place
                art.content = result_ft.body
                art.summary = result_ft.body[:500]
                meta = dict(art.metadata or {})
                meta["full_text_source"] = "publisher_scrape_onboarding_guarantee"
                meta["full_text_char_count"] = result_ft.char_count
                meta["publisher_url"] = result_ft.publisher_url
                art.metadata = meta
                # Phase 36 fix — write the mutated article BACK TO DISK so the
                # body-coverage stats reflect reality AND on-demand re-enrich
                # reads the body instead of the old title-only file. Without
                # this, Stage 10 saw the body (because we pass the in-memory
                # dataclass to the pipeline) but the body-coverage metric +
                # the lazy-backfill path in on_demand both saw 0% coverage.
                try:
                    from engine.ingestion.news_fetcher import _write_article
                    _write_article(art)
                except Exception as exc:  # noqa: BLE001 — persist is additive
                    logger.warning(
                        "[onboard %s] couldn't persist backfilled body for %s: %s",
                        canonical_slug, art.id, exc,
                    )
                ftc_added += 1
            onboarding_events.emit_event(canonical_slug, "full_text_capture_done", {
                "slug": canonical_slug,
                "candidates_checked": ftc_checked,
                "bodies_added": ftc_added,
                "already_grounded": ftc_already_grounded,
                "paywalled_skipped": ftc_paywalled,
            })
            logger.info(
                "[onboard %s] full-text guarantee: %d/%d candidates body-grounded "
                "(+%d added, %d already, %d paywalled)",
                canonical_slug, ftc_added + ftc_already_grounded, ftc_checked,
                ftc_added, ftc_already_grounded, ftc_paywalled,
            )
        except Exception as exc:  # noqa: BLE001 — guarantee is additive
            logger.warning(
                "[onboard %s] full-text guarantee step failed (non-fatal): %s",
                canonical_slug, exc,
            )

        # Stage 3: run each article through the full 12-stage pipeline.
        # Phase 22.1 — only count NON-rejected articles in `analysed` so
        # the dashboard's "fetched/analysed" stats match the indexed
        # rows the user will actually see. Pre-fix: a German prospect
        # whose 2 articles were both relevance-rejected showed
        # "ready 2/2 analysed" but the feed was empty.
        # Phase 28 — surface the critical-3 selection once so the
        # frontend skeleton can lock to exactly three article IDs.
        # ``fresh`` is already pre-ranked by ``select_top_n_for_pipeline``
        # upstream; the first three are the critical surface.
        critical_three = [a.id for a in fresh[:3]]
        if critical_three:
            onboarding_events.emit_event(canonical_slug, "critical_3_selected", {
                "slug": canonical_slug,
                "article_ids": critical_three,
            })

        attempted = 0
        analysed = 0
        home_count = 0
        total_to_analyse = min(limit, len(fresh))
        for article in fresh:
            if attempted >= limit:
                break
            attempted += 1
            onboarding_events.emit_event(canonical_slug, "analysis_started", {
                "article_id": article.id,
                "position": attempted,
                "total": total_to_analyse,
            })
            article_dict = {
                "id": article.id,
                "title": article.title,
                "content": article.content,
                "summary": article.summary,
                "source": article.source,
                "url": article.url,
                "published_at": article.published_at,
                "metadata": article.metadata,
            }
            try:
                summary = _run_article(article_dict, company_obj)
                if not summary.rejected:
                    analysed += 1
                    if summary.tier == "HOME":
                        home_count += 1
                onboarding_status.upsert(canonical_slug, analysed=analysed, home_count=home_count)
                onboarding_events.emit_event(canonical_slug, "analysis_done", {
                    "article_id": article.id,
                    "headline": (article.title or "")[:200],
                    "criticality_band": getattr(summary, "tier", "UNKNOWN"),
                    "position": attempted,
                    "total": total_to_analyse,
                })
            except Exception as exc:
                logger.exception("[onboard %s] article %s failed: %s", canonical_slug, article.id, exc)
                onboarding_events.emit_event(canonical_slug, "analysis_done", {
                    "article_id": article.id,
                    "headline": (article.title or "")[:200],
                    "criticality_band": "FAILED",
                    "position": attempted,
                    "total": total_to_analyse,
                })
                # Continue with the rest — one bad article shouldn't fail the whole onboarding.
                continue

        # Phase 36 — Eager Stage 10 on the top-3 by criticality, regardless
        # of tier. Without this, new tenants whose articles all score
        # SECONDARY/MEDIUM (most non-baseline companies on a normal week)
        # land with ZERO analysis — every "View Insights →" click triggers
        # a 45-60s on-demand re-enrichment. By running top-3 Stage 10 here
        # we guarantee body-grounded insight + Stage 12 recs are READY in
        # the article sheet the moment the user swipes up.
        #
        # Cost: 3 × ~$0.05 = ~$0.15 LLM per onboard. Acceptable. The HOME-
        # only gate in engine.main._run_article still applies at the
        # nightly ingestion path — this is an onboarding-only one-shot.
        try:
            from engine.db import connect as _db_connect
            with _db_connect() as _c:
                _rows = _c.execute(
                    "SELECT id FROM article_index WHERE company_slug = ? "
                    "ORDER BY criticality_score DESC, published_at DESC LIMIT 3",
                    (canonical_slug,),
                ).fetchall()
            top3_ids = [r[0] for r in _rows]
            if top3_ids:
                from engine.analysis.on_demand import enrich_on_demand
                logger.info(
                    "[onboard %s] eager Stage 10 on top-3 by criticality: %s",
                    canonical_slug, top3_ids,
                )
                for aid in top3_ids:
                    try:
                        enrich_on_demand(aid, canonical_slug, force=True)
                        onboarding_events.emit_event(canonical_slug, "analysis_done", {
                            "article_id": aid,
                            "headline": "(eager Stage 10)",
                            "criticality_band": "EAGER",
                            "position": 0, "total": 0,
                        })
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[onboard %s] eager Stage 10 failed for %s: %s",
                            canonical_slug, aid, exc,
                        )
        except Exception as exc:  # noqa: BLE001 — eager pass is additive
            logger.warning(
                "[onboard %s] eager-Stage-10 pass failed (non-fatal): %s",
                canonical_slug, exc,
            )

        # Strict-10 deck guarantee — surface every fetched article in the
        # /now deck, even when relevance scorer rejected it (e.g. stock-
        # price tickers, investor roadshows that score esg=0). Without
        # this stub-row pass, a tenant whose 9 fetched articles included
        # 7 REJECTED ends up with a 2-card deck. We write a minimal
        # article_pool + company_article_view row for any fetched article
        # that didn't make it through the full pipeline, tagged LOW band
        # so it sorts below the analyzed CRITICAL/HIGH/MEDIUM cards.
        # Clicking a stub card triggers on-demand enrichment.
        try:
            from engine.models import article_pool, company_article_view
            seeded = 0
            for art in fresh:
                if article_pool.get(art.id) is not None:
                    continue  # already in the pool — pipeline wrote it
                article_pool.upsert(
                    article_id=art.id,
                    url=art.url or "",
                    title=(art.title or "")[:512],
                    source=art.source,
                    published_at=art.published_at,
                    primary_industry=company_obj.industry or "Unknown",
                    material_industries=[company_obj.industry or "Unknown"],
                    primary_pillar=None,
                    primary_theme=None,
                    event_id=None,
                    event_polarity="neutral",
                    shared_analysis={
                        "stub": True,
                        "reason": "below_relevance_threshold_or_pipeline_skipped",
                    },
                )
                if company_article_view.get(art.id, canonical_slug) is None:
                    # Use band="REJECTED" + low score so analyzed articles
                    # sort above stubs in deck_for_company (CRITICAL→HIGH→
                    # MEDIUM→LOW→else, criticality_score DESC within each).
                    company_article_view.upsert(
                        article_id=art.id,
                        company_slug=canonical_slug,
                        personalised_analysis={"stub": True},
                        criticality_score=0.1,
                        criticality_band="REJECTED",
                    )
                seeded += 1
            if seeded:
                logger.info(
                    "[onboard %s] seeded %d stub rows for non-analyzed articles",
                    canonical_slug, seeded,
                )
        except Exception as exc:  # noqa: BLE001 — stub seeding is additive
            logger.warning(
                "[onboard %s] stub-row seeding failed (non-fatal): %s",
                canonical_slug, exc,
            )

        # Phase 22.1/22.2 — make the canonical's article_index rows
        # visible to a session bound to the alias slug (login-time
        # slug from the email domain stem, e.g. "puma" vs canonical
        # "puma-se"). `mirror_to_slug` is the explicit name used in
        # the Phase 22.2 plan; it delegates to `register_alias` since
        # `article_index.id` is the primary key and all read helpers
        # already route through `resolve_slug`. Without this call the
        # dashboard stays empty even when the analysis succeeded.
        if alias_slug:
            from engine.index import sqlite_index
            mirrored = sqlite_index.mirror_to_slug(canonical_slug, alias_slug)
            logger.info("[onboard %s] mirrored %d rows to alias=%s",
                        canonical_slug, mirrored, alias_slug)

        onboarding_status.mark_ready(canonical_slug,
                                     fetched=len(fresh),
                                     analysed=analysed,
                                     home_count=home_count)
        # Mirror canonical's final stats to the alias slug so the
        # frontend (which is polling the placeholder) shows the right
        # numbers + clears the stale error from any earlier failed run.
        if alias_slug:
            onboarding_status.mark_ready(alias_slug,
                                         fetched=len(fresh),
                                         analysed=analysed,
                                         home_count=home_count)
        logger.info("[onboard %s] done: %d/%d analysed (rejected %d), %d HOME",
                    canonical_slug, analysed, attempted, attempted - analysed, home_count)

        # Phase 28 — terminal SSE event. Emit on BOTH slugs so the
        # alias-listening frontend (login-time slug) also closes its
        # stream cleanly.
        done_payload = {
            "slug": canonical_slug,
            "ready_at": onboarding_status.get(canonical_slug).finished_at if onboarding_status.get(canonical_slug) else None,
            "fetched": len(fresh),
            "analysed": analysed,
            "home_count": home_count,
        }
        onboarding_events.emit_event(canonical_slug, "onboard_complete", done_payload)
        if alias_slug:
            onboarding_events.emit_event(alias_slug, "onboard_complete", done_payload)

        # Forum v1.1 — make sure the 5 welcome threads exist so the new
        # tenant doesn't land on an empty /forum page. Idempotent — if
        # another tenant already triggered the seed, this is a no-op.
        # Failure is non-fatal; onboarding completes regardless.
        try:
            from engine.models.forum_threads import seed_welcome_threads
            seed_welcome_threads()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[onboard] forum seed failed (non-fatal): %s", exc)
    except Exception as exc:
        logger.exception("[onboard] unexpected failure: %s", exc)
        tb = traceback.format_exc()[:500]
        # Mark failure on whichever slug is being polled. If the alias and
        # canonical both exist, mark both so neither shows stale state.
        onboarding_status.mark_failed(slug, f"{exc}\n{tb}")
        onboarding_events.emit_event(slug, "onboard_failed", {
            "slug": slug, "error": str(exc)[:300],
        })
        try:
            if 'canonical_slug' in dir() and canonical_slug != slug:
                onboarding_status.mark_failed(canonical_slug, f"{exc}\n{tb}")
                onboarding_events.emit_event(canonical_slug, "onboard_failed", {
                    "slug": canonical_slug, "error": str(exc)[:300],
                })
        except Exception:
            pass


@router.post("/onboard", status_code=202)
def onboard(
    body: OnboardRequest,
) -> dict[str, Any]:
    """Kick off the onboarding pipeline. Returns immediately with 202.

    Phase 16: accepts domain-only entry (no `name` required). The slug
    is derived from whichever input is present; the background task
    re-slugifies once yfinance returns the canonical company name, so
    a domain-only POST may produce a tighter slug than the placeholder
    returned here. Frontend should use the slug from the status endpoint
    rather than this 202 response for redirects.
    """
    from engine.ingestion.company_onboarder import _slugify, _domain_to_search_term
    if not (body.name or body.ticker_hint or body.domain):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of `name`, `ticker_hint`, or `domain`",
        )
    seed = body.name or _domain_to_search_term(body.domain or "") or body.ticker_hint or "pending"
    expected_slug = _slugify(seed)

    # Seed the status row up-front so pollers never race with the task start.
    onboarding_status.upsert(expected_slug, state="pending")

    # Hand off to the dedicated onboarding worker (separate Replit
    # workflow). The API process never runs the pipeline inline.
    enqueue_onboarding(
        slug=expected_slug,
        name=body.name,
        ticker_hint=body.ticker_hint,
        domain=body.domain,
        limit=body.limit,
    )

    return {
        "status": "queued",
        "slug": expected_slug,
        "poll_url": f"/api/admin/onboard/{expected_slug}/status",
    }


@router.get("/onboard/{slug}/status")
def onboard_status(slug: str) -> dict[str, Any]:
    """Return the current onboarding progress for a slug. 404 if never kicked off."""
    status = onboarding_status.get(slug)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no onboarding record for {slug}")
    return status.to_dict()
