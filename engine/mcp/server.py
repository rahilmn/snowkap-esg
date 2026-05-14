"""Phase C — MCP server handle for Snowkap-ESG.

Manifest-driven introspection + tool dispatch. Ports the *capability*
slice of Base Version's `yoda/mcp/server.py` without the protocol slice
(stdio/SSE SDK transport) — that's a follow-up.

Three responsibilities:
  1. **list_tools()** — walk the manifest, look up Pydantic input
     schemas + annotations per tool, return the MCP-spec-compatible
     descriptor list.
  2. **list_resources() / read_resource(uri)** — surface read-only
     resources (wiki pages, ontology TTL, audit logs, autoresearcher
     ledger) by URI so an MCP client can pull authoritative state.
  3. **invoke(tool_name, payload)** — validate input through the
     Pydantic model, dispatch to a registered handler, return a
     structured `{ok, result, error}` dict.

Audit / Toulmin / L2 4-tag discipline is enforced at the **handler**
level (each tool adapter), not here. The server is a thin router.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from pydantic import BaseModel, ValidationError

from engine.config import get_data_path
from engine.mcp.tool_metadata import (
    TOOL_METADATA,
    annotations_for,
    input_schema_for,
)


_THIS_DIR = Path(__file__).resolve().parent
_DEFAULT_MANIFEST_PATH = _THIS_DIR / "manifest.json"


# Handler signature: (validated_input_dict) -> JSON-serialisable result
ToolHandler = Callable[[dict[str, Any]], Any]


@dataclass
class ToolDescriptor:
    """MCP-spec-compatible tool descriptor."""
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": self.annotations,
        }


@dataclass
class ResourceDescriptor:
    """A surface-able MCP resource (read-only)."""
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Read the manifest JSON. Returns an empty manifest if missing.

    Empty manifest is a soft-fail so dev environments without a manifest
    still boot — `list_tools()` just returns the empty list.
    """
    p = path or _DEFAULT_MANIFEST_PATH
    if not p.exists():
        return {"exposed_tools": [], "schema_version": "v1"}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


class MCPServerHandle:
    """In-process handle for the Snowkap MCP catalog.

    Construct via `build_server()` to get a handle pre-loaded with
    the on-disk manifest + the default resource providers. For tests,
    construct directly with a custom manifest dict + empty providers.
    """

    def __init__(
        self,
        manifest: Mapping[str, Any],
        *,
        handlers: Mapping[str, ToolHandler] | None = None,
        resource_provider: Callable[[], Iterable[ResourceDescriptor]] | None = None,
        resource_reader: Callable[[str], tuple[str, str] | None] | None = None,
    ) -> None:
        self._manifest = dict(manifest)
        self._handlers: dict[str, ToolHandler] = dict(handlers or {})
        self._resource_provider = resource_provider
        self._resource_reader = resource_reader

    # ---------- introspection ------------------------------------------------

    @property
    def manifest(self) -> dict[str, Any]:
        return dict(self._manifest)

    @property
    def name(self) -> str:
        return str(self._manifest.get("mcp_server_name") or "snowkap-esg")

    @property
    def version(self) -> str:
        return str(self._manifest.get("mcp_server_version") or "0.0.0")

    def list_tools(self) -> list[ToolDescriptor]:
        """Walk the manifest, marrying it with per-tool Pydantic schemas."""
        tools: list[ToolDescriptor] = []
        for entry in self._manifest.get("exposed_tools", []):
            name = entry.get("name")
            if not name:
                continue
            tools.append(
                ToolDescriptor(
                    name=name,
                    description=str(entry.get("description") or ""),
                    input_schema=input_schema_for(name),
                    annotations=annotations_for(name),
                )
            )
        return tools

    def list_resources(self) -> list[ResourceDescriptor]:
        if self._resource_provider is None:
            return []
        return list(self._resource_provider())

    def read_resource(self, uri: str) -> dict[str, Any] | None:
        """Return `{uri, mimeType, text}` for a resource URI, or None."""
        if self._resource_reader is None:
            return None
        result = self._resource_reader(uri)
        if result is None:
            return None
        mime_type, text = result
        return {"uri": uri, "mimeType": mime_type, "text": text}

    # ---------- dispatch -----------------------------------------------------

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        """Late-binding registration (used by `build_server`)."""
        self._handlers[tool_name] = handler

    def has_handler(self, tool_name: str) -> bool:
        return tool_name in self._handlers

    def invoke(self, tool_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate input through the Pydantic model, dispatch to handler.

        Returns a uniform shape: `{ok, result?, error?}`. Never raises;
        validation + handler exceptions are converted to error dicts so
        an HTTP route can surface the failure without crashing.
        """
        payload = payload or {}
        entry = TOOL_METADATA.get(tool_name)
        if not entry:
            return {"ok": False, "error": {"code": "unknown_tool", "message": f"no tool '{tool_name}' registered"}}
        model: type[BaseModel] = entry["input_model"]
        try:
            validated = model.model_validate(payload)
        except ValidationError as exc:
            return {
                "ok": False,
                "error": {"code": "input_validation_error", "message": str(exc), "details": exc.errors()},
            }
        handler = self._handlers.get(tool_name)
        if handler is None:
            return {"ok": False, "error": {"code": "no_handler", "message": f"tool '{tool_name}' has no handler bound"}}
        try:
            result = handler(validated.model_dump())
        except Exception as exc:  # noqa: BLE001 — surface any handler error verbatim
            return {
                "ok": False,
                "error": {"code": "handler_error", "message": f"{type(exc).__name__}: {exc}"},
            }
        return {"ok": True, "result": result}

    # ---------- diagnostics --------------------------------------------------

    def smoke(self) -> dict[str, Any]:
        """Introspection snapshot (used by `scripts/run_mcp_server.py --smoke`)."""
        tools = self.list_tools()
        resources = self.list_resources()
        unbound = [t.name for t in tools if not self.has_handler(t.name)]
        return {
            "server": {"name": self.name, "version": self.version},
            "transport": self._manifest.get("transport", []),
            "differentiator_amplifications": self._manifest.get("differentiator_amplifications", []),
            "tools": {
                "total": len(tools),
                "names": [t.name for t in tools],
                "unbound_handlers": unbound,
            },
            "resources": {
                "total": len(resources),
                "uris": [r.uri for r in resources],
            },
        }


def build_server(
    manifest_path: Path | None = None,
    *,
    data_dir: Path | None = None,
) -> MCPServerHandle:
    """Production factory. Loads manifest + the default tool registry."""
    manifest = load_manifest(manifest_path)
    # Local import — avoids a circular dependency at module load time
    # (tools/ imports server.ToolDescriptor in some adapters' typing).
    from engine.mcp import resources as _resources
    from engine.mcp.tools import build_default_registry

    base_data = data_dir or get_data_path("")
    handle = MCPServerHandle(
        manifest,
        handlers={},
        resource_provider=lambda: _resources.list_default_resources(base_data),
        resource_reader=lambda uri: _resources.read_default_resource(uri, base_data),
    )
    for name, handler in build_default_registry(base_data).items():
        handle.register_handler(name, handler)
    return handle
