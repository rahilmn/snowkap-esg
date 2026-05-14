"""Phase C — `/api/mcp/*` admin endpoint tests."""
from __future__ import annotations


def test_manifest_endpoint(client, headers):
    resp = client.get("/api/mcp/manifest", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mcp_server_name"] == "snowkap-esg"
    assert isinstance(body["exposed_tools"], list)


def test_tools_endpoint_returns_schemas(client, headers):
    resp = client.get("/api/mcp/tools", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    names = {t["name"] for t in body["tools"]}
    assert "wiki-search" in names
    assert "advisor-resolve" in names
    # Schema is hydrated per tool
    wiki = next(t for t in body["tools"] if t["name"] == "wiki-search")
    assert "inputSchema" in wiki
    assert "annotations" in wiki


def test_resources_endpoint_returns_list(client, headers):
    resp = client.get("/api/mcp/resources", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["resources"], list)


def test_invoke_requires_permission(client, headers):
    """Without manage_drip_campaigns, invoke is 403."""
    resp = client.post(
        "/api/mcp/invoke", headers=headers,
        json={"tool": "wiki-search", "payload": {"q": "water"}},
    )
    # Bearer auth not provided → permission check denies (legacy X-API-Key
    # doesn't grant the manage_drip_campaigns permission on its own).
    assert resp.status_code in (401, 403)
