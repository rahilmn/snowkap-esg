"""Phase 25 W6 — admin batch onboarding endpoint.

Single POST that accepts a HubSpot CSV upload and enqueues 17 (or
however many pass the filter) onboarding jobs through the existing
``engine.jobs.onboard_queue``. The endpoint returns the parsed roster
+ disambiguation flags immediately so the admin UI can render the per-row
status table; the actual ticker resolution + financial fetch + news
ingest happens asynchronously via the existing onboarding worker.

Endpoints:

  * ``POST /api/admin/onboard/batch`` — multipart CSV upload OR
    application/json body with explicit row list. Returns a batch
    summary + per-row roster.
  * ``GET  /api/admin/onboard/batch/preview`` — admin uploads CSV but
    DOES NOT enqueue (dry-run); returns roster + summary so the admin
    can review the 17 candidates before committing.

Both gated by ``manage_drip_campaigns`` (super-admin only).

Feature flag: ``SNOWKAP_BATCH_ONBOARD_ENABLED`` env var (default 1).
Set to 0 to disable the batch endpoint while keeping the
single-company ``/api/admin/onboard`` flow live (per Phase 25 Section
7.7 rollback strategy).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth import require_auth
from api.auth_context import require_bearer_permission

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/onboard/batch",
    tags=["admin-onboard-batch"],
    dependencies=[
        Depends(require_auth),
        Depends(require_bearer_permission("manage_drip_campaigns")),
    ],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BatchRosterEntry(BaseModel):
    """One row from the parsed CSV — surfaced to the admin UI for review."""
    record_id: str
    deal_name: str
    company_name: str
    slug: str
    deal_stage: str
    region: str
    headquarter_country: str
    amount_inr: float | None
    deal_owner: str
    needs_disambiguation: bool
    disambiguation_candidates: list[dict[str, Any]]


class BatchPreviewResponse(BaseModel):
    """Returned by both preview + commit — preview omits the job_ids list."""
    total_eligible: int
    won_count: int
    negotiation_count: int
    countries: list[str]
    auto_resolvable: int
    needs_review: int
    roster: list[BatchRosterEntry]


class BatchCommitResponse(BatchPreviewResponse):
    """Commit response also returns the enqueued job IDs so the admin UI
    can poll per-row status via the existing
    ``GET /api/admin/onboard/{slug}/status`` endpoint."""
    enqueued_job_ids: list[int]
    skipped_already_existing: list[str]


# ---------------------------------------------------------------------------
# Feature flag check
# ---------------------------------------------------------------------------


def _check_feature_flag() -> None:
    flag = os.environ.get("SNOWKAP_BATCH_ONBOARD_ENABLED", "1").strip().lower()
    if flag in {"0", "false", "no"}:
        raise HTTPException(
            status_code=503,
            detail="Batch onboarding disabled via SNOWKAP_BATCH_ONBOARD_ENABLED=0. "
                   "Use the single-company /api/admin/onboard endpoint instead.",
        )


# ---------------------------------------------------------------------------
# Shared CSV → roster + disambiguation pipeline
# ---------------------------------------------------------------------------


def _parse_uploaded_csv(file_content: bytes) -> list[Any]:
    """Write the upload to a temp file (csv module needs a path/file object),
    parse via the existing batch onboarder."""
    from engine.ingestion.csv_batch_onboarder import parse_csv

    # Write to a tmp file so parse_csv can re-open with the right encoding
    with tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, mode="wb",
    ) as tmp:
        tmp.write(file_content)
        tmp_path = Path(tmp.name)
    try:
        return parse_csv(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _enrich_with_disambiguation(roster: list[Any]) -> list[BatchRosterEntry]:
    """For each roster entry, query the disambiguator and stamp the
    candidates + needs_disambiguation flag onto the response shape."""
    from engine.ingestion.ticker_disambiguator import disambiguate

    enriched: list[BatchRosterEntry] = []
    for r in roster:
        needs_review, candidates = disambiguate(r.company_name)
        enriched.append(BatchRosterEntry(
            record_id=r.record_id,
            deal_name=r.deal_name,
            company_name=r.company_name,
            slug=r.slug,
            deal_stage=r.deal_stage,
            region=r.region,
            headquarter_country=r.headquarter_country,
            amount_inr=r.amount_inr,
            deal_owner=r.deal_owner,
            needs_disambiguation=needs_review,
            disambiguation_candidates=[c.to_dict() for c in candidates],
        ))
    return enriched


def _build_summary(
    enriched: list[BatchRosterEntry],
) -> dict[str, Any]:
    """Aggregate counts the UI shows above the per-row table."""
    won = [e for e in enriched if e.deal_stage == "Won"]
    negotiation = [e for e in enriched if e.deal_stage == "Negotiation"]
    countries: dict[str, int] = {}
    for e in enriched:
        countries[e.headquarter_country] = countries.get(e.headquarter_country, 0) + 1
    return {
        "total_eligible": len(enriched),
        "won_count": len(won),
        "negotiation_count": len(negotiation),
        "countries": [f"{c}:{n}" for c, n in sorted(countries.items(), key=lambda x: -x[1])],
        "auto_resolvable": sum(1 for e in enriched if not e.needs_disambiguation),
        "needs_review": sum(1 for e in enriched if e.needs_disambiguation),
    }


# ---------------------------------------------------------------------------
# POST /api/admin/onboard/batch/preview — dry-run, parse only
# ---------------------------------------------------------------------------


@router.post("/preview", response_model=BatchPreviewResponse)
async def preview_batch_onboard(
    csv_file: UploadFile = File(..., description="HubSpot deals CSV export"),
) -> BatchPreviewResponse:
    """Parse the uploaded CSV and return the proposed onboarding roster
    WITHOUT enqueueing anything. Used by the admin UI's first-step
    review modal so the operator can confirm the 17 candidates before
    committing them to the queue."""
    _check_feature_flag()
    try:
        content = await csv_file.read()
        roster = _parse_uploaded_csv(content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse failed: {exc}") from exc

    enriched = _enrich_with_disambiguation(roster)
    summary = _build_summary(enriched)
    return BatchPreviewResponse(roster=enriched, **summary)


# ---------------------------------------------------------------------------
# POST /api/admin/onboard/batch — commit (parse + enqueue)
# ---------------------------------------------------------------------------


@router.post("", response_model=BatchCommitResponse)
async def commit_batch_onboard(
    csv_file: UploadFile = File(..., description="HubSpot deals CSV export"),
    skip_existing: bool = True,
) -> BatchCommitResponse:
    """Parse the uploaded CSV AND enqueue each row through the existing
    onboarding pipeline. Returns the per-row roster + the enqueued job
    IDs for polling.

    ``skip_existing=True`` (default): rows whose slug already exists in
    ``config/companies.json`` are skipped to avoid clobbering the
    original 7 target companies' tenants. Set False to force re-enqueue
    (e.g. retry a failed batch run from scratch).
    """
    _check_feature_flag()
    try:
        content = await csv_file.read()
        roster = _parse_uploaded_csv(content)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"CSV parse failed: {exc}") from exc

    enriched = _enrich_with_disambiguation(roster)

    # Look up existing tenants to skip
    existing_slugs: set[str] = set()
    if skip_existing:
        try:
            from engine.config import load_companies
            existing_slugs = {c.slug for c in load_companies()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("commit_batch_onboard: load_companies failed (%s); "
                           "treating all as new", exc)

    enqueued_ids: list[int] = []
    skipped_existing: list[str] = []
    enqueue_failures: list[str] = []

    from engine.jobs.onboard_queue import enqueue as _enqueue

    for entry in enriched:
        if entry.slug in existing_slugs:
            skipped_existing.append(entry.slug)
            continue
        # Pick the highest-confidence candidate's ticker as the hint.
        # If the entry needs review, the ticker will be a placeholder
        # ("UNKNOWN" or "PRIVATE:...") — the worker handles those by
        # falling back to name-based yfinance search.
        candidates = entry.disambiguation_candidates
        ticker_hint = None
        if candidates:
            top = candidates[0]
            tk = str(top.get("ticker") or "")
            if tk and not tk.startswith("PRIVATE:") and tk != "UNKNOWN":
                ticker_hint = tk
        try:
            job_id = _enqueue(
                slug=entry.slug,
                name=entry.company_name,
                ticker_hint=ticker_hint,
                domain=None,  # CSV doesn't carry domains; resolver will guess
                item_limit=10,
            )
            enqueued_ids.append(job_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "commit_batch_onboard: enqueue failed for slug=%s name=%r: %s",
                entry.slug, entry.company_name, exc,
            )
            enqueue_failures.append(entry.slug)

    summary = _build_summary(enriched)
    logger.info(
        "commit_batch_onboard: enqueued=%d skipped_existing=%d failed=%d",
        len(enqueued_ids), len(skipped_existing), len(enqueue_failures),
    )
    return BatchCommitResponse(
        roster=enriched,
        enqueued_job_ids=enqueued_ids,
        skipped_already_existing=skipped_existing,
        **summary,
    )
