"""Phase C — MCP admin endpoints.

Three read-only GETs + one POST:

  * `GET /api/mcp/manifest` — raw manifest JSON
  * `GET /api/mcp/tools`     — list_tools() output with schemas
  * `GET /api/mcp/resources` — list_resources() output
  * `POST /api/mcp/invoke`   — invoke a tool by name (validation + dispatch)

The `invoke` endpoint is gated by the `manage_drip_campaigns`
permission AND, for destructive tools, requires the caller to send a
verbatim sign-off phrase in the body.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from api.auth_context import get_bearer_claims, require_bearer_permission
from engine.mcp import build_server, dispatch_tool


router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _handle(request: Request):
    """Cache an in-process MCP handle on the FastAPI app state."""
    handle = getattr(request.app.state, "_mcp_handle", None)
    if handle is None:
        handle = build_server()
        request.app.state._mcp_handle = handle
    return handle


@router.get("/manifest")
def get_manifest(request: Request, _claims=Depends(get_bearer_claims)):
    return _handle(request).manifest


@router.get("/tools")
def list_tools(request: Request, _claims=Depends(get_bearer_claims)):
    handle = _handle(request)
    return {
        "tools": [t.to_dict() for t in handle.list_tools()],
        "smoke": handle.smoke(),
    }


@router.get("/resources")
def list_resources(request: Request, _claims=Depends(get_bearer_claims)):
    handle = _handle(request)
    return {"resources": [r.to_dict() for r in handle.list_resources()]}


@router.post("/invoke")
def invoke(
    request: Request,
    body: dict[str, Any] = Body(...),
    _claims=Depends(require_bearer_permission("manage_drip_campaigns")),
):
    tool = body.get("tool")
    payload = body.get("payload") or {}
    signoff = body.get("signoff")
    if not isinstance(tool, str) or not tool.strip():
        raise HTTPException(status_code=422, detail="tool is required")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")
    handle = _handle(request)
    result = dispatch_tool(
        handle, tool=tool, payload=payload, user_signoff=signoff,
    )
    return {
        "tool": result.tool,
        "state": result.state,
        "result": result.result,
        "error": result.error,
        "signoff_phrase": result.signoff_phrase,
        "annotations": result.annotations,
    }
