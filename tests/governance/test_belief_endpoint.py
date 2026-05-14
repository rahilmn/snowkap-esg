"""L7 — `/api/companies/{slug}/beliefs` HTTP endpoint."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from engine.governance.belief_schema import RiskBandBelief
from engine.governance.company_agent import CompanyAgent


@pytest.fixture
def api_key():
    return os.environ.get("SNOWKAP_API_KEY", "test-api-key")


@pytest.fixture
def client(api_key, monkeypatch):
    monkeypatch.setenv("SNOWKAP_API_KEY", api_key)
    # Import after env override so api.auth reads the right key
    from api.main import app
    return TestClient(app)


@pytest.fixture
def headers(api_key):
    return {"X-API-Key": api_key}


def test_list_beliefs_returns_empty_for_unknown_tenant(client, headers):
    """L7 endpoint — unknown tenant returns empty list (not 404)."""
    resp = client.get("/api/companies/never-onboarded-xyz/beliefs", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant"] == "never-onboarded-xyz"
    assert body["beliefs"] == []
    assert body["count"] == 0


def test_list_beliefs_returns_persisted_state(client, headers, tmp_path, monkeypatch):
    """L7 endpoint — persisted beliefs surface through the API."""
    # Persist a belief snapshot at the default location
    # (the endpoint reads from the canonical data/agents path; we
    # write through CompanyAgent without overriding audit_dir so the
    # path used by load_from_disk matches).
    agent = CompanyAgent(tenant="api-test-tenant")
    agent.update_typed_belief(
        RiskBandBelief(topic="climate", band="HIGH", confidence_band="moderate"),
        rationale="r", actor="company_agent",
    )
    path = agent.dump_to_disk()
    try:
        resp = client.get("/api/companies/api-test-tenant/beliefs", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        names = [b["name"] for b in body["beliefs"]]
        assert "risk_band:climate" in names
    finally:
        # Clean up so re-runs aren't polluted
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass


def test_get_single_belief_returns_404_when_missing(client, headers):
    """L7 endpoint — unknown belief returns 404."""
    resp = client.get(
        "/api/companies/some-tenant/beliefs/nonexistent_belief",
        headers=headers,
    )
    assert resp.status_code == 404


def test_endpoint_requires_api_key(client):
    """L7 endpoint — no key → 401-ish (whatever require_api_key enforces)."""
    resp = client.get("/api/companies/x/beliefs")  # no headers
    # We don't pin the exact code (depends on api.auth implementation),
    # but it MUST NOT be 200.
    assert resp.status_code != 200
