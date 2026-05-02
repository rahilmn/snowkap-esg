"""Phase 22.3 — Magic-link OTP storage + helpers.

Two-step authentication: `POST /auth/login` generates a 6-digit OTP,
sends it via Resend, and returns `{"step": "verify"}` (NO token).
`POST /auth/verify` accepts the OTP and issues the JWT.

When `RESEND_API_KEY` is unset (dev / CI), the OTP module falls back
to "preview mode": the code is logged + returned in the response so
local pytest + smoke scripts still work without depending on email.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# Reuse the same SQLite database as the article index so we don't grow
# the deployment footprint with a separate DB file. The file path is
# resolved lazily so a test can monkey-patch it before the first call.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "snowkap.db"

OTP_LENGTH = 6
OTP_TTL_SECONDS = 10 * 60  # 10 minutes
MAX_ATTEMPTS = 5  # before we burn the OTP and force a re-request


def _db_path() -> Path:
    return Path(os.environ.get("SNOWKAP_DB_PATH", str(_DEFAULT_DB_PATH)))


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    """Idempotent — safe to call on every request. Creates auth_otp."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_otp (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                expires_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )


def _generate_code() -> str:
    """Cryptographically random 6-digit numeric code (zero-padded)."""
    n = secrets.randbelow(10 ** OTP_LENGTH)
    return f"{n:0{OTP_LENGTH}d}"


def issue(email: str) -> tuple[str, float]:
    """Generate + persist a fresh OTP for ``email``.

    Returns ``(code, expires_at)``. Overwrites any in-flight code for
    the same email so the user can re-request without admin help.
    """
    ensure_schema()
    code = _generate_code()
    now = time.time()
    expires_at = now + OTP_TTL_SECONDS
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_otp (email, code, expires_at, attempts, created_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(email) DO UPDATE SET
                code = excluded.code,
                expires_at = excluded.expires_at,
                attempts = 0,
                created_at = excluded.created_at
            """,
            (email.lower().strip(), code, expires_at, now),
        )
    return code, expires_at


def verify(email: str, code: str) -> tuple[bool, str | None]:
    """Check ``code`` against the stored OTP for ``email``.

    Returns ``(ok, error)``. On success the OTP is consumed (deleted)
    so a code can't be replayed. On failure the attempt count is
    incremented; once we cross MAX_ATTEMPTS the row is wiped and the
    user must request a new code.
    """
    ensure_schema()
    em = email.lower().strip()
    cd = (code or "").strip()
    if len(cd) != OTP_LENGTH or not cd.isdigit():
        return False, "Code must be 6 digits."
    with _connect() as conn:
        row = conn.execute(
            "SELECT code, expires_at, attempts FROM auth_otp WHERE email = ?",
            (em,),
        ).fetchone()
        if row is None:
            return False, "No code on file. Request a new one."
        stored_code, expires_at, attempts = row
        if time.time() > float(expires_at):
            conn.execute("DELETE FROM auth_otp WHERE email = ?", (em,))
            return False, "Code expired. Request a new one."
        if attempts >= MAX_ATTEMPTS:
            conn.execute("DELETE FROM auth_otp WHERE email = ?", (em,))
            return False, "Too many attempts. Request a new code."
        # Constant-time compare — an attacker can't time-side-channel
        # a partial match.
        if not secrets.compare_digest(stored_code, cd):
            conn.execute(
                "UPDATE auth_otp SET attempts = attempts + 1 WHERE email = ?",
                (em,),
            )
            return False, "Incorrect code."
        # Success — burn the OTP.
        conn.execute("DELETE FROM auth_otp WHERE email = ?", (em,))
        return True, None


def is_email_otp_enabled() -> bool:
    """Phase 22.4 — OTP login disabled at user request.

    Originally this returned True iff `RESEND_API_KEY` was configured,
    forcing all logins through a 2-step email-code challenge. The
    feedback after the BASF/Lloyds walkthrough was that the extra
    friction wasn't worth it for the current allowlist of prospects,
    so this now returns False unconditionally — every login mints a
    JWT in one step (the legacy single-step path).

    The OTP module is left in place (DB schema, issue/verify, /auth/verify
    endpoint) so we can re-enable it later by flipping this flag without
    re-implementing the flow.
    """
    return False


def render_otp_email(code: str, name: str | None = None) -> tuple[str, str]:
    """Return ``(subject, html_body)`` for the OTP email."""
    greet = f"Hi {name.split(' ')[0]}," if name else "Hello,"
    subject = f"Your Snowkap login code: {code}"
    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 24px;">
      <p style="font-size: 14px; color: #444;">{greet}</p>
      <p style="font-size: 14px; color: #444;">
        Use the code below to finish signing in to Snowkap ESG Intelligence.
        It expires in 10 minutes.
      </p>
      <div style="margin: 24px 0; padding: 16px 24px; background: #f4f6f8; border-radius: 12px;
                  font-family: 'SF Mono', Menlo, monospace; font-size: 28px; letter-spacing: 0.4em;
                  text-align: center; font-weight: 700; color: #0e97e7;">
        {code}
      </div>
      <p style="font-size: 12px; color: #888;">
        If you didn't request this code, you can safely ignore this email.
      </p>
      <p style="font-size: 12px; color: #888; margin-top: 24px;">
        — Snowkap ESG
      </p>
    </div>
    """.strip()
    return subject, html
