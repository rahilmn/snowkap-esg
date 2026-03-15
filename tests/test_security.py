"""Security tests — JWT, magic links, domain validation, permissions.

Covers:
- JWT creation/decoding round-trip
- Token expiry
- Domain blocklist enforcement
- Email-domain match validation (CLAUDE.md Rule #8)
- Permission and role mapping
"""

import pytest
from datetime import datetime, timedelta, timezone
from jose import jwt

from backend.core.config import settings
from backend.core.security import (
    BLOCKED_DOMAINS,
    create_jwt_token,
    decode_jwt_token,
    extract_domain_from_email,
    generate_magic_link_token,
    is_corporate_domain,
    validate_email_domain_match,
)
from backend.core.permissions import (
    DESIGNATION_ROLE_MAP,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_permissions_for_role,
    map_designation_to_role,
)


# --- JWT Tests ---

class TestJWT:
    def test_create_and_decode_roundtrip(self):
        token = create_jwt_token(
            tenant_id="t1", user_id="u1", company_id="c1",
            designation="Analyst", permissions=["view_dashboard"], domain="acme.com",
        )
        payload = decode_jwt_token(token)
        assert payload["sub"] == "u1"
        assert payload["tenant_id"] == "t1"
        assert payload["company_id"] == "c1"
        assert payload["designation"] == "Analyst"
        assert payload["permissions"] == ["view_dashboard"]
        assert payload["domain"] == "acme.com"

    def test_jwt_contains_iat_and_exp(self):
        token = create_jwt_token(
            tenant_id="t1", user_id="u1", company_id="c1",
            designation="CEO", permissions=[], domain="acme.com",
        )
        payload = decode_jwt_token(token)
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_expired_jwt_raises(self):
        # Create a token that's already expired
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "u1", "tenant_id": "t1", "company_id": "c1",
            "designation": "Analyst", "permissions": [], "domain": "acme.com",
            "iat": now - timedelta(hours=48),
            "exp": now - timedelta(hours=24),
        }
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        with pytest.raises(Exception):
            decode_jwt_token(expired_token)

    def test_tampered_jwt_raises(self):
        token = create_jwt_token(
            tenant_id="t1", user_id="u1", company_id="c1",
            designation="Analyst", permissions=[], domain="acme.com",
        )
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(Exception):
            decode_jwt_token(tampered)

    def test_wrong_secret_jwt_raises(self):
        payload = {
            "sub": "u1", "tenant_id": "t1", "company_id": "c1",
            "designation": "Analyst", "permissions": [], "domain": "acme.com",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        }
        bad_token = jwt.encode(payload, "wrong-secret-key", algorithm="HS256")
        with pytest.raises(Exception):
            decode_jwt_token(bad_token)


# --- Magic Link Tests ---

class TestMagicLink:
    def test_token_is_unique(self):
        tokens = {generate_magic_link_token() for _ in range(100)}
        assert len(tokens) == 100, "Magic link tokens must be unique"

    def test_token_length(self):
        token = generate_magic_link_token()
        assert len(token) >= 48, "Token must be at least 48 chars for security"


# --- Domain Validation Tests ---

class TestDomainValidation:
    @pytest.mark.parametrize("domain", list(BLOCKED_DOMAINS))
    def test_blocked_domains_rejected(self, domain: str):
        assert not is_corporate_domain(domain)

    @pytest.mark.parametrize("domain", ["mahindra.com", "tata.com", "infosys.com", "reliance.com"])
    def test_corporate_domains_accepted(self, domain: str):
        assert is_corporate_domain(domain)

    def test_case_insensitive(self):
        assert not is_corporate_domain("Gmail.com")
        assert not is_corporate_domain("GMAIL.COM")

    def test_extract_domain(self):
        assert extract_domain_from_email("user@mahindra.com") == "mahindra.com"
        assert extract_domain_from_email("User@ACME.COM") == "acme.com"

    def test_email_domain_match(self):
        assert validate_email_domain_match("user@mahindra.com", "mahindra.com")
        assert not validate_email_domain_match("user@other.com", "mahindra.com")

    def test_email_domain_match_case_insensitive(self):
        assert validate_email_domain_match("user@Mahindra.com", "mahindra.com")
        assert validate_email_domain_match("user@mahindra.com", "MAHINDRA.COM")


# --- RBAC Tests ---

class TestRBAC:
    def test_all_roles_have_permissions(self):
        for role in Role:
            perms = get_permissions_for_role(role)
            assert len(perms) > 0, f"Role {role} has no permissions"

    def test_member_has_minimal_permissions(self):
        perms = get_permissions_for_role(Role.MEMBER)
        assert Permission.VIEW_DASHBOARD in perms
        assert Permission.MANAGE_USERS not in perms
        assert Permission.PLATFORM_ADMIN not in perms

    def test_platform_admin_has_all_permissions(self):
        perms = get_permissions_for_role(Role.PLATFORM_ADMIN)
        for p in Permission:
            assert p in perms, f"Platform admin missing {p}"

    def test_analyst_cannot_manage_users(self):
        perms = get_permissions_for_role(Role.ANALYST)
        assert Permission.MANAGE_USERS not in perms
        assert Permission.PLATFORM_ADMIN not in perms

    def test_sustainability_manager_can_trigger_predictions(self):
        perms = get_permissions_for_role(Role.SUSTAINABILITY_MANAGER)
        assert Permission.TRIGGER_PREDICTIONS in perms
        assert Permission.MANAGE_RULES in perms

    def test_tenant_admin_can_manage_users(self):
        perms = get_permissions_for_role(Role.TENANT_ADMIN)
        assert Permission.MANAGE_USERS in perms
        assert Permission.MANAGE_TENANT in perms

    def test_executive_has_view_and_export(self):
        perms = get_permissions_for_role(Role.EXECUTIVE)
        assert Permission.VIEW_DASHBOARD in perms
        assert Permission.EXPORT_DATA in perms
        assert Permission.EDIT_ANALYSIS not in perms

    def test_unknown_role_falls_back_to_member(self):
        perms = get_permissions_for_role("nonexistent_role")
        assert perms == ROLE_PERMISSIONS[Role.MEMBER]


# --- Designation Mapping Tests ---

class TestDesignationMapping:
    @pytest.mark.parametrize("designation,expected", [
        ("CEO", Role.EXECUTIVE),
        ("ceo", Role.EXECUTIVE),
        ("CFO", Role.EXECUTIVE),
        ("Head of Sustainability", Role.SUSTAINABILITY_MANAGER),
        ("sustainability manager", Role.SUSTAINABILITY_MANAGER),
        ("ESG Analyst", Role.ANALYST),
        ("Analyst", Role.ANALYST),
        ("Consultant", Role.ANALYST),
    ])
    def test_known_designations(self, designation: str, expected: str):
        assert map_designation_to_role(designation) == expected

    def test_unknown_designation_defaults_to_member(self):
        assert map_designation_to_role("Intern") == Role.MEMBER
        assert map_designation_to_role("Random Title") == Role.MEMBER

    def test_whitespace_handling(self):
        assert map_designation_to_role("  CEO  ") == Role.EXECUTIVE
