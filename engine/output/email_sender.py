"""Phase 9 — Email sender (Resend) + name-from-email helper.

Two things:
  1. `name_from_email("ambalika.mehrotra@mintedit.com")` → "Ambalika"
     Used to personalise the newsletter greeting when a recipient clicks
     Share and enters just an email address.
  2. `send_email(to, subject, html_body)` — Resend SDK wrapper.
     Degrades gracefully: if `RESEND_API_KEY` is missing it logs the message
     and returns a preview result instead of raising. This keeps dev + CI
     paths working without forcing every dev to have a real key.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


_EMAIL_RE = re.compile(r"^([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")

# Common corporate suffixes / generic mailboxes we don't want to greet by name
_GENERIC_LOCAL_PARTS = {
    "info", "contact", "hello", "team", "support", "sales", "admin",
    "noreply", "no-reply", "postmaster", "webmaster", "help", "hi",
    "enquiries", "press", "media", "pr",
}

# Words we drop if they appear as separate tokens (company suffixes / department)
_NAME_TOKEN_BLOCKLIST = {
    "editor", "editorial", "bureau", "desk", "team", "group",
    "corp", "corporate", "inc", "ltd", "llc", "official",
}


# ---------------------------------------------------------------------------
# Name extraction
# ---------------------------------------------------------------------------


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def name_from_email(email: str) -> str | None:
    """Best-guess first name from the local part of an email.

    Rules:
      - `firstname.lastname@x.com`    → "Firstname"
      - `firstname_lastname@x.com`    → "Firstname"
      - `first-name@x.com`            → "First"
      - `f.lastname@x.com`            → None (initial is not a usable greeting)
      - `info@x.com` / common generic → None (don't greet bots by name)
      - `jsmith+tag@x.com`            → "Jsmith" (tag stripped)
      - `a.b@x.com`, `x@x.com`        → None (initials / single letter)

    Returns None when no reasonable first name can be extracted — caller
    should fall back to a neutral greeting ("Hi there," / "Hello,").
    """
    if not email:
        return None
    m = _EMAIL_RE.match(email.strip())
    if not m:
        return None
    local = m.group(1).lower()

    # Strip gmail-style +tag
    local = local.split("+", 1)[0]

    # Generic mailboxes — no personal greeting
    if local in _GENERIC_LOCAL_PARTS:
        return None

    # Split on common separators
    parts = re.split(r"[._\-]+", local)
    # Drop empty and blocked tokens
    parts = [p for p in parts if p and p not in _NAME_TOKEN_BLOCKLIST]

    if not parts:
        return None

    first = parts[0]
    # Single-letter first token → probably an initial; don't guess
    if len(first) <= 1:
        return None

    # Reject if it's clearly a generated alias (digits dominate)
    if sum(c.isdigit() for c in first) >= len(first) / 2:
        return None

    # Title-case: "ambalika" → "Ambalika"; handle irish/scottish "o'connor" style lightly
    return first.capitalize()


# ---------------------------------------------------------------------------
# Send result + sender
# ---------------------------------------------------------------------------


@dataclass
class SendResult:
    status: str  # "sent" | "preview" | "failed"
    recipient: str
    subject: str
    provider_id: str = ""  # Resend message id when sent
    error: str = ""
    # Phase 13 B3 — error taxonomy. Distinguishes transient retryable failures
    # (rate_limit, timeout) from permanent ones (auth, bad_request, unknown).
    # The share-flow API maps this to HTTP 503 + Retry-After for transient
    # classes so the UI can render an actionable retry banner instead of an
    # opaque "send failed".
    error_class: str = ""  # "" | "rate_limit" | "timeout" | "auth" | "bad_request" | "unknown"


def _classify_resend_error(exc: BaseException) -> str:
    """Map a Resend SDK exception → error_class string.

    Resend's Python SDK exposes a few specific exception classes, and a
    pile of HTTP-status-mapped subclasses. We probe by attribute name to
    stay loosely coupled across SDK versions; fall back to message text
    matching if neither hits.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate_limit" in name or "rate limit" in msg or "429" in msg or "too many requests" in msg:
        return "rate_limit"
    if "timeout" in name or "timeout" in msg or "connection" in name or "connection reset" in msg or "connection refused" in msg:
        return "timeout"
    if "authentication" in name or "unauthorized" in name or "401" in msg or "invalid api key" in msg:
        return "auth"
    if "validation" in name or "badrequest" in name or "400" in msg:
        return "bad_request"
    return "unknown"


def _default_from_address() -> str:
    """Prefer `SNOWKAP_FROM_ADDRESS` (Phase 10) or legacy `EMAIL_FROM`; fall
    back to the verified Snowkap sender on snowkap.co.in."""
    return (
        os.environ.get("SNOWKAP_FROM_ADDRESS")
        or os.environ.get("EMAIL_FROM")
        or "Snowkap ESG <newsletter@snowkap.co.in>"
    )


def send_email(
    to: str,
    subject: str,
    html_body: str,
    from_address: str | None = None,
    dry_run: bool = False,
    attachments: list[dict] | None = None,
) -> SendResult:
    """Send an HTML email via Resend. Returns SendResult.

    - `dry_run=True` → returns a "preview" result without hitting Resend. Use
      for testing / confirmation UX.
    - If `RESEND_API_KEY` is missing, logs the email and returns "preview"
      (NOT a failure — we don't want to block CI or dev environments).
    - If Resend raises, returns status="failed" with error message.
    """
    if not is_valid_email(to):
        return SendResult(status="failed", recipient=to, subject=subject,
                          error="invalid recipient email")

    if dry_run:
        logger.info("dry_run email — would send to %s, subject %r", to, subject[:60])
        return SendResult(status="preview", recipient=to, subject=subject)

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.warning("RESEND_API_KEY not set — returning preview (email NOT sent)")
        return SendResult(status="preview", recipient=to, subject=subject,
                          error="RESEND_API_KEY missing")

    try:
        import resend
    except ImportError:
        return SendResult(status="failed", recipient=to, subject=subject,
                          error="resend package not installed")

    resend.api_key = api_key
    from_addr = from_address or _default_from_address()

    payload: dict[str, Any] = {
        "from": from_addr,
        "to": to,
        "subject": subject,
        "html": html_body,
    }
    # Phase 11+ — inline attachments (e.g. the Snowkap logo as CID) so the
    # image renders in every email client including Outlook Desktop where
    # external <img src="https://..."> references are blocked by default.
    # Format per Resend docs: [{"content": <base64>, "filename": ..., "content_id": ...}]
    if attachments:
        payload["attachments"] = attachments

    try:
        response: Any = resend.Emails.send(payload)
    except Exception as exc:  # noqa: BLE001 — resend raises many types
        klass = _classify_resend_error(exc)
        logger.error(
            "resend send failed (class=%s): %s", klass, exc,
        )
        return SendResult(
            status="failed",
            recipient=to,
            subject=subject,
            error=str(exc)[:200],
            error_class=klass,
        )

    # Resend returns an `id` in the response dict
    msg_id = ""
    if isinstance(response, dict):
        msg_id = str(response.get("id", ""))
    logger.info("email sent via resend: to=%s msg_id=%s subject=%r",
                to, msg_id, subject[:60])
    return SendResult(status="sent", recipient=to, subject=subject, provider_id=msg_id)
