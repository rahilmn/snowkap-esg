"""Phase 13 B7 — Email backend status endpoint.

Lets the frontend gate the Share button on whether the email backend is
actually live (Resend API key + verified sender configured) BEFORE the user
clicks. Prevents the demo-day failure mode where the share button is
visible per-permission but immediately returns "preview" because the
RESEND_API_KEY is missing.

Routes:
  GET /api/admin/email-config-status →
    {"enabled": bool, "sender": str, "reason"?: str}
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends

from api.auth import require_auth

router = APIRouter(tags=["admin-email"])


@dataclass
class _EmailStatus:
    enabled: bool
    sender: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "enabled": self.enabled,
            "sender": self.sender,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def _check() -> _EmailStatus:
    """Snapshot of the email backend's runtime configuration.

    enabled = True only when:
      - RESEND_API_KEY is non-empty AND not an obvious placeholder
      - SNOWKAP_FROM_ADDRESS (or fallback EMAIL_FROM) is configured
    Otherwise enabled=False with a `reason` explaining the gap.
    """
    api_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    sender = (
        os.environ.get("SNOWKAP_FROM_ADDRESS")
        or os.environ.get("EMAIL_FROM")
        or ""
    ).strip()

    if not api_key:
        return _EmailStatus(False, sender, reason="RESEND_API_KEY not set")
    # Cheap placeholder check (matches api.main._looks_like_placeholder semantics)
    low = api_key.lower()
    placeholder_markers = ("your_", "changeme", "placeholder", "replace_me", "todo_", "example_")
    if any(low.startswith(m) for m in placeholder_markers):
        return _EmailStatus(False, sender, reason="RESEND_API_KEY is a placeholder")
    if not sender:
        return _EmailStatus(False, "", reason="SNOWKAP_FROM_ADDRESS not set")
    if "@" not in sender:
        return _EmailStatus(False, sender, reason="SNOWKAP_FROM_ADDRESS missing @")
    return _EmailStatus(True, sender)


@router.get("/api/admin/email-config-status")
def email_config_status(_: None = Depends(require_auth)) -> dict[str, Any]:
    """Return whether the email backend is live + the verified sender.

    Frontend `useAuthStore` reads this on app boot so the Share button can
    gate on `canShareByEmail && emailConfigured`. Disabled state surfaces
    a tooltip explaining why ("Email service is not configured for this
    deployment — contact ops").
    """
    return _check().to_dict()
