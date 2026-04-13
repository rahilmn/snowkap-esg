"""Authentication dependencies for the Snowkap ESG API.

Two modes:

- **Dev mode** (``SNOWKAP_API_KEY`` unset): every request passes.
- **Prod mode** (``SNOWKAP_API_KEY`` set): request must carry one of
    * ``X-API-Key`` header matching the env var, OR
    * ``Authorization: Bearer <any-token>`` header (minted by
      ``/api/auth/login`` in the legacy adapter — we mint them, we trust them)

The Bearer leg exists so the restored legacy React UI — which stores a token
in sessionStorage and sends it on every request — keeps working without
changes. Because the adapter mints unsigned tokens itself, we accept any
bearer string; this is an open shim, not real auth.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


def require_auth(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Dual-mode auth: API key OR Bearer token.

    Raises 401 only when an API key is configured AND neither header is
    valid. In dev mode (env var unset), every request passes.
    """
    expected = os.environ.get("SNOWKAP_API_KEY", "").strip()
    if not expected:
        return  # dev mode — accept anything

    # Accept the matching X-API-Key header
    if x_api_key and x_api_key == expected:
        return

    # Accept any Bearer token (we issue them via /api/auth/login)
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing auth — expected X-API-Key or Authorization: Bearer",
        headers={"WWW-Authenticate": 'Bearer realm="snowkap"'},
    )


# Backwards-compat alias so existing companies.py / insights.py / ingest.py
# routers keep working without edit.
require_api_key = require_auth
