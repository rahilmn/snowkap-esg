"""Phase C — MCP server for Snowkap-ESG.

Ports the Base Version pattern (`yoda/mcp/`): manifest-driven tool
catalog, introspection methods (list_tools, list_resources,
read_resource), per-tool input schemas + annotations.

Phase 1 ships **introspection + tool dispatch** only (no stdio/SSE
SDK bootstrap — same stance as Base Version's Phase M Wave 4 Task 4.1).
Real MCP-protocol stdio/SSE comes in a follow-up; today's API surface
(`GET /api/mcp/manifest`, `GET /api/mcp/tools`, `POST /api/mcp/invoke`)
gives the admin UI everything it needs.
"""
from engine.mcp.chat_integration import ToolInvocationResult, dispatch_tool
from engine.mcp.server import MCPServerHandle, build_server, load_manifest

__all__ = [
    "MCPServerHandle",
    "ToolInvocationResult",
    "build_server",
    "dispatch_tool",
    "load_manifest",
]
