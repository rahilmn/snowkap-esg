"""Phase C — MCP server unit tests.

Covers:
  * manifest load + name/version
  * list_tools walks the manifest and looks up Pydantic schemas
  * list_resources / read_resource (with mock providers)
  * invoke() happy path (validated input → handler called)
  * invoke() returns input_validation_error for bad input
  * invoke() returns no_handler when tool not registered
  * invoke() returns handler_error when handler raises
  * smoke() reports the right counts
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.mcp import MCPServerHandle, load_manifest
from engine.mcp.server import ResourceDescriptor, build_server
from engine.mcp.tool_metadata import is_destructive


# ---------- manifest --------------------------------------------------------


def test_load_manifest_returns_dict():
    manifest = load_manifest()
    assert manifest["mcp_server_name"] == "snowkap-esg"
    assert manifest["schema_version"] == "v1"
    assert isinstance(manifest["exposed_tools"], list)
    assert len(manifest["exposed_tools"]) >= 10


def test_load_manifest_soft_fails_on_missing(tmp_path):
    missing = tmp_path / "nope.json"
    out = load_manifest(missing)
    assert out["exposed_tools"] == []


# ---------- introspection ---------------------------------------------------


def test_list_tools_returns_descriptors_with_schema():
    manifest = load_manifest()
    handle = MCPServerHandle(manifest)
    tools = handle.list_tools()
    assert len(tools) == len(manifest["exposed_tools"])
    by_name = {t.name: t for t in tools}
    # Pydantic schemas should be present for known tools
    wiki = by_name["wiki-search"]
    assert wiki.input_schema["type"] == "object"
    assert "q" in wiki.input_schema["properties"]
    assert wiki.annotations.get("readOnlyHint") is True


def test_list_tools_marks_destructive():
    manifest = load_manifest()
    handle = MCPServerHandle(manifest)
    by_name = {t.name: t for t in handle.list_tools()}
    assert by_name["advisor-resolve"].annotations.get("destructiveHint") is True
    assert is_destructive("advisor-resolve") is True
    assert is_destructive("wiki-search") is False


def test_list_resources_uses_provider():
    fake_resources = [
        ResourceDescriptor(uri="snowkap://test/x", name="x", description="d"),
    ]
    handle = MCPServerHandle(
        {"exposed_tools": []},
        resource_provider=lambda: fake_resources,
    )
    out = handle.list_resources()
    assert len(out) == 1
    assert out[0].uri == "snowkap://test/x"


def test_read_resource_uses_reader():
    handle = MCPServerHandle(
        {"exposed_tools": []},
        resource_reader=lambda uri: ("text/plain", "hello") if uri == "x" else None,
    )
    assert handle.read_resource("missing") is None
    out = handle.read_resource("x")
    assert out == {"uri": "x", "mimeType": "text/plain", "text": "hello"}


def test_smoke_reports_counts():
    handle = MCPServerHandle(
        {"exposed_tools": [{"name": "wiki-search", "description": "..."}]},
        resource_provider=lambda: [
            ResourceDescriptor(uri="snowkap://r/1", name="r1", description=""),
        ],
    )
    out = handle.smoke()
    assert out["tools"]["total"] == 1
    assert out["tools"]["names"] == ["wiki-search"]
    assert out["tools"]["unbound_handlers"] == ["wiki-search"]
    assert out["resources"]["total"] == 1


# ---------- invoke() --------------------------------------------------------


def test_invoke_unknown_tool_returns_error():
    handle = MCPServerHandle({"exposed_tools": []})
    out = handle.invoke("not-real", {})
    assert out["ok"] is False
    assert out["error"]["code"] == "unknown_tool"


def test_invoke_bad_input_returns_validation_error():
    manifest = load_manifest()
    handle = MCPServerHandle(manifest, handlers={"wiki-search": lambda p: {"hits": []}})
    # `q` is required + min_length=1
    out = handle.invoke("wiki-search", {"q": ""})
    assert out["ok"] is False
    assert out["error"]["code"] == "input_validation_error"


def test_invoke_no_handler_returns_error():
    manifest = load_manifest()
    handle = MCPServerHandle(manifest)  # no handlers
    out = handle.invoke("wiki-search", {"q": "water"})
    assert out["ok"] is False
    assert out["error"]["code"] == "no_handler"


def test_invoke_happy_path():
    captured: dict = {}

    def fake_handler(payload):
        captured.update(payload)
        return {"hits": ["stub"]}

    manifest = load_manifest()
    handle = MCPServerHandle(manifest, handlers={"wiki-search": fake_handler})
    out = handle.invoke("wiki-search", {"q": "water", "top_k": 3})
    assert out["ok"] is True
    assert out["result"] == {"hits": ["stub"]}
    assert captured["q"] == "water"
    assert captured["top_k"] == 3


def test_invoke_handler_error_is_surfaced():
    def boom(_payload):
        raise RuntimeError("kaboom")

    manifest = load_manifest()
    handle = MCPServerHandle(manifest, handlers={"wiki-search": boom})
    out = handle.invoke("wiki-search", {"q": "water"})
    assert out["ok"] is False
    assert out["error"]["code"] == "handler_error"
    assert "kaboom" in out["error"]["message"]


# ---------- build_server() factory ------------------------------------------


def test_build_server_loads_default_handlers(tmp_path):
    """Smoke — every tool in the manifest must have a handler bound."""
    handle = build_server(data_dir=tmp_path)
    smoke = handle.smoke()
    assert smoke["tools"]["unbound_handlers"] == [], (
        f"Tools without handlers: {smoke['tools']['unbound_handlers']}"
    )
    assert smoke["tools"]["total"] >= 10
