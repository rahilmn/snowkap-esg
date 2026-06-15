"""Phase 51.C — login trusts the /auth/resolve-domain slug so an existing
company is never re-onboarded.

The bug: signing in as review@adanipower.com spun up a duplicate "adanipower"
tenant and a billable background onboard (which then failed on a read-only data
dir) instead of landing on the existing adani-power deck. resolve-domain already
matches the company and returns its canonical slug; the LoginPage now echoes it
back and the server trusts it (after validating it's a real seeded company).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from api.routes import legacy_adapter as la

_FAKE_COMPANIES = [
    SimpleNamespace(slug="adani-power", name="Adani Power", domain="adanipower.com"),
    SimpleNamespace(slug="icici-bank", name="ICICI Bank", domain="icicibank.com"),
]


def test_valid_resolved_slug_short_circuits_onboarding() -> None:
    """A valid resolver slug is returned verbatim — no fuzzy match, no
    register_tenant, no onboarding — even when the domain looks brand-new."""
    with patch.object(la, "load_companies", return_value=_FAKE_COMPANIES), \
         patch.object(la, "_slug_for_company") as m_slug, \
         patch.object(la.tenant_registry, "register_tenant") as reg:
        slug = la._ensure_tenant_for_login(
            body_email="review@adanipower.com",
            body_domain="adanipower.com",
            body_company_name="Adani Power Limited",   # note: != seed name
            background=None,
            resolved_slug="adani-power",
        )
    assert slug == "adani-power"
    m_slug.assert_not_called()  # short-circuited before the fuzzy matcher
    reg.assert_not_called()     # and before any onboarding registration


def test_unknown_resolved_slug_is_ignored() -> None:
    """A bogus client-supplied slug is NOT trusted — login falls through to the
    normal name/domain matcher instead of stamping an arbitrary tenant."""
    with patch.object(la, "load_companies", return_value=_FAKE_COMPANIES), \
         patch.object(la, "_slug_for_company", return_value="icici-bank") as m_slug:
        slug = la._ensure_tenant_for_login(
            body_email="x@icicibank.com",
            body_domain="icicibank.com",
            body_company_name="ICICI Bank",
            background=None,
            resolved_slug="totally-bogus-not-a-company",
        )
    assert slug == "icici-bank"   # fell through to the real matcher …
    m_slug.assert_called_once()   # … i.e. the bogus slug was ignored


def test_no_resolved_slug_preserves_legacy_behaviour() -> None:
    """Back-compat: with no slug supplied (old clients) the path is unchanged —
    the fuzzy matcher still runs."""
    with patch.object(la, "load_companies", return_value=_FAKE_COMPANIES), \
         patch.object(la, "_slug_for_company", return_value="adani-power") as m_slug:
        slug = la._ensure_tenant_for_login(
            body_email="review@adanipower.com",
            body_domain="adanipower.com",
            body_company_name="Adani Power",
            background=None,
        )
    assert slug == "adani-power"
    m_slug.assert_called_once()
