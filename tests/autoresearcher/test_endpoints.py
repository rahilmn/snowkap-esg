"""Autoresearcher HTTP endpoint smoke tests."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_key():
    return os.environ.get("SNOWKAP_API_KEY", "test-api-key")


@pytest.fixture
def client(api_key, monkeypatch):
    monkeypatch.setenv("SNOWKAP_API_KEY", api_key)
    from api.main import app
    return TestClient(app)


@pytest.fixture
def headers(api_key):
    return {"X-API-Key": api_key}


def test_experiments_endpoint_returns_shape(client, headers):
    """The endpoint returns a graceful shape even with empty ledger."""
    resp = client.get("/api/autoresearcher/experiments?tier=system",
                      headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "system"
    assert "count" in body
    assert isinstance(body["experiments"], list)


def test_experiments_requires_api_key(client):
    resp = client.get("/api/autoresearcher/experiments")
    assert resp.status_code != 200


def test_experiments_rejects_unknown_tier(client, headers):
    resp = client.get("/api/autoresearcher/experiments?tier=galactic",
                      headers=headers)
    assert resp.status_code == 422


def test_leaderboard_endpoint_returns_shape(client, headers):
    resp = client.get("/api/autoresearcher/leaderboard?tier=system&top_n=10",
                      headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tier"] == "system"
    assert isinstance(body["entries"], list)


def test_leaderboard_caps_top_n(client, headers):
    resp = client.get("/api/autoresearcher/leaderboard?tier=system&top_n=1000",
                      headers=headers)
    assert resp.status_code == 422


def test_run_endpoint_requires_bearer_permission(client, headers):
    """POST /run requires manage_drip_campaigns bearer; api-key alone is not enough."""
    resp = client.post(
        "/api/autoresearcher/run",
        headers=headers,
        json={"tier": "system", "budget": 1},
    )
    # No bearer token → 401/403
    assert resp.status_code in (401, 403, 422)


def test_run_endpoint_validates_body(client, headers):
    resp = client.post(
        "/api/autoresearcher/run",
        headers=headers,
        json={"tier": "bogus", "budget": 5},
    )
    # bearer not present means 401/403; payload also invalid (422) — either is fine
    assert resp.status_code in (401, 403, 422)
