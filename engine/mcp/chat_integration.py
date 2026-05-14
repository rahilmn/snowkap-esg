"""Phase C — Bridge between chat SSE and MCP tool dispatch.

When the LLM emits a `tool_call` in its response stream, the chat
route picks it up, hands it here, and we:

1. Look up the tool's annotations (read-only vs destructive).
2. If destructive AND no verbatim sign-off → return a `signoff_request`
   sentinel for the chat route to forward to the client as an SSE
   `signoff_request` event. The user must reply with the exact
   confirmation phrase before the tool actually runs.
3. Otherwise dispatch through the in-process MCP server handle.

This module never produces SSE bytes directly — the chat route
takes the structured result and shapes the SSE event. Keeps the
chat route thin and this module easy to unit-test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.mcp.server import MCPServerHandle
from engine.mcp.tool_metadata import is_destructive


_DEFAULT_SIGNOFF_PHRASE = "Confirm and execute"


@dataclass
class ToolInvocationResult:
    """Result of attempting to dispatch a tool through the MCP server.

    `state` semantics:
      * "ok"            — tool ran, `result` populated
      * "signoff_required" — destructive call without sign-off; nothing ran
      * "error"         — validation or handler failure; `error` populated
    """
    tool: str
    state: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    signoff_phrase: str | None = None
    annotations: dict[str, Any] | None = None


def dispatch_tool(
    handle: MCPServerHandle,
    *,
    tool: str,
    payload: dict[str, Any],
    user_signoff: str | None = None,
    signoff_phrase: str = _DEFAULT_SIGNOFF_PHRASE,
) -> ToolInvocationResult:
    """Dispatch a tool call with verbatim-sign-off enforcement.

    Args:
        handle: live MCP server handle (from `build_server()`).
        tool: tool name from the manifest.
        payload: validated input dict (the server re-validates).
        user_signoff: the user's most recent message text, used to
            check verbatim sign-off for destructive tools.
        signoff_phrase: the phrase the user must type back verbatim
            (case-sensitive) to authorise the destructive call.
    """
    annotations = {**(handle.list_tools() and {}), **{}}  # placeholder, replaced below
    # Walk the tool list once to find this tool's annotations
    tool_meta = next((t for t in handle.list_tools() if t.name == tool), None)
    annotations = tool_meta.annotations if tool_meta else {}

    if is_destructive(tool):
        if not user_signoff or signoff_phrase not in user_signoff:
            return ToolInvocationResult(
                tool=tool,
                state="signoff_required",
                signoff_phrase=signoff_phrase,
                annotations=annotations,
            )

    envelope = handle.invoke(tool, payload)
    if envelope.get("ok"):
        return ToolInvocationResult(
            tool=tool,
            state="ok",
            result=envelope.get("result"),
            annotations=annotations,
        )
    return ToolInvocationResult(
        tool=tool,
        state="error",
        error=envelope.get("error"),
        annotations=annotations,
    )
