"""W1.8 — /api/wiki/{search,related,page} endpoint tests."""
from __future__ import annotations

import os
from pathlib import Path

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


def test_search_endpoint_returns_shape_when_wiki_missing(client, headers):
    """Wiki root may not exist on a fresh checkout; endpoint should
    return a graceful empty shape, NOT a 500."""
    resp = client.get("/api/wiki/search", params={"q": "water"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "count" in body
    assert "hits" in body
    assert isinstance(body["hits"], list)


def test_search_requires_api_key(client):
    resp = client.get("/api/wiki/search", params={"q": "water"})
    assert resp.status_code != 200


def test_search_rejects_empty_query(client, headers):
    resp = client.get("/api/wiki/search", params={"q": ""}, headers=headers)
    # Pydantic min_length=1 catches this with a 422
    assert resp.status_code == 422


def test_related_endpoint_returns_404_for_unknown_page(client, headers):
    """If the wiki root exists but the path doesn't → 404. If wiki root
    doesn't exist either, the endpoint returns an empty backlinks list
    (graceful)."""
    resp = client.get(
        "/api/wiki/related", params={"path": "system/themes/nonexistent.md"},
        headers=headers,
    )
    # 404 (if wiki exists) or 200 with empty backlinks (if it doesn't)
    assert resp.status_code in (200, 404)


def test_page_endpoint_returns_404_for_unknown(client, headers):
    resp = client.get("/api/wiki/page", params={"path": "system/themes/never.md"}, headers=headers)
    assert resp.status_code == 404


def test_search_rejects_path_traversal(client, headers):
    """`?path=../../../etc/passwd` style attempts must be blocked."""
    resp = client.get(
        "/api/wiki/page", params={"path": "../../../etc/passwd"},
        headers=headers,
    )
    assert resp.status_code in (400, 404)
