"""Phase 51.C — super-admin on-demand deck refresh endpoint.

Lets an admin pull fresh news for ONE company synchronously, reusing the weekly
cron's in-process path (fetch_for_company -> build_company_deck) by canonical
slug — avoiding the onboard paths' worker-queue + duplicate-slug pitfalls.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

from api.main import app
from api.routes import legacy_adapter as la

_HDRS = {"X-API-Key": "test-api-key"}


def _fake_deck():
    return SimpleNamespace(
        critical_published=3, light_published=7,
        to_dict=lambda: {"critical_published": 3, "light_published": 7, "fetched": 10},
    )


def test_refresh_deck_super_admin_runs_in_process() -> None:
    company = SimpleNamespace(slug="adani-power", name="Adani Power", domain="adanipower.com")
    with patch.object(la, "is_snowkap_super_admin", return_value=True), \
         patch.object(la, "load_companies", return_value=[company]), \
         patch("engine.ingestion.news_fetcher.fetch_for_company", return_value=[]) as m_fetch, \
         patch("engine.analysis.deck_builder.build_company_deck", return_value=_fake_deck()) as m_deck:
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/refresh-deck", json={"slug": "adani-power"}, headers=_HDRS
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "adani-power"
    assert body["deck"]["critical_published"] == 3
    # Reused the exact cron path — fetched then built the deck once.
    m_fetch.assert_called_once()
    m_deck.assert_called_once()


def test_refresh_deck_rejects_non_super_admin() -> None:
    with patch.object(la, "is_snowkap_super_admin", return_value=False):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/refresh-deck", json={"slug": "adani-power"}, headers=_HDRS
            )
    assert r.status_code == 403


def test_refresh_deck_unknown_slug_404() -> None:
    with patch.object(la, "is_snowkap_super_admin", return_value=True), \
         patch.object(la, "load_companies", return_value=[]):
        with TestClient(app) as client:
            r = client.post(
                "/api/admin/refresh-deck", json={"slug": "no-such-co"}, headers=_HDRS
            )
    assert r.status_code == 404
