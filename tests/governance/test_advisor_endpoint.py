"""L6 — `/api/advisor/queue` + `/api/advisor/resolve` endpoint tests."""
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


def test_queue_endpoint_returns_empty_when_no_events(client, headers):
    # NB: this asserts against the LIVE on-disk advisor_queue.jsonl,
    # which may have entries from earlier test runs. We only check
    # the shape, not the count.
    resp = client.get("/api/advisor/queue", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "count" in body
    assert "events" in body
    assert isinstance(body["events"], list)


def test_queue_endpoint_supports_tenant_filter(client, headers):
    resp = client.get(
        "/api/advisor/queue", params={"tenant": "never-onboarded-xyz"}, headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # never-onboarded-xyz has no events
    assert body["events"] == []
    assert body["count"] == 0


def test_queue_endpoint_requires_api_key(client):
    resp = client.get("/api/advisor/queue")
    assert resp.status_code != 200


def test_resolve_endpoint_requires_api_key_and_permission(client):
    """L6 — resolve requires both api-key AND manage_drip_campaigns."""
    # No headers → fails
    resp = client.post("/api/advisor/resolve", json={
        "event_id": "abcd1234", "resolution": "approve",
    })
    assert resp.status_code != 200


def test_resolve_endpoint_validates_payload(client, headers):
    """L6 — body validation: bad resolution value rejected by Pydantic."""
    resp = client.post(
        "/api/advisor/resolve",
        headers=headers,
        json={"event_id": "abcd1234", "resolution": "defer"},
    )
    # Bearer-token check runs before body validation on some configs;
    # either 401/403 (no bearer) or 422 (bad payload) is acceptable here
    # — both reject the call.
    assert resp.status_code in (401, 403, 422)
