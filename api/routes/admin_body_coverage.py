"""Phase 36 — Per-tenant article-body coverage admin endpoint.

Operator dashboard surface for "how many articles per company have full
article body vs are still headline-only". Lets the operator answer
"is YES Bank getting body coverage today?" in one curl call instead of
walking the filesystem manually.

Routes:
  GET /api/admin/body-coverage →
    {
      "computed_at": <unix>,
      "last_retry_at": <unix or null>,
      "last_retry_result": {bodies_added, paywalled, files_checked, ...} or null,
      "slugs": [
        {
          "slug": "yes-bank",
          "total": 18,
          "with_body": 12,
          "headline_only": 6,
          "body_coverage_pct": 67,
          "top_failing_publishers": [{"source": "msn", "count": 4}, ...]
        },
        ...
      ]
    }

Result is cached in-process for 60s so a polling dashboard doesn't beat
on the filesystem every refresh.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from api.auth_context import get_bearer_claims
from engine.config import get_data_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin-body-coverage"])

_MIN_BODY_CHARS = 300

# Tiny in-process cache so dashboard polling at 1Hz doesn't melt the FS.
_CACHE_TTL_SECONDS = 60.0
_cache_value: dict[str, Any] | None = None
_cache_expires_at: float = 0.0


def _compute_coverage() -> dict[str, Any]:
    inputs_root = Path(get_data_path("inputs", "news"))
    slugs_out: list[dict[str, Any]] = []
    if inputs_root.exists():
        for slug_dir in sorted(p for p in inputs_root.iterdir() if p.is_dir()):
            total = 0
            with_body = 0
            failing: Counter[str] = Counter()
            for f in slug_dir.glob("*.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                total += 1
                content = (d.get("content") or "").strip()
                title = (d.get("title") or "").strip()
                is_grounded = (
                    len(content) >= _MIN_BODY_CHARS
                    and content != title
                    and len(content) > len(title) + 50
                )
                if is_grounded:
                    with_body += 1
                else:
                    failing[(d.get("source") or "unknown").lower()] += 1
            headline_only = total - with_body
            pct = round((with_body / total * 100), 1) if total else 0.0
            slugs_out.append({
                "slug": slug_dir.name,
                "total": total,
                "with_body": with_body,
                "headline_only": headline_only,
                "body_coverage_pct": pct,
                "top_failing_publishers": [
                    {"source": s, "count": c} for s, c in failing.most_common(3)
                ],
            })

    # Pull last retry-cron result from scheduler_state
    last_retry_at: float | None = None
    last_retry_result: dict[str, Any] | None = None
    try:
        from engine.models.scheduler_state import get_last_run
        row = get_last_run("full_text_retry")
        if row:
            last_retry_at = row["last_run_at"]
            last_retry_result = row["last_result"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("body-coverage: scheduler_state read failed: %s", exc)

    return {
        "computed_at": time.time(),
        "last_retry_at": last_retry_at,
        "last_retry_result": last_retry_result,
        "slugs": slugs_out,
    }


def get_body_coverage(force_refresh: bool = False) -> dict[str, Any]:
    """Module-level helper so /metrics can read the same payload without
    triggering a duplicate filesystem walk per scrape."""
    global _cache_value, _cache_expires_at
    now = time.time()
    if not force_refresh and _cache_value is not None and now < _cache_expires_at:
        return _cache_value
    _cache_value = _compute_coverage()
    _cache_expires_at = now + _CACHE_TTL_SECONDS
    return _cache_value


@router.get("/api/admin/body-coverage")
def body_coverage(
    claims: dict[str, Any] = Depends(get_bearer_claims),
) -> dict[str, Any]:
    """Return per-tenant article-body coverage stats.

    Gated by ``manage_drip_campaigns`` permission (the existing
    super-admin permission used by every other /api/admin/* route).
    Falls back to allowing any authenticated user when the permission
    isn't present on the JWT (legacy / dev tokens).
    """
    perms = set(claims.get("permissions") or [])
    if "manage_drip_campaigns" not in perms and "admin" not in perms:
        # Allow read in dev (JWT without the permission) so this endpoint
        # is usable in browser without the full admin auth flow. Tighten
        # in prod by setting `SNOWKAP_ENV=production` (the existing
        # production env-guard will check perm strictly).
        import os as _os
        if _os.environ.get("SNOWKAP_ENV") == "production":
            from fastapi import HTTPException
            raise HTTPException(
                status_code=403,
                detail="Admin permission required (manage_drip_campaigns)",
            )
    return get_body_coverage()
