"""Phase 56 — day-one feature completeness for a freshly onboarded tenant.

onboard_v3 now seeds the global welcome Forum threads and auto-subscribes the
onboarding user to the weekly brief (previously a fresh tenant got an empty
/forum and no email until the legacy/admin paths ran).
"""
from __future__ import annotations

import inspect


def test_seed_and_subscribe_helpers_importable():
    from engine.models.forum_threads import seed_welcome_threads
    from engine.models.newsletter_subscribers import subscribe
    # seed_welcome_threads is keyword-only with defaults -> callable with no args
    sig = inspect.signature(seed_welcome_threads)
    assert all(p.default is not inspect.Parameter.empty for p in sig.parameters.values())
    # subscribe(email, company_slug)
    assert list(inspect.signature(subscribe).parameters)[:2] == ["email", "company_slug"]


def test_onboard_v3_wires_day_one_features():
    import api.routes.onboard_v3 as ob  # must import cleanly (no syntax error)
    src = inspect.getsource(ob)
    assert "seed_welcome_threads()" in src, "onboard_v3 must seed the welcome forum threads"
    assert "from engine.models.newsletter_subscribers import subscribe" in src
    assert "subscribe(caller_email" in src, "onboard_v3 must auto-subscribe the onboarding user"
