"""Phase 4 §6.4 — share endpoint accepts the new `role` field.

Today the role is captured for the audit / future per-role rendering;
the endpoint must NOT 422 on the new field for any well-formed
request, and must keep working when the field is OMITTED (back-compat
for callers that haven't been updated yet).
"""
from __future__ import annotations

import pytest

from api.routes.share import ShareRequest


def test_share_request_accepts_role_field():
    """Valid roles round-trip through the Pydantic model."""
    for role in ("cfo", "ceo", "analyst", "esg-analyst"):
        req = ShareRequest(
            recipient_email="cfo@example.com",
            sender_note="Heads-up on this one",
            role=role,
        )
        assert req.role == role


def test_share_request_role_is_optional_default_none():
    """Existing callers that omit `role` keep working byte-identical."""
    req = ShareRequest(recipient_email="cfo@example.com")
    assert req.role is None
    assert req.sender_note is None


def test_share_request_unknown_role_is_accepted():
    """The model accepts any string today (validation deferred to the
    body renderer when it grows per-role logic). This keeps frontend
    schema-flexibility — adding a new role like 'sustainability_manager'
    doesn't require a Pydantic enum bump."""
    req = ShareRequest(
        recipient_email="x@example.com",
        role="sustainability_manager",
    )
    assert req.role == "sustainability_manager"


def test_share_request_required_recipient_still_validated():
    """Adding `role` must not loosen any existing validation."""
    with pytest.raises(Exception):
        ShareRequest(role="cfo")  # missing recipient_email
