"""Bearer-token context helpers for the running api/ stack.

Phase 11A — Signed JWT verification (strict).

Tokens are minted by `legacy_adapter.auth_login` with `HS256` signatures
using the `JWT_SECRET` env var (PyJWT). `decode_bearer` verifies the
signature before trusting any claims — unsigned tokens are always
rejected (return `{}`).

This module provides:
  * `mint_bearer(claims, exp_days=7)` — sign + return token string for /login
  * `decode_bearer(authorization)` — verify signature + return claims dict
  * `get_bearer_claims()` — FastAPI dependency returning the dict
  * `require_bearer_permission(*perms)` — dependency factory (403 on miss)
  * `is_snowkap_super_admin(email)` — allowlist gate
  * `SUPER_ADMIN_PERMISSIONS` — list of perms granted to super-admins
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import jwt as _jwt
from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)


def _jwt_secret() -> str:
    """Read JWT_SECRET from env. Refusing to sign without one is explicit."""
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET env var is empty. Set it before minting / verifying tokens."
        )
    return secret


def mint_bearer(claims: dict[str, Any], exp_days: int = 7) -> str:
    """Sign a JWT with HS256 + JWT_SECRET. Adds `iat` and `exp` automatically.

    Returns a `<header>.<payload>.<signature>` string (no `Bearer ` prefix).
    """
    now = int(time.time())
    payload: dict[str, Any] = {**claims, "iat": now, "exp": now + (exp_days * 86400)}
    token = _jwt.encode(payload, _jwt_secret(), algorithm="HS256")
    # PyJWT ≥2 returns str; older returns bytes — normalise.
    return token if isinstance(token, str) else token.decode("utf-8")


def decode_bearer(authorization: str | None) -> dict[str, Any]:
    """Verify + decode a Bearer JWT. Returns {} on failure — never raises.

    Verification path:
      1. Parse `Bearer <token>` header.
      2. `jwt.decode` with HS256 + secret. Anything that isn't a valid
         HS256-signed token (unsigned, tampered, expired, malformed) returns
         {}. There is no compat fallback.
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

    try:
        claims = _jwt.decode(token, secret, algorithms=["HS256"])
        if isinstance(claims, dict):
            return claims
        return {}
    except _jwt.ExpiredSignatureError:
        logger.info("decode_bearer: token expired")
        return {}
    except _jwt.InvalidSignatureError:
        logger.info("decode_bearer: invalid signature, token rejected")
        return {}
    except _jwt.DecodeError:
        logger.info("decode_bearer: malformed token, rejected")
        return {}
    except Exception as exc:  # defensive — never crash auth path
        logger.warning("decode_bearer: jwt.decode raised %s: %s", type(exc).__name__, exc)
        return {}


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


# Phase 24.1 — the "All Companies" cross-tenant view (company_id=null) is
# strictly Snowkap Sales territory. Other Snowkap super-admins (ci@,
# newsletter@, etc.) keep their super-admin permissions for actions like
# onboarding and sharing, but the aggregated dashboard tab is reserved
# for the sales account because that's the only role that legitimately
# needs cross-tenant visibility for prospect-to-customer conversion.
#
# Tunable via SNOWKAP_SALES_ADMIN_EMAIL (default: sales@snowkap.co.in)
# so the same code path supports staging tenants and ops rotations
# without a redeploy.
_DEFAULT_SALES_ADMIN_EMAIL = "sales@snowkap.co.in"


def is_snowkap_sales_admin(email: str) -> bool:
    """Return True iff `email` matches the Snowkap Sales admin account.

    Stricter than ``is_snowkap_super_admin``. Only this email gets the
    cross-tenant aggregate view ("All Companies" tab). Every other user
    — even other Snowkap super-admins — sees only their bound tenant.
    """
    import os

    if not email:
        return False
    target = (
        os.environ.get("SNOWKAP_SALES_ADMIN_EMAIL", _DEFAULT_SALES_ADMIN_EMAIL)
        .strip()
        .lower()
    )
    return email.strip().lower() == target


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
