"""JWT + Magic Link authentication.

Per CLAUDE.md:
- No passwords, no OTP — magic link only
- JWT claims: {tenant_id, user_id, company_id, designation, permissions[], domain}
- Email domain must match company domain at login
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from backend.core.config import settings

# Blocked domains — personal email providers cannot be used for login
BLOCKED_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "mail.com", "protonmail.com", "zoho.com", "yandex.com",
    "live.com", "msn.com", "rediffmail.com",
})


def create_jwt_token(
    tenant_id: str,
    user_id: str,
    company_id: str,
    designation: str,
    permissions: list[str],
    domain: str,
) -> str:
    """Create JWT with full tenant context per CLAUDE.md spec."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "company_id": company_id,
        "designation": designation,
        "permissions": permissions,
        "domain": domain,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT token. Raises JWTError on failure."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def generate_magic_link_token() -> str:
    """Generate a cryptographically secure magic link token."""
    return secrets.token_urlsafe(48)


def is_corporate_domain(domain: str) -> bool:
    """Check that domain is not a personal email provider."""
    return domain.lower() not in BLOCKED_DOMAINS


def extract_domain_from_email(email: str) -> str:
    """Extract domain portion from email address."""
    return email.rsplit("@", 1)[-1].lower()


def validate_email_domain_match(email: str, company_domain: str) -> bool:
    """Per CLAUDE.md Rule #8: email domain must match company domain at login."""
    return extract_domain_from_email(email) == company_domain.lower()
