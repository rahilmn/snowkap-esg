"""Authentication dependencies for the Snowkap ESG API.

Two modes:

- **Dev mode** (``SNOWKAP_API_KEY`` unset AND ``REQUIRE_SIGNED_JWT`` unset):
  every request passes. Local development ergonomic.
- **Prod mode** (``SNOWKAP_API_KEY`` OR ``REQUIRE_SIGNED_JWT`` set): request
  must carry one of
    * ``X-API-Key`` header matching the env var (machine-to-machine), OR
    * ``Authorization: Bearer <jwt>`` where the JWT is signed with
      ``JWT_SECRET`` — verified via ``api.auth_context.decode_bearer``.

``decode_bearer`` only accepts HS256-signed tokens; unsigned/legacy
``alg:none`` tokens are always rejected (Task #4). ``REQUIRE_SIGNED_JWT``
here is just the strict-mode toggle for whether ``require_auth`` enforces
authentication on every request — it no longer affects the decoder.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status

from api.auth_context import decode_bearer


def _strict_mode_enabled() -> bool:
    """True if either production signal is set."""
    api_key = os.environ.get("SNOWKAP_API_KEY", "").strip()
    require_jwt = os.environ.get("REQUIRE_SIGNED_JWT", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    return bool(api_key) or require_jwt


def require_auth(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Dual-mode auth: API key OR verified Bearer JWT.

    In dev mode (no SNOWKAP_API_KEY and no REQUIRE_SIGNED_JWT), every
    request passes — matches the historical shim behaviour. In prod
    mode, requests need either a valid API key OR a signed JWT.
    """
    if not _strict_mode_enabled():
        return  # dev mode — accept anything

    # Machine-to-machine: matching API key
    expected = os.environ.get("SNOWKAP_API_KEY", "").strip()
    if expected and x_api_key and x_api_key == expected:
        return

    # Human sessions: verified Bearer JWT
    if authorization:
        claims = decode_bearer(authorization)
        if claims:
            # decode_bearer vetoes tampered signatures, expired tokens,
            # and unsigned legacy tokens — only HS256-signed tokens reach
            # here with non-empty claims.
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing auth — expected X-API-Key or Authorization: Bearer <signed-jwt>",
        headers={"WWW-Authenticate": 'Bearer realm="snowkap"'},
    )


# Backwards-compat alias so existing companies.py / insights.py / ingest.py
# routers keep working without edit.
require_api_key = require_auth
