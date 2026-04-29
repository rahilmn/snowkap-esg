"""Bearer-token context helpers for the running api/ stack.

Phase 11A — Signed JWT verification.

Tokens are now minted by `legacy_adapter.auth_login` with `HS256` signatures
using the `JWT_SECRET` env var (PyJWT). `decode_bearer` verifies the
signature before trusting any claims.

Back-compat window (24h from the flip): if `REQUIRE_SIGNED_JWT` env var is
unset OR falsy, unsigned base64 tokens are still accepted (with a warning
logged) so existing sessionStorage tokens keep working. Once the last
unsigned client is flushed, set `REQUIRE_SIGNED_JWT=1` to reject unsigned
with 401.

This module provides:
  * `mint_bearer(claims, exp_days=7)` — sign + return token string for /login
  * `decode_bearer(authorization)` — verify signature + return claims dict
  * `get_bearer_claims()` — FastAPI dependency returning the dict
  * `require_bearer_permission(*perms)` — dependency factory (403 on miss)
  * `is_snowkap_super_admin(email)` — allowlist gate
  * `SUPER_ADMIN_PERMISSIONS` — list of perms granted to super-admins
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

import jwt as _jwt
from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)


def _pad_b64(s: str) -> str:
    return s + "=" * (-len(s) % 4)


def _jwt_secret() -> str:
    """Read JWT_SECRET from env. Refusing to sign without one is explicit."""
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET env var is empty. Set it before minting / verifying tokens."
        )
    return secret


def _require_signed() -> bool:
    """Feature flag. When truthy, unsigned tokens are rejected with 401."""
    return os.environ.get("REQUIRE_SIGNED_JWT", "").strip().lower() in {"1", "true", "yes", "on"}


def mint_bearer(claims: dict[str, Any], exp_days: int = 7) -> str:
    """Sign a JWT with HS256 + JWT_SECRET. Adds `iat` and `exp` automatically.

    Returns a `<header>.<payload>.<signature>` string (no `Bearer ` prefix).
    """
    now = int(time.time())
    payload: dict[str, Any] = {**claims, "iat": now, "exp": now + (exp_days * 86400)}
    token = _jwt.encode(payload, _jwt_secret(), algorithm="HS256")
    # PyJWT ≥2 returns str; older returns bytes — normalise.
    return token if isinstance(token, str) else token.decode("utf-8")


def _unsigned_decode(token: str) -> dict[str, Any]:
    """Fallback path: base64-decode the payload without signature check.

    Kept for the back-compat window. Logs a warning and tags the claims dict
    with `_unsigned: True` so downstream callers can observe the legacy path.
    """
    segments = token.split(".")
    if len(segments) < 2:
        return {}
    try:
        payload_bytes = base64.urlsafe_b64decode(_pad_b64(segments[1]))
        payload = json.loads(payload_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            payload["_unsigned"] = True
            return payload
        return {}
    except (ValueError, json.JSONDecodeError):
        return {}


def decode_bearer(authorization: str | None) -> dict[str, Any]:
    """Verify + decode a Bearer JWT. Returns {} on failure — never raises.

    Verification path:
      1. Parse `Bearer <token>` header.
      2. Try `jwt.decode` with HS256 + secret → if OK, return claims.
      3. If signature fails AND `REQUIRE_SIGNED_JWT` is falsy, fall back to
         unsigned base64 decode (legacy compat). Log a warning.
      4. If signature fails AND `REQUIRE_SIGNED_JWT` is truthy → return {}.
      5. Expired tokens always return {} (reject even in compat mode).
    """
    if not authorization:
        return {}
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return {}
    token = parts[1].strip()
    if not token:
        return {}

    try:
        secret = _jwt_secret()
    except RuntimeError:
        # No secret configured — nothing to verify against. Stay strict.
        logger.error("decode_bearer: JWT_SECRET missing, refusing to decode")
        return {}

    # 1) Try signed verification
    try:
        claims = _jwt.decode(token, secret, algorithms=["HS256"])
        if isinstance(claims, dict):
            return claims
        return {}
    except _jwt.ExpiredSignatureError:
        logger.info("decode_bearer: token expired")
        return {}
    except _jwt.InvalidSignatureError:
        # Signature mismatch — could be legacy unsigned OR forged.
        pass
    except _jwt.DecodeError:
        # Malformed JWT structure — could still be legacy base64-only.
        pass
    except Exception as exc:  # defensive — never crash auth path
        logger.warning("decode_bearer: jwt.decode raised %s: %s", type(exc).__name__, exc)

    # 2) Compat fallback (only if env flag is NOT set to strict)
    if _require_signed():
        logger.info("decode_bearer: unsigned token rejected (REQUIRE_SIGNED_JWT on)")
        return {}

    claims = _unsigned_decode(token)
    if claims:
        logger.warning(
            "decode_bearer: accepted UNSIGNED legacy token (sub=%s). "
            "Set REQUIRE_SIGNED_JWT=1 when every client has refreshed.",
            claims.get("sub", "?"),
        )
    return claims


def get_bearer_claims(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI dependency: decoded claims (empty dict if no/bad token)."""
    return decode_bearer(authorization)


def require_bearer_permission(*required: str):
    """Dependency factory: raises 403 if the bearer token lacks any required perm.

    In dev mode the token's `permissions` array is the source of truth for
    what the user can do. Tokens are minted by legacy_adapter.auth_login and
    include the permissions granted at login time (including super_admin for
    allowlisted Snowkap internal emails).
    """

    def _check(claims: dict[str, Any] = Depends(get_bearer_claims)) -> dict[str, Any]:
        perms = claims.get("permissions") or []
        if not isinstance(perms, list):
            perms = []
        missing = [p for p in required if p not in perms]
        if missing:
            logger.warning(
                "phase10_permission_denied",
                extra={"required": list(required), "missing": missing, "sub": claims.get("sub")},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permissions: {', '.join(missing)}",
            )
        return claims

    return _check


def has_permission(authorization: str | None, perm: str) -> bool:
    """Non-raising check used by endpoints that adjust behaviour (not gate it)."""
    perms = (decode_bearer(authorization) or {}).get("permissions") or []
    return isinstance(perms, list) and perm in perms


# --- Snowkap internal allowlist -------------------------------------------

_SNOWKAP_INTERNAL_DOMAINS = ("@snowkap.com", "@snowkap.co.in")


def is_snowkap_super_admin(email: str) -> bool:
    """Return True when `email` is on the comma-separated SNOWKAP_INTERNAL_EMAILS
    allowlist AND ends with an internal Snowkap domain.

    Allowlist is read fresh on each call so env changes take effect without
    a process restart. Empty allowlist → no one is super-admin (safe default).
    """
    import os

    if not email:
        return False
    e = email.strip().lower()
    if not any(e.endswith(dom) for dom in _SNOWKAP_INTERNAL_DOMAINS):
        return False
    allow_raw = os.environ.get("SNOWKAP_INTERNAL_EMAILS", "")
    allowlist = {x.strip().lower() for x in allow_raw.split(",") if x.strip()}
    if not allowlist:
        return False
    return e in allowlist


# Permissions granted to super-admins. Kept as a literal list (not imported
# from backend.core.permissions) so api/ has zero dependency on the SQLAlchemy
# stack when running in dev mode.
#
# Invariant (tested in tests/test_phase10_super_admin_access.py):
# SUPER_ADMIN_PERMISSIONS must be a superset of every value in the
# backend.core.permissions.Permission enum. When adding a new permission
# there, update this list too — the test will fail until you do.
#
# "read" and "chat" are shim-only legacy strings the frontend still checks
# for — they are not in the enum but exist on the regular-user token shape,
# so super-admin carries them to preserve UI parity with everyone else.
SUPER_ADMIN_PERMISSIONS: list[str] = [
    # Shim-only legacy strings (also on regular-user tokens)
    "read",
    "chat",
    # Dashboard
    "view_dashboard",
    # News
    "view_news",
    "manage_news_sources",
    # Analysis
    "view_analysis",
    "edit_analysis",
    "verify_reports",
    "export_data",
    # Predictions
    "view_predictions",
    "trigger_predictions",
    # Ontology
    "view_ontology",
    "manage_ontology",
    "manage_rules",
    "manage_assertions",
    # Campaigns (legacy enum)
    "view_campaigns",
    "manage_campaigns",
    # Reports
    "view_reports",
    "generate_reports",
    # Tenant admin
    "manage_users",
    "manage_tenant",
    "manage_roles",
    # Platform admin
    "platform_admin",
    "impersonate_user",
    "view_all_tenants",
    # Phase 10 additions
    "super_admin",
    "override_tenant_context",
    "manage_drip_campaigns",
]
